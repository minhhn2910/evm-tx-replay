#!/usr/bin/env python3
"""Estimate gas saved by sharing static calls, storage accesses and hashes.

Reads opcode_summary/<block>/txs.jsonl and block.json. No RPC.
STATICCALL gas comes from block_result/<block>/<tx>/txCalls.json (fast mode):
the frame's gasUsed is the whole call. SLOAD, SSTORE, and KECCAK256 stay on
txs.jsonl, so those blocks do not need to be summarized again.

  STATICCALL: ideal cross-tx cache of (to, input, output). A later identical
  triple is a hit. The gas saved is that frame's gasUsed minus --cache-hit-cost.
  A hit does not descend into the frame.
  SLOAD/SSTORE: pure (contract, slot) counting. A repeat is a key seen earlier
  in the range. Same-tx warm means it was seen earlier in this transaction.
  SLOAD saves max(0, g - --warm-cost) on a range hit that is not same-tx warm.
  SSTORE saves max(0, g - --sstore-warm-cost) on every range hit.
  KECCAK256 saves max(0, g - --cache-hit-cost) when `output` was already seen.

Block gas is the sum of receipt gasUsed. --ignore-simple leaves out simple
transfers (the summary's `simple` flag, or gasUsed == 21000) so the savings
percentages use the same denominator as the opcode charts.

Example:
  python gas_sharing_analysis.py --summary-dir opcode_summary --start-block 24000000 --range 2 --ignore-simple -o gas_sharing.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from data_analysis import as_int, load_json

SIMPLE_GAS = 21000
DATA_OPS = {"SLOAD": "sload", "SSTORE": "sstore"}
CALL_OPS = {"CALL", "STATICCALL", "DELEGATECALL", "CALLCODE", "CREATE", "CREATE2"}


def pct_summary(blocks: dict[int, Counter], num: str, den: str) -> dict:
    """Per-block num/den as percent: mean, min, max, and the aggregate over all blocks."""
    rows = [(c[num], c[den]) for c in blocks.values() if c[den]]
    if not rows:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "aggregate": 0.0}
    pcts = [100.0 * n / d for n, d in rows]
    return {
        "mean": sum(pcts) / len(pcts),
        "min": min(pcts),
        "max": max(pcts),
        "aggregate": 100.0 * sum(n for n, _ in rows) / sum(d for _, d in rows),
    }


def fmt(summary: dict) -> str:
    return "  ".join(f"{k} {v:6.2f}%" for k, v in summary.items())


def parse_start_blocks(text: str) -> list[int]:
    starts = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        starts.append(int(part, 0) if part.startswith(("0x", "0X")) else int(part))
    if not starts:
        raise argparse.ArgumentTypeError("need at least one start block")
    return starts


def average_values(items):
    """Unweighted average of similarly shaped reports. Percent summaries keep min-of-min and max-of-max."""
    items = [item for item in items if item is not None]
    if not items:
        return None
    if all(isinstance(item, bool) for item in items):
        return items[0]
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in items):
        return sum(items) / len(items)
    if all(isinstance(item, str) for item in items):
        return items[0] if all(item == items[0] for item in items) else items
    if all(isinstance(item, dict) for item in items):
        keys = items[0].keys()
        if set(keys) == {"mean", "min", "max", "aggregate"}:
            return {
                "mean": sum(item["mean"] for item in items) / len(items),
                "min": min(item["min"] for item in items),
                "max": max(item["max"] for item in items),
                "aggregate": sum(item["aggregate"] for item in items) / len(items),
            }
        return {key: average_values([item[key] for item in items if key in item]) for key in keys}
    return items[0]


def seen_before():
    seen = set()

    def hit(key, output):
        found = (key, output) in seen
        seen.add((key, output))
        return found

    return hit


def load_txs(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_block(summary_dir: Path, block: int) -> dict | None:
    path = summary_dir / str(block) / "block.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def frame_gas(node: dict) -> int:
    return as_int(node.get("gasUsed"))


def is_static(node: dict) -> bool:
    return str(node.get("type") or "").upper() == "STATICCALL"


def account_tree(root: dict, stats: Counter) -> None:
    """Call gas is exclusive frame gas. Static gas is the inclusive gasUsed of an outermost STATICCALL."""

    def visit(node, is_root, in_static):
        children = node.get("calls") or []
        if not is_root:
            stats["call_gas"] += max(0, frame_gas(node) - sum(frame_gas(child) for child in children))
        if is_static(node):
            stats["static_calls"] += 1
            if not in_static:
                stats["static_gas"] += frame_gas(node)
        for child in children:
            visit(child, False, in_static or is_static(node))

    visit(root, True, False)


def cache_tree(node: dict, hit, stats: Counter, cache_hit_cost: int) -> None:
    """A hit saves the frame's whole gasUsed and does not walk its children."""
    if is_static(node) and not node.get("error"):
        key = ((node.get("to") or "").lower(), (node.get("input") or "0x").lower())
        if hit(key, (node.get("output") or "0x").lower()):
            stats["static_hits"] += 1
            stats["static_saved"] += max(0, frame_gas(node) - cache_hit_cost)
            return
    for child in node.get("calls") or []:
        cache_tree(child, hit, stats, cache_hit_cost)


def is_simple_tx(tx: dict) -> bool:
    """A simple transfer is marked by stage 1, or spends exactly 21000 gas."""
    if tx.get("simple"):
        return True
    return as_int(tx.get("gasUsed")) == SIMPLE_GAS


def account_calls(ops: list[dict], stats: Counter) -> bool:
    """Add call `direct` and outermost STATICCALL `inside`. True when a call has no depth."""
    stack: list[tuple[int, bool]] = []
    missing = False
    for op in ops:
        name = op.get("op")
        if name not in CALL_OPS:
            continue
        depth = op.get("d")
        if depth is None:
            missing = True
        else:
            while stack and stack[-1][0] >= depth:
                stack.pop()
        stats["call_gas"] += max(0, int(op.get("direct", op.get("g") or 0)))
        if name == "STATICCALL":
            stats["static_calls"] += 1
            if depth is None or not any(is_static for _, is_static in stack):
                stats["static_gas"] += max(0, int(op.get("inside", op.get("g") or 0)))
        if depth is not None:
            stack.append((depth, name == "STATICCALL"))
    return missing


def cache_calls(ops: list[dict], hit, stats: Counter, cache_hit_cost: int) -> bool:
    """A hit saves the call's inclusive `inside`. Nested static calls under that hit are skipped.

    Returns True when a hit has no `inside` field, so the saving cannot be the whole frame.
    """
    stack: list[tuple[int, bool]] = []
    missing_inside = False
    for op in ops:
        if op.get("op") not in CALL_OPS:
            continue
        depth = op.get("d")
        if depth is None:
            if op.get("op") == "STATICCALL" and not op.get("error"):
                is_hit = _count_static_hit(op, hit, stats, cache_hit_cost)
                missing_inside = (is_hit and "inside" not in op) or missing_inside
            continue
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if any(skipping for _, skipping in stack):
            continue
        if op.get("op") != "STATICCALL" or op.get("error"):
            stack.append((depth, False))
            continue
        is_hit = _count_static_hit(op, hit, stats, cache_hit_cost)
        missing_inside = is_hit and "inside" not in op or missing_inside
        stack.append((depth, is_hit))
    return missing_inside


def _count_static_hit(op: dict, hit, stats: Counter, cache_hit_cost: int) -> bool:
    """Return whether this static call is a cache hit. Saved gas is inclusive `inside`."""
    key = ((op.get("to") or "").lower(), (op.get("input") or "0x").lower())
    if not hit(key, (op.get("output") or "0x").lower()):
        return False
    stats["static_hits"] += 1
    if "inside" not in op:
        return True
    stats["static_saved"] += max(0, int(op["inside"]) - cache_hit_cost)
    return True


def scan_ops(
    ops: list[dict],
    seen_slots: set,
    seen_block: set,
    seen_hashes: set,
    stats: Counter,
    warm_cost: int,
    sstore_warm_cost: int,
    cache_hit_cost: int,
) -> None:
    """Count repeats of (contract, slot). SLOAD saves only when the key is cold in this tx."""
    seen_tx: set = set()
    for op in ops:
        name = op.get("op")
        cost = int(op.get("g") or 0)
        if name in DATA_OPS and "c" in op and "slot" in op:
            label = DATA_OPS[name]
            key = (str(op["c"]).lower(), as_int(op["slot"]))
            in_tx, in_block, in_range = key in seen_tx, key in seen_block, key in seen_slots
            stats[label] += 1
            stats[f"{label}_gas"] += cost
            stats["data_ops"] += 1
            stats["data_gas"] += cost
            stats[f"{label}_repeat"] += in_range
            stats[f"{label}_block_repeat"] += in_block
            stats[f"{label}_tx_warm"] += in_tx
            if in_range and not (label == "sload" and in_tx):
                stats[f"{label}_saving_ops"] += 1
                floor = warm_cost if label == "sload" else sstore_warm_cost
                stats[f"{label}_saved"] += max(0, cost - floor)
            seen_tx.add(key)
            seen_block.add(key)
            seen_slots.add(key)
        elif name == "KECCAK256" and "output" in op:
            output = as_int(op["output"])
            stats["hash_ops"] += 1
            stats["hash_gas"] += cost
            if output in seen_hashes:
                stats["hash_same"] += 1
                stats["hash_saved"] += max(0, cost - cache_hit_cost)
            seen_hashes.add(output)


def analyze_summary(
    summary_dir: Path,
    data_dir: Path,
    start_block: int,
    n_blocks: int,
    warm_cost: int,
    sstore_warm_cost: int,
    cache_hit_cost: int,
    ignore_simple: bool,
) -> tuple[dict, dict]:
    static_blocks: dict[int, Counter] = {}
    full_blocks: dict[int, Counter] = {}
    hit = seen_before()
    seen_slots: set = set()
    seen_hashes: set = set()
    missing_tree = 0
    block_stats = []

    for block in range(start_block, start_block + n_blocks):
        path = summary_dir / str(block) / "txs.jsonl"
        if not path.is_file():
            print(f"  skip missing summary {path}", file=sys.stderr)
            continue
        txs = load_txs(path)
        record = load_block(summary_dir, block)
        print(f"  block {block}: {len(txs)} txs")
        static_stats = static_blocks.setdefault(block, Counter())
        full_stats = full_blocks.setdefault(block, Counter())
        seen_block: set = set()
        jsonl_gas = 0
        for tx in txs:
            gas_used = as_int(tx.get("gasUsed"))
            jsonl_gas += gas_used
            simple = is_simple_tx(tx)
            if simple:
                static_stats["simple_gas"] += gas_used
                static_stats["simple_txs"] += 1
            if not (ignore_simple and simple):
                static_stats["block_gas"] += gas_used
                full_stats["block_gas"] += gas_used
            full_stats["txs"] += 1
            ops = tx.get("ops") or []
            root = load_json(data_dir / str(block) / tx["tx"] / "txCalls.json")
            if root:
                account_tree(root, static_stats)
                cache_tree(root, hit, static_stats, cache_hit_cost)
            else:
                missing_tree += 1
                account_calls(ops, static_stats)
                cache_calls(ops, hit, static_stats, cache_hit_cost)
            scan_ops(ops, seen_slots, seen_block, seen_hashes, full_stats, warm_cost, sstore_warm_cost, cache_hit_cost)
        if record:
            block_stats.append(record)
            if record.get("gasUsed") != jsonl_gas:
                print(
                    f"  warning: block {block} txs.jsonl gas {jsonl_gas} != block.json gasUsed {record.get('gasUsed')}",
                    file=sys.stderr,
                )

    if missing_tree:
        print(f"  warning: {missing_tree} txs have no txCalls.json in {data_dir}", file=sys.stderr)

    static_total = sum(static_blocks.values(), Counter())
    static = {
        "blocks": len(static_blocks),
        "ignore_simple": ignore_simple,
        "cache_hit_cost": cache_hit_cost,
        "static_calls": static_total["static_calls"],
        "hits": static_total["static_hits"],
        "saved_gas": static_total["static_saved"],
        "gas": {
            "block": static_total["block_gas"],
            "call": static_total["call_gas"],
            "static": static_total["static_gas"],
            "simple": static_total["simple_gas"],
        },
        "simple_txs": static_total["simple_txs"],
        "same_input_output_pct": pct_summary(static_blocks, "static_hits", "static_calls"),
        "share_of_block_gas_pct": {
            "call": pct_summary(static_blocks, "call_gas", "block_gas"),
            "static": pct_summary(static_blocks, "static_gas", "block_gas"),
        },
        "reduction_pct": {
            "call": pct_summary(static_blocks, "static_saved", "call_gas"),
            "static": pct_summary(static_blocks, "static_saved", "static_gas"),
            "block": pct_summary(static_blocks, "static_saved", "block_gas"),
        },
    }

    for stats in full_blocks.values():
        stats["data_repeat"] = sum(stats[f"{name}_repeat"] for name in DATA_OPS.values())
        stats["data_saved"] = sum(stats[f"{name}_saved"] for name in DATA_OPS.values())
    full_total = sum(full_blocks.values(), Counter())

    def op_stats(name: str) -> dict:
        return {
            "ops": full_total[name],
            "repeat_pct": pct_summary(full_blocks, f"{name}_repeat", name),
            "block_repeat_pct": pct_summary(full_blocks, f"{name}_block_repeat", name),
            "tx_warm_pct": pct_summary(full_blocks, f"{name}_tx_warm", name),
            "saving_ops": full_total[f"{name}_saving_ops"],
            "saved_gas": full_total[f"{name}_saved"],
            "saved_pct_block": pct_summary(full_blocks, f"{name}_saved", "block_gas"),
            "saved_pct_op": pct_summary(full_blocks, f"{name}_saved", f"{name}_gas"),
        }

    full = {
        "blocks": len(full_blocks),
        "txs": full_total["txs"],
        "ignore_simple": ignore_simple,
        "warm_cost": warm_cost,
        "sstore_warm_cost": sstore_warm_cost,
        "cache_hit_cost": cache_hit_cost,
        "totals": {key: full_total[key] for key in sorted(full_total)},
        "data_access_gas_share_pct": pct_summary(full_blocks, "data_gas", "block_gas"),
        "data_access": {
            "description": "pure (contract, slot) occurrence counting",
            "all": {
                "repeat_pct": pct_summary(full_blocks, "data_repeat", "data_ops"),
                "saved_gas": full_total["data_saved"],
                "saved_pct_block": pct_summary(full_blocks, "data_saved", "block_gas"),
                "saved_pct_op": pct_summary(full_blocks, "data_saved", "data_gas"),
            },
            **{name: op_stats(name) for name in DATA_OPS.values()},
        },
        "hash_gas_share_pct": pct_summary(full_blocks, "hash_gas", "block_gas"),
        "hash_same_output_pct": pct_summary(full_blocks, "hash_same", "hash_ops"),
        "hash_saved_pct": {
            "block": pct_summary(full_blocks, "hash_saved", "block_gas"),
            "hash": pct_summary(full_blocks, "hash_saved", "hash_gas"),
        },
    }
    summed = {
        "gasUsed": sum(row.get("gasUsed", 0) for row in block_stats),
        "txs": sum(row.get("txs", 0) for row in block_stats),
        "simpleTxs": sum(row.get("simpleTxs", 0) for row in block_stats),
        "simpleGas": sum(row.get("simpleGas", 0) for row in block_stats),
        "denomGas": sum(row.get("denomGas", 0) for row in block_stats),
    }
    return static, full, summed


def print_static(report: dict) -> None:
    gas_totals = report["gas"]
    denom = "block gas excluding simple transfers" if report.get("ignore_simple") else "block gas"
    print(
        f"  blocks {report['blocks']}  static calls {report['static_calls']}"
        f"  cache hit cost {report.get('cache_hit_cost', 3)}  denominator {denom}"
    )
    print(
        f"  gas: block {gas_totals['block']}  call {gas_totals['call']}  static {gas_totals['static']}"
        f"  simple {gas_totals.get('simple', 0)} ({report.get('simple_txs', 0)} txs)"
    )
    print(f"    call gas / block gas              {fmt(report['share_of_block_gas_pct']['call'])}")
    print(f"    static call gas / block gas       {fmt(report['share_of_block_gas_pct']['static'])}")
    print(f"  ideal cache: {report['hits']} hits, {report['saved_gas']} gas saved (frame gasUsed)")
    print(f"    same input+output / static calls  {fmt(report['same_input_output_pct'])}")
    for name, summary in report["reduction_pct"].items():
        print(f"    saved / {name:<6} gas               {fmt(summary)}")


def print_storage(report: dict) -> None:
    totals = report["totals"]
    access = report["data_access"]
    print(f"  blocks {report['blocks']}  txs {report['txs']}  block gas {totals.get('block_gas', 0)}")
    print(f"  SLOAD {totals.get('sload', 0)}  SSTORE {totals.get('sstore', 0)}  {access['description']}")
    print(f"    data access gas / block gas       {fmt(report['data_access_gas_share_pct'])}")
    for name in DATA_OPS.values():
        op = access[name]
        label = name.upper()
        print(f"    {label:<6} repeated in range         {fmt(op['repeat_pct'])}")
        print(f"    {label:<6} repeated in same block    {fmt(op['block_repeat_pct'])}")
        print(f"    {label:<6} warm in same tx           {fmt(op['tx_warm_pct'])}")
        print(f"    {label:<6} saved / block gas         {fmt(op['saved_pct_block'])}")
        print(f"    {label:<6} saved / own op gas        {fmt(op['saved_pct_op'])}")
        print(f"    {label:<6} {op['saving_ops']} saving ops, {op['saved_gas']} gas saved")
    print(f"    ALL    repeated in range         {fmt(access['all']['repeat_pct'])}")
    print(f"    ALL    saved / block gas         {fmt(access['all']['saved_pct_block'])}")
    print(
        f"  KECCAK256 {totals.get('hash_ops', 0)}  same output {totals.get('hash_same', 0)}"
        f"  saved {totals.get('hash_saved', 0)}  cache hit cost {report.get('cache_hit_cost', 3)}"
    )
    print(f"    hash gas / block gas              {fmt(report['hash_gas_share_pct'])}")
    print(f"    same output / KECCAK256           {fmt(report['hash_same_output_pct'])}")
    print(f"    hash saved / block gas            {fmt(report['hash_saved_pct']['block'])}")
    print(f"    hash saved / hash gas             {fmt(report['hash_saved_pct']['hash'])}")


def parse_args():
    parser = argparse.ArgumentParser(description="Estimate gas sharing for static calls, storage and hashing.")
    parser.add_argument("--start-block", type=parse_start_blocks, required=True, dest="start_blocks", help="First block, or a comma-separated list of interval starts")
    parser.add_argument("--range", dest="n_blocks", type=int, required=True, help="Number of consecutive blocks")
    parser.add_argument("--summary-dir", default="opcode_summary", help="Stage-1 root (block/txs.jsonl and block.json)")
    parser.add_argument("--data-dir", default="block_result", help="Fast-mode root (txCalls.json) for STATICCALL frame gas")
    parser.add_argument("--ignore-simple", action="store_true", help="Leave simple transfers (21000 gas) out of the block-gas denominator")
    parser.add_argument("--warm-cost", type=int, default=100, help="Gas charged for a shared SLOAD")
    parser.add_argument("--sstore-warm-cost", type=int, default=100, help="Gas charged for a cached SSTORE")
    parser.add_argument("--cache-hit-cost", type=int, default=3, help="Gas charged for a cached STATICCALL or KECCAK256")
    parser.add_argument("-o", "--output", help="Write the report as JSON")
    args = parser.parse_args()
    if args.n_blocks < 1:
        parser.error("--range must be >= 1")
    if args.warm_cost < 0 or args.sstore_warm_cost < 0 or args.cache_hit_cost < 0:
        parser.error("cache costs must be >= 0")
    return args


def analyze_interval(args, summary_dir: Path, start_block: int) -> dict:
    end_block = start_block + args.n_blocks - 1
    print(f"\n===== interval {start_block}..{end_block} ({args.n_blocks} blocks) =====")
    static, full, block = analyze_summary(
        summary_dir,
        Path(args.data_dir),
        start_block,
        args.n_blocks,
        args.warm_cost,
        args.sstore_warm_cost,
        args.cache_hit_cost,
        args.ignore_simple,
    )
    if block["txs"]:
        print(
            f"block.json: gasUsed {block['gasUsed']}  txs {block['txs']}"
            f"  simple {block['simpleTxs']} ({block['simpleGas']} gas)  denom {block['denomGas']}"
        )
    print("STATICCALL sharing")
    print_static(static)
    storage = full if full["blocks"] else None
    if storage:
        print("SLOAD/SSTORE and KECCAK256 sharing")
        print_storage(storage)
    return {
        "start_block": start_block,
        "end_block": end_block,
        "range": args.n_blocks,
        "ignore_simple": args.ignore_simple,
        "block": block,
        "static": static,
        "storage": storage,
    }


def print_average(average: dict) -> None:
    print(f"\n===== average of {average['intervals']} intervals =====")
    block = average.get("block") or {}
    if block.get("txs"):
        print(
            f"block.json (average): gasUsed {block['gasUsed']:.0f}  txs {block['txs']:.1f}"
            f"  simple {block['simpleTxs']:.1f}  denom {block['denomGas']:.0f}"
        )
    print_static(average["static"])
    if average.get("storage"):
        print_storage(average["storage"])


def main():
    args = parse_args()
    summary_dir = Path(args.summary_dir)
    if not summary_dir.is_dir():
        print(f"No such summary directory: {summary_dir}", file=sys.stderr)
        return 1
    ranges = [analyze_interval(args, summary_dir, start) for start in args.start_blocks]
    average = None
    if len(ranges) > 1:
        average = average_values(ranges)
        average["intervals"] = len(ranges)
        print_average(average)
    if args.output:
        report = {"start_blocks": args.start_blocks, "range": args.n_blocks, "ignore_simple": args.ignore_simple, "intervals": ranges, "average": average}
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
