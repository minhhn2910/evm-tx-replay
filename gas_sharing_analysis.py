#!/usr/bin/env python3
"""Estimate gas saved by sharing static calls, storage accesses and hashes.

Fast trace (block_result/<block>/<tx>/txCalls.json):
  STATICCALLs with the same input and output, and the gas they would save
  against call gas, static call gas and block gas. Two cache models:
    A  ideal cross-tx cache: (to, input, output) already seen in the range
    C  consecutive repeats: same output as the previous call with (to, input)

Full trace (raw_block/<block>/<tx>/txTraceEIP3155.json):
  SLOAD/SSTORE share of block gas, KECCAK256 with an already seen output and
  the saving from skipping repeated hashes (their full gasCost). Per SLOAD and
  SSTORE, two models of "repeated" vs "warm in the tx":
    count  repeat = (contract, slot) accessed earlier in the range,
           tx_warm = accessed earlier in the same tx
    evm    repeat = accessed earlier in the range or charged warm,
           tx_warm = charged warm by the EVM (gasCost, includes access lists)
  Saving = (repeat and not tx_warm) ops x (2100 - --warm-cost): every range
  repeat is charged warm instead of only the repeats inside each tx.

RPC (--endpoint): header gasUsed and tx count per block, the average tx gas,
and a check that the traced block gas equals the header.

Gas definitions:
  block gas        sum of root gasUsed of the block's txs (fast trace); equals
                   the receipts' gasUsed, i.e. after refunds
  call gas         sum of exclusive gas (gasUsed - children) of non-root frames;
                   includes precompile frames and CREATE code deposit, which the
                   EIP-3155 steps do not show
  static call gas  inclusive gasUsed of STATICCALLs not nested in a STATICCALL
  saved gas        inclusive gasUsed of a cache hit; its subtree is skipped

Limitations:
  - callTracer gasUsed excludes the caller-side call overhead (cold/warm
    account access, memory expansion), so static call savings are a lower bound.
  - Caches are oracles keyed on exact outputs: no state invalidation, and they
    are cold at --start-block, so longer ranges find more repeats.
  - Tx order inside a block is the collection mtime order.
  - A cold access is credited 2100 - warm_cost for SSTORE too; EIP-2929 would
    remove exactly the 2100 surcharge. Slots warmed in a reverted frame turn
    cold again, so a few credited accesses were seen earlier in the same tx.
  - EIP-2930 access lists pre-warm slots: the count model treats their first
    touch as cold and credits a saving the EVM already gives; the evm model
    does not.
  - Hashes inside a cached static call are counted by both the hash and the
    static call saving, so their sum is an upper bound.

Example:
  python gas_sharing_analysis.py --start-block 24000000 --range 2 -o gas_sharing.json
  python gas_sharing_analysis.py --start-block 24000000,24100000 --range 5 -o gas_sharing.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

from data_analysis import as_int, iter_txs, load_json
from utils.rpc import normalize_endpoint, rpc_call

SLOAD, SSTORE, KECCAK256 = 0x54, 0x55, 0x20
CREATE_OPS = {0xF0, 0xF5}
CALL_TO_OPS = {0xF1, 0xFA}  # CALL, STATICCALL: storage context becomes the target
CALL_SELF_OPS = {0xF2, 0xF4}  # CALLCODE, DELEGATECALL: storage context unchanged
COLD_COST = 2100
SSTORE_COLD = {2200, 5000, 22100}
ADDRESS_MASK = (1 << 160) - 1
DATA_OPS = {SLOAD: "sload", SSTORE: "sstore"}
MODELS = {
    "count": "pure (contract, slot) occurrence counting",
    "evm": "EVM warm status from gasCost, includes access-list pre-warming",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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


def gas(node: dict) -> int:
    return as_int(node.get("gasUsed"))


def is_static(node: dict) -> bool:
    return str(node.get("type") or "").upper() == "STATICCALL"


# ---------------------------------------------------------------------------
# Fast trace: STATICCALL sharing
# ---------------------------------------------------------------------------

def seen_before():
    seen = set()

    def hit(key, output):
        found = (key, output) in seen
        seen.add((key, output))
        return found

    return hit


def same_as_last():
    last = {}

    def hit(key, output):
        found = last.get(key) == output
        last[key] = output
        return found

    return hit


CASES = {"A": ("ideal cross-tx cache", seen_before), "C": ("consecutive repeats", same_as_last)}


def account_gas(root: dict, stats: Counter) -> None:
    stats["block_gas"] += gas(root)

    def visit(node, is_root, in_static):
        children = node.get("calls") or []
        if not is_root:
            stats["call_gas"] += max(0, gas(node) - sum(gas(c) for c in children))
        if is_static(node):
            stats["static_calls"] += 1
            if not in_static:
                stats["static_gas"] += gas(node)
        for child in children:
            visit(child, False, in_static or is_static(node))

    visit(root, True, False)


def cache_walk(node: dict, hit, stats: Counter, case: str) -> None:
    if is_static(node) and not node.get("error"):
        key = ((node.get("to") or "").lower(), (node.get("input") or "0x").lower())
        if hit(key, (node.get("output") or "0x").lower()):
            stats[f"{case}_hits"] += 1
            stats[f"{case}_saved"] += gas(node)
            return
    for child in node.get("calls") or []:
        cache_walk(child, hit, stats, case)


def analyze_static(data_dir: Path, start_block: int, n_blocks: int) -> tuple[dict, dict]:
    blocks: dict[int, Counter] = {}
    hits = {case: make() for case, (_, make) in CASES.items()}

    print("Fast trace: STATICCALL sharing")
    for block, tx_dir in iter_txs(data_dir, start_block, n_blocks):
        root = load_json(tx_dir / "txCalls.json")
        if not root:
            continue
        stats = blocks.setdefault(block, Counter())
        account_gas(root, stats)
        for case, hit in hits.items():
            cache_walk(root, hit, stats, case)

    total = sum(blocks.values(), Counter())
    report = {
        "blocks": len(blocks),
        "static_calls": total["static_calls"],
        "gas": {"block": total["block_gas"], "call": total["call_gas"], "static": total["static_gas"]},
        "share_of_block_gas_pct": {
            "call": pct_summary(blocks, "call_gas", "block_gas"),
            "static": pct_summary(blocks, "static_gas", "block_gas"),
        },
        "cases": {},
    }
    for case, (description, _) in CASES.items():
        report["cases"][case] = {
            "description": description,
            "hits": total[f"{case}_hits"],
            "same_input_output_pct": pct_summary(blocks, f"{case}_hits", "static_calls"),
            "saved_gas": total[f"{case}_saved"],
            "reduction_pct": {
                name: pct_summary(blocks, f"{case}_saved", f"{name}_gas") for name in ("call", "static", "block")
            },
        }
    return report, blocks


def print_static(report: dict) -> None:
    gas_totals = report["gas"]
    print(f"  blocks {report['blocks']}  static calls {report['static_calls']}")
    print(f"  gas: block {gas_totals['block']}  call {gas_totals['call']}  static {gas_totals['static']}")
    print(f"    call gas / block gas              {fmt(report['share_of_block_gas_pct']['call'])}")
    print(f"    static call gas / block gas       {fmt(report['share_of_block_gas_pct']['static'])}")
    for case, stats in report["cases"].items():
        print(f"  case {case} ({stats['description']}): {stats['hits']} hits, {stats['saved_gas']} gas saved")
        print(f"    same input+output / static calls  {fmt(stats['same_input_output_pct'])}")
        for name, summary in stats["reduction_pct"].items():
            print(f"    saved / {name:<6} gas               {fmt(summary)}")


# ---------------------------------------------------------------------------
# RPC: block headers
# ---------------------------------------------------------------------------

def check_blocks(endpoint: str, start_block: int, n_blocks: int, traced: dict[int, Counter]) -> dict | None:
    """Header gasUsed and tx count per block; flags blocks whose traced gas differs from the header."""
    rows = {}
    try:
        for block in range(start_block, start_block + n_blocks):
            header = rpc_call(endpoint, "eth_getBlockByNumber", [hex(block), False])
            rows[block] = {"gas_used": as_int(header["gasUsed"]), "txs": len(header["transactions"])}
    except Exception as error:  # noqa: BLE001 - any RPC failure just skips the check
        print(f"RPC block check skipped ({endpoint}): {error}", file=sys.stderr)
        return None

    print(f"RPC block headers ({endpoint})")
    for block, row in rows.items():
        row["avg_tx_gas"] = row["gas_used"] / row["txs"] if row["txs"] else 0.0
        row["traced_block_gas"] = traced.get(block, Counter())["block_gas"]
        mark = "" if row["traced_block_gas"] == row["gas_used"] else "  MISMATCH vs traced txs"
        print(
            f"  block {block}: gasUsed {row['gas_used']}  txs {row['txs']}"
            f"  avg tx gas {row['avg_tx_gas']:.0f}  traced {row['traced_block_gas']}{mark}"
        )
    gas_used = sum(r["gas_used"] for r in rows.values())
    txs = sum(r["txs"] for r in rows.values())
    overall = gas_used / txs if txs else 0.0
    print(f"  overall: gasUsed {gas_used}  txs {txs}  avg tx gas {overall:.0f}")
    return {"blocks": {str(b): r for b, r in rows.items()}, "gas_used": gas_used, "txs": txs, "avg_tx_gas": overall}


# ---------------------------------------------------------------------------
# Full trace: SLOAD/SSTORE and KECCAK256 sharing
# ---------------------------------------------------------------------------

def address(word) -> str:
    return f"0x{as_int(word) & ADDRESS_MASK:040x}"


def scan_trace(tx_dir: Path, root_to: str | None, seen_slots: set, seen_hashes: set, stats: Counter) -> None:
    """root_to is the callTracer root `to`: the tx target, or the created address for a creation tx.

    Per SLOAD/SSTORE, both models count `repeat` (seen before in the range),
    `tx_warm` (warm within the tx) and `saving_ops` (repeat but not tx_warm).
    """
    ctx = [address(root_to) if root_to else f"create:{tx_dir.name}"]
    creates = itertools.count()
    seen_tx: set = set()

    def handle(step, nxt):
        # cast emits negative gasCost on some ops right after a call returns
        op, cost, depth = as_int(step["op"]), int(str(step["gasCost"]), 0), step["depth"]
        stack = step.get("stack") or []
        del ctx[depth:]

        if op in DATA_OPS and stack:
            name = DATA_OPS[op]
            key = (ctx[-1], as_int(stack[-1]))
            in_tx, in_range = key in seen_tx, key in seen_slots
            evm_warm = cost != COLD_COST if op == SLOAD else cost not in SSTORE_COLD
            stats[name] += 1
            stats[f"{name}_gas"] += cost
            stats["data_ops"] += 1
            stats["data_gas"] += cost
            for model, (repeat, tx_warm) in (("count", (in_range, in_tx)), ("evm", (in_range or evm_warm, evm_warm))):
                stats[f"{name}_{model}_repeat"] += repeat
                stats[f"{name}_{model}_tx_warm"] += tx_warm
                stats[f"{name}_{model}_saving_ops"] += repeat and not tx_warm
            seen_tx.add(key)
            seen_slots.add(key)

        elif op == KECCAK256 and nxt and nxt.get("stack"):
            output = as_int(nxt["stack"][-1])
            stats["hash_ops"] += 1
            stats["hash_gas"] += cost
            if output in seen_hashes:
                stats["hash_same"] += 1
                stats["hash_saved"] += cost
            seen_hashes.add(output)

        if nxt and nxt["depth"] == depth + 1:
            if op in CALL_TO_OPS and len(stack) >= 2:
                ctx.append(address(stack[-2]))
            elif op in CALL_SELF_OPS:
                ctx.append(ctx[-1])
            elif op in CREATE_OPS:
                ctx.append(f"create:{tx_dir.name}:{next(creates)}")

    prev = None
    with (tx_dir / "txTraceEIP3155.json").open(encoding="utf-8") as trace:
        for line in trace:
            if not line.strip():
                continue
            step = json.loads(line)
            if prev is not None:
                handle(prev, step)
            prev = step
    if prev is not None:
        handle(prev, None)


def analyze_full_trace(
    trace_dir: Path, data_dir: Path, start_block: int, n_blocks: int, warm_cost: int
) -> tuple[dict, dict]:
    blocks: dict[int, Counter] = {}
    seen_slots: set = set()
    seen_hashes: set = set()

    print("Full trace: SLOAD/SSTORE and KECCAK256 sharing")
    for block, tx_dir in iter_txs(trace_dir, start_block, n_blocks):
        if not (tx_dir / "txTraceEIP3155.json").is_file():
            continue
        stats = blocks.setdefault(block, Counter())
        root = load_json(data_dir / str(block) / tx_dir.name / "txCalls.json")
        if root:
            stats["block_gas"] += gas(root)
        else:
            stats["missing_block_gas"] += 1
        stats["txs"] += 1
        scan_trace(tx_dir, (root or {}).get("to"), seen_slots, seen_hashes, stats)

    for stats in blocks.values():
        for model in MODELS:
            for name in DATA_OPS.values():
                stats[f"{name}_{model}_saved"] = stats[f"{name}_{model}_saving_ops"] * (COLD_COST - warm_cost)
            stats[f"data_{model}_repeat"] = sum(stats[f"{name}_{model}_repeat"] for name in DATA_OPS.values())
            stats[f"data_{model}_saved"] = sum(stats[f"{name}_{model}_saved"] for name in DATA_OPS.values())

    total = sum(blocks.values(), Counter())
    if total["missing_block_gas"]:
        print(f"  warning: {total['missing_block_gas']} txs have no txCalls.json in {data_dir}", file=sys.stderr)

    def op_stats(name: str, model: str) -> dict:
        prefix = f"{name}_{model}"
        return {
            "ops": total[name],
            "repeat_pct": pct_summary(blocks, f"{prefix}_repeat", name),
            "tx_warm_pct": pct_summary(blocks, f"{prefix}_tx_warm", name),
            "saving_ops": total[f"{prefix}_saving_ops"],
            "saved_gas": total[f"{prefix}_saved"],
            "saved_pct_block": pct_summary(blocks, f"{prefix}_saved", "block_gas"),
            "saved_pct_op": pct_summary(blocks, f"{prefix}_saved", f"{name}_gas"),
        }

    report = {
        "blocks": len(blocks),
        "txs": total["txs"],
        "warm_cost": warm_cost,
        "totals": {k: total[k] for k in sorted(total)},
        "data_access_gas_share_pct": pct_summary(blocks, "data_gas", "block_gas"),
        "data_access": {
            model: {
                "description": description,
                "all": {
                    "repeat_pct": pct_summary(blocks, f"data_{model}_repeat", "data_ops"),
                    "saved_gas": total[f"data_{model}_saved"],
                    "saved_pct_block": pct_summary(blocks, f"data_{model}_saved", "block_gas"),
                    "saved_pct_op": pct_summary(blocks, f"data_{model}_saved", "data_gas"),
                },
                **{name: op_stats(name, model) for name in DATA_OPS.values()},
            }
            for model, description in MODELS.items()
        },
        "hash_gas_share_pct": pct_summary(blocks, "hash_gas", "block_gas"),
        "hash_same_output_pct": pct_summary(blocks, "hash_same", "hash_ops"),
        "hash_saved_pct": {
            "block": pct_summary(blocks, "hash_saved", "block_gas"),
            "hash": pct_summary(blocks, "hash_saved", "hash_gas"),
        },
    }
    return report, blocks


def print_full_trace(report: dict) -> None:
    t = report["totals"]
    print(f"  blocks {report['blocks']}  txs {report['txs']}  block gas {t.get('block_gas', 0)}")
    print(f"  SLOAD {t.get('sload', 0)}  SSTORE {t.get('sstore', 0)}  warm cost {report['warm_cost']}")
    print(f"    data access gas / block gas       {fmt(report['data_access_gas_share_pct'])}")
    for model, stats in report["data_access"].items():
        print(f"  model {model}: {stats['description']}")
        for name in DATA_OPS.values():
            op = stats[name]
            label = name.upper()
            print(f"    {label:<6} repeated in range         {fmt(op['repeat_pct'])}")
            print(f"    {label:<6} warm in same tx           {fmt(op['tx_warm_pct'])}")
            print(f"    {label:<6} saved / block gas         {fmt(op['saved_pct_block'])}")
            print(f"    {label:<6} saved / own op gas        {fmt(op['saved_pct_op'])}")
            print(f"    {label:<6} {op['saving_ops']} saving ops, {op['saved_gas']} gas saved")
        print(f"    ALL    repeated in range         {fmt(stats['all']['repeat_pct'])}")
        print(f"    ALL    saved / block gas         {fmt(stats['all']['saved_pct_block'])}")
    print(f"  KECCAK256 {t.get('hash_ops', 0)}  same output {t.get('hash_same', 0)}  saved {t.get('hash_saved', 0)}")
    print(f"    hash gas / block gas              {fmt(report['hash_gas_share_pct'])}")
    print(f"    same output / KECCAK256           {fmt(report['hash_same_output_pct'])}")
    print(f"    hash saved / block gas            {fmt(report['hash_saved_pct']['block'])}")
    print(f"    hash saved / hash gas             {fmt(report['hash_saved_pct']['hash'])}")


# ---------------------------------------------------------------------------
# Paper-style summary
# ---------------------------------------------------------------------------

def print_summary(static: dict, full: dict | None) -> None:
    na = "n/a (needs full EIP-3155 trace)"
    static_a = static["cases"]["A"]
    static_same = f"{static_a['same_input_output_pct']['aggregate']:.2f}%"
    static_saved = static_a["reduction_pct"]["block"]["mean"]

    if full:
        share = full["data_access_gas_share_pct"]
        data_share = f"{share['mean']:.2f}% (min {share['min']:.2f}%)"
        same_slot = f"{full['data_access']['count']['all']['repeat_pct']['max']:.2f}%"
        hash_same = f"{full['hash_same_output_pct']['aggregate']:.2f}%"
        saved = full["data_access"]["evm"]["all"]["saved_pct_block"]
        counted = full["data_access"]["count"]["all"]["saved_pct_block"]
        data_saved = (
            f"{saved['max']:.2f}% ({saved['mean']:.2f}% on average;"
            f" pure counting {counted['max']:.2f}% / {counted['mean']:.2f}%)"
        )
        hash_saved = full["hash_saved_pct"]["block"]["mean"]
        shared = (
            f"{hash_saved + static_saved:.2f}% (hashing {hash_saved:.2f}% + static calls {static_saved:.2f}%,"
            " an upper bound since hashes inside cached static calls count twice)"
        )
    else:
        data_share = same_slot = hash_same = data_saved = na
        shared = f"{static_saved:.2f}% from static calls; hashing {na}"

    print("\nSummary")
    print(
        f"  We find that data access accounts for more than {data_share} of total gas per block,"
        f" and up to {same_slot} of data access operations involve the same addresses."
        f" Our analysis also identifies {hash_same} of hashing and {static_same} of static call"
        " operations having the same input and output, making them amenable to gas sharing."
        " We evaluate the amount of potential gas saving under a real Ethereum workload and an"
        " ideal cross-transaction cache. The results show that sharing data access operations"
        f" can achieve up to {data_saved} gas reduction, while sharing hashing and static call"
        f" operations can lead to {shared} reduction."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Estimate gas sharing for static calls, storage and hashing.")
    parser.add_argument(
        "--start-block",
        type=parse_start_blocks,
        required=True,
        dest="start_blocks",
        help="First block, or a comma-separated list of interval starts (each uses --range blocks)",
    )
    parser.add_argument("--range", dest="n_blocks", type=int, required=True, help="Number of consecutive blocks")
    parser.add_argument("--data-dir", default="block_result", help="Fast trace root (txCalls.json)")
    parser.add_argument("--trace-dir", default="raw_block", help="Full trace root (txTraceEIP3155.json)")
    parser.add_argument("--warm-cost", type=int, default=100, help="Gas of a shared (warm) storage access")
    parser.add_argument(
        "--endpoint", default="http://localhost:8545", help="RPC for block headers; empty string skips the check"
    )
    parser.add_argument("-o", "--output", help="Write the report as JSON")
    args = parser.parse_args()
    if args.n_blocks < 1:
        parser.error("--range must be >= 1")
    if not 0 <= args.warm_cost <= COLD_COST:
        parser.error(f"--warm-cost must be between 0 and {COLD_COST}")
    return args


def analyze_interval(args, data_dir: Path, trace_dir: Path, start_block: int) -> dict:
    end_block = start_block + args.n_blocks - 1
    print(f"\n===== interval {start_block}..{end_block} ({args.n_blocks} blocks) =====")

    static, static_blocks = analyze_static(data_dir, start_block, args.n_blocks)
    print_static(static)

    headers = None
    if args.endpoint:
        headers = check_blocks(normalize_endpoint(args.endpoint), start_block, args.n_blocks, static_blocks)

    full = None
    if trace_dir.is_dir():
        full, _ = analyze_full_trace(trace_dir, data_dir, start_block, args.n_blocks, args.warm_cost)
        print_full_trace(full)
        if not full["blocks"]:
            full = None
    else:
        print(f"No full trace directory {trace_dir}; skipping SLOAD/SSTORE and KECCAK256 stats", file=sys.stderr)

    print_summary(static, full)
    return {
        "start_block": start_block,
        "end_block": end_block,
        "range": args.n_blocks,
        "block_headers": headers,
        "static": static,
        "full_trace": full,
    }


def print_average(average: dict) -> None:
    print(f"\n===== average of {average['intervals']} intervals =====")
    print_static(average["static"])
    headers = average.get("block_headers")
    if headers:
        print(
            f"RPC block headers (average): gasUsed {headers['gas_used']:.0f}  "
            f"txs {headers['txs']:.1f}  avg tx gas {headers['avg_tx_gas']:.0f}"
        )
    if average.get("full_trace"):
        print_full_trace(average["full_trace"])
    print_summary(average["static"], average.get("full_trace"))


def main():
    args = parse_args()
    data_dir, trace_dir = Path(args.data_dir), Path(args.trace_dir)
    if not data_dir.is_dir():
        print(f"No such data directory: {data_dir}", file=sys.stderr)
        return 1

    ranges = [analyze_interval(args, data_dir, trace_dir, start) for start in args.start_blocks]
    average = None
    if len(ranges) > 1:
        average = average_values(ranges)
        average["intervals"] = len(ranges)
        print_average(average)

    if args.output:
        report = {
            "start_blocks": args.start_blocks,
            "range": args.n_blocks,
            "intervals": ranges,
            "average": average,
        }
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
