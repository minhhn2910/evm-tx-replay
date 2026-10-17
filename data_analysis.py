#!/usr/bin/env python3
"""Analyze fast-mode traces under block_result/.

Each transaction folder contains txStateWrite.json / txStateRead.json
and txCalls.json (callTracer), as described in AGENT.md.

Examples:
  python data_analysis.py --storage --start-block 24200000 --range 12 -o storage
  python data_analysis.py --staticcall --start-block 24200000 --range 12 -o static --top 20
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def as_int(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith(("0x", "0X")) else int(text)


def load_json(path: Path):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def short_hex(value: str, head: int = 10, tail: int = 6) -> str:
    text = value or "0x"
    if len(text) <= head + tail + 1:
        return text
    return f"{text[: head + 2]}…{text[-tail:]}"


PLOT_TAIL = 10  # values above this are merged into one ">10" bar


def output_paths(output: str) -> tuple[Path, Path]:
    path = Path(output)
    stem = path.with_suffix("") if path.suffix.lower() in {".png", ".pdf", ".json"} else path
    stem.parent.mkdir(parents=True, exist_ok=True)
    return stem.with_suffix(".pdf"), stem.with_suffix(".json")


def mode_output(output: str, mode: str, both: bool) -> str:
    if not both:
        return output
    path = Path(output)
    name = path.stem if path.suffix.lower() in {".png", ".pdf", ".json"} else path.name
    return str(path.parent / f"{name}_{mode}")


def save_report(plot: Path, json_path: Path, report: dict, fig) -> None:
    fig.savefig(plot, bbox_inches="tight")
    plt.close(fig)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {plot}")
    print(f"Wrote {json_path}")


# ---------------------------------------------------------------------------
# block_result layout: <data-dir>/<block>/<tx_hash>/{txStateWrite,txStateRead,txCalls}.json
# ---------------------------------------------------------------------------

def iter_txs(data_dir: Path, start_block: int, n_blocks: int):
    """Yield (block_number, tx_dir) in block order, txs by collection mtime."""
    for block in range(start_block, start_block + n_blocks):
        folder = data_dir / str(block)
        if not folder.is_dir():
            print(f"  skip missing block {block}", file=sys.stderr)
            continue
        txs = sorted(
            (p for p in folder.iterdir() if p.is_dir()),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        print(f"  block {block}: {len(txs)} txs")
        for tx_dir in txs:
            yield block, tx_dir


def storage_writes(writes):
    """Each written slot is one write. Accepts the flattened list or a legacy diff."""
    if isinstance(writes, list):
        for item in writes:
            yield (
                str(item.get("address") or "").lower(),
                str(item.get("slot") or "").lower(),
                as_int(item.get("old_value")),
                as_int(item.get("value")),
            )
        return

    pre = (writes or {}).get("pre") or {}
    post = (writes or {}).get("post") or {}
    for address in set(pre) | set(post):
        before_slots = ((pre.get(address) or {}).get("storage") or {})
        after_slots = ((post.get(address) or {}).get("storage") or {})
        for slot in set(before_slots) | set(after_slots):
            before = as_int(before_slots[slot]) if slot in before_slots else 0
            after = as_int(after_slots[slot]) if slot in after_slots else 0
            yield address.lower(), slot.lower(), before, after


def walk_calls(node: dict):
    if not node:
        return
    yield node
    for child in node.get("calls") or []:
        yield from walk_calls(child)


def streaks(values) -> list[int]:
    """Lengths of consecutive equal runs."""
    runs: list[int] = []
    prev, length = object(), 0
    for value in values:
        if value == prev:
            length += 1
        else:
            if length:
                runs.append(length)
            prev, length = value, 1
    if length:
        runs.append(length)
    return runs


def plot_histogram(counts: Counter, xlabel: str, ylabel: str, start: int = 0, tail: int = PLOT_TAIL):
    """Single histogram: integer bins start..tail, plus one bar for values > tail."""
    labels = [str(x) for x in range(start, tail + 1)]
    heights = [counts.get(x, 0) for x in range(start, tail + 1)]
    tail_n = sum(n for value, n in counts.items() if value > tail)
    if tail_n:
        labels.append(f">{tail}")
        heights.append(tail_n)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    xs = range(len(heights))
    colors = ["#3b6ea5"] * len(heights)
    if tail_n:
        colors[-1] = "#c44e52"
    ax.bar(xs, heights, width=0.8, color=colors, edgecolor="white", linewidth=0.4)
    ax.set_xticks(list(xs), labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Mode 1: storage writes
# ---------------------------------------------------------------------------

def analyze_storage(data_dir: Path, start_block: int, n_blocks: int, output: str) -> dict:
    last: dict[tuple[str, str], tuple[int, int]] = {}
    distances: list[int] = []
    n_writes = n_rewrite = n_same_value = n_same_after_gap = 0

    print("Storage writes")
    for block, tx_dir in iter_txs(data_dir, start_block, n_blocks):
        writes = load_json(tx_dir / "txStateWrite.json")
        if writes is None:
            writes = load_json(tx_dir / "txStateDiff.json")
        if writes is None:
            continue
        for address, slot, _before, after in storage_writes(writes):
            n_writes += 1
            key = (address, slot)
            if key in last:
                prev_block, prev_value = last[key]
                distance = block - prev_block
                distances.append(distance)
                n_rewrite += 1
                if after == prev_value:
                    n_same_value += 1
                    if distance > 0:
                        n_same_after_gap += 1
            last[key] = (block, after)

    hist = Counter(distances)
    pct_same = 100.0 * n_same_value / n_rewrite if n_rewrite else 0.0
    pct_same_after = 100.0 * n_same_after_gap / n_rewrite if n_rewrite else 0.0

    print(f"  writes              {n_writes}")
    print(f"  unique slots        {len(last)}")
    print(f"  re-writes           {n_rewrite}")
    print(f"  same value as last  {n_same_value} ({pct_same:.2f}% of re-writes)")
    print(f"  same value, dist>0  {n_same_after_gap} ({pct_same_after:.2f}% of re-writes)")

    fig = plot_histogram(
        hist,
        xlabel="Blocks since previous write of the same slot",
        ylabel="Number of writes",
        start=0,
    )

    report = {
        "mode": "storage",
        "start_block": start_block,
        "range": n_blocks,
        "writes": n_writes,
        "unique_slots": len(last),
        "rewrites": n_rewrite,
        "same_value_as_previous": n_same_value,
        "same_value_pct": round(pct_same, 4),
        "same_value_after_gap": n_same_after_gap,
        "same_value_after_gap_pct": round(pct_same_after, 4),
        "distance_histogram": {str(k): hist[k] for k in sorted(hist)},
    }
    plot, json_path = output_paths(output)
    save_report(plot, json_path, report, fig)
    return report


# ---------------------------------------------------------------------------
# Mode 2: STATICCALL
# ---------------------------------------------------------------------------

def analyze_staticcall(
    data_dir: Path, start_block: int, n_blocks: int, output: str, top_n: int
) -> dict:
    n_calls = n_static = 0
    # (contract, data) -> results in chronological order
    results_by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    gas_by_entry: Counter = Counter()

    print("STATICCALLs")
    for _block, tx_dir in iter_txs(data_dir, start_block, n_blocks):
        tree = load_json(tx_dir / "txCalls.json")
        if tree is None:
            continue
        for call in walk_calls(tree):
            n_calls += 1
            if str(call.get("type") or "").upper() != "STATICCALL":
                continue
            n_static += 1
            contract = (call.get("to") or "").lower()
            data = (call.get("input") or "0x").lower()
            result = (call.get("output") or "0x").lower()
            gas_used = as_int(call.get("gasUsed"))
            results_by_key[(contract, data)].append(result)
            gas_by_entry[(contract, data, result)] += gas_used

    all_streaks: list[int] = []
    n_in_streak_ge2 = 0
    n_keys_with_streak_ge2 = 0
    for seq in results_by_key.values():
        runs = streaks(seq)
        all_streaks.extend(runs)
        if any(run >= 2 for run in runs):
            n_keys_with_streak_ge2 += 1
        n_in_streak_ge2 += sum(run for run in runs if run >= 2)

    hist = Counter(all_streaks)
    pct_static = 100.0 * n_static / n_calls if n_calls else 0.0
    pct_inv_ge2 = 100.0 * n_in_streak_ge2 / n_static if n_static else 0.0
    pct_keys_ge2 = 100.0 * n_keys_with_streak_ge2 / len(results_by_key) if results_by_key else 0.0

    top = gas_by_entry.most_common(top_n)
    print(f"  all calls                         {n_calls}")
    print(f"  STATICCALL                        {n_static} ({pct_static:.2f}%)")
    print(f"  unique (contract, data)           {len(results_by_key)}")
    print(f"  invocations in consecutive-same   {n_in_streak_ge2} ({pct_inv_ge2:.2f}%)")
    print(f"  (contract, data) with streak >= 2 {n_keys_with_streak_ge2} ({pct_keys_ge2:.2f}%)")
    print(f"  top {len(top)} by combined gasUsed:")
    print(f"  {'gas':>12}  contract  data  result")
    for (contract, data, result), gas in top:
        print(f"  {gas:12d}  {short_hex(contract)}  {short_hex(data, 8, 4)}  {short_hex(result, 8, 4)}")

    fig = plot_histogram(
        hist,
        xlabel="Consecutive identical results for the same (contract, data)",
        ylabel="Number of sequences",
        start=1,
    )

    report = {
        "mode": "staticcall",
        "start_block": start_block,
        "range": n_blocks,
        "all_calls": n_calls,
        "staticcalls": n_static,
        "staticcall_pct": round(pct_static, 4),
        "unique_contract_data": len(results_by_key),
        "invocations_in_streak_ge2": n_in_streak_ge2,
        "invocations_in_streak_ge2_pct": round(pct_inv_ge2, 4),
        "keys_with_streak_ge2": n_keys_with_streak_ge2,
        "keys_with_streak_ge2_pct": round(pct_keys_ge2, 4),
        "streak_histogram": {str(k): hist[k] for k in sorted(hist)},
        "top": [
            {"contract": c, "data": d, "result": r, "gas_used": gas}
            for (c, d, r), gas in top
        ],
    }
    plot, json_path = output_paths(output)
    save_report(plot, json_path, report, fig)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze fast-mode block_result traces (storage writes / STATICCALLs)."
    )
    parser.add_argument(
        "--storage",
        action="store_true",
        help="Analyze storage-write distance and unchanged-value rate",
    )
    parser.add_argument(
        "--staticcall",
        action="store_true",
        help="Analyze STATICCALL share, consecutive results, and top-N gas",
    )
    parser.add_argument("--start-block", type=int, required=True, help="First block number")
    parser.add_argument(
        "--range",
        dest="n_blocks",
        type=int,
        required=True,
        help="Number of consecutive blocks to include",
    )
    parser.add_argument("-o", "--output", required=True, help="Output file prefix (writes .pdf and .json)")
    parser.add_argument("--data-dir", default="block_result", help="Root folder of collected blocks")
    parser.add_argument("--top", type=int, default=20, help="Top-N STATICCALL entries by gas (default 20)")
    args = parser.parse_args()
    if not args.storage and not args.staticcall:
        parser.error("specify --storage and/or --staticcall")
    if args.n_blocks < 1:
        parser.error("--range must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    return args


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print(f"No such data directory: {data_dir}", file=sys.stderr)
        return 1

    both = args.storage and args.staticcall
    if args.storage:
        analyze_storage(data_dir, args.start_block, args.n_blocks, mode_output(args.output, "storage", both))
    if args.staticcall:
        analyze_staticcall(
            data_dir, args.start_block, args.n_blocks, mode_output(args.output, "staticcall", both), args.top
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
