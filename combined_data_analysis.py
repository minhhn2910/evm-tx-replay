#!/usr/bin/env python3
"""Opcode frequency and gas over one or more block intervals.

Summarize traces once, then plot any interval from that summary. Opcode gas
share uses the receipt gas of transactions that are not simple transfers.

  python combined_data_analysis.py --summarize --trace-dir raw_block --data-dir block_result --summary-dir opcode_summary --start-block 24000000,24100000 --range 5 --jobs 8
  python combined_data_analysis.py --horizontal-bar --summary-dir opcode_summary --start-block 24000000 --range 2 --call-gas own -o opcodes.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_analysis import as_int
from gas_sharing_analysis import parse_start_blocks
from tx_opcode_summary import summarize_tx
from utils.rpc import normalize_endpoint, rpc_call

FORCED = ("SLOAD", "SSTORE", "KECCAK256", "CALL", "STATICCALL")
# Wong colorblind-safe set, then Tol muted extras. Other / Remainder stay gray.
PALETTE = [
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#D55E00",
    "#CC79A7",
    "#56B4E9",
    "#F0E442",
    "#000000",
    "#332288",
    "#88CCEE",
    "#44AA99",
    "#117733",
    "#999933",
    "#DDCC77",
    "#661100",
    "#882255",
]
OTHER_COLOR = "#BDBDBD"
FREQ_COLOR = "#0072B2"
GAS_COLOR = "#E69F00"


def interval_blocks(starts: list[int], n_blocks: int) -> list[int]:
    blocks = []
    for start in starts:
        blocks.extend(range(start, start + n_blocks))
    return blocks


def collect_block_gas(endpoint: str, blocks: list[int], output: Path) -> None:
    gas = {}
    for block in blocks:
        header = rpc_call(endpoint, "eth_getBlockByNumber", [hex(block), False])
        record = {"gasUsed": as_int(header["gasUsed"]), "txs": len(header["transactions"])}
        gas[str(block)] = record
        print(f"  block {block}: gasUsed {record['gasUsed']}  txs {record['txs']}")
    output.write_text(json.dumps(gas, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    total_gas = sum(record["gasUsed"] for record in gas.values())
    total_txs = sum(record["txs"] for record in gas.values())
    print(f"transactions {total_txs}  gas used {total_gas}")


def hex_hash(value) -> str:
    text = value.hex() if hasattr(value, "hex") and not isinstance(value, str) else str(value)
    text = text.lower()
    return text if text.startswith("0x") else "0x" + text


def fetch_block(endpoint: str, block: int) -> tuple[list[str], int, dict[str, int]]:
    """Transaction order, header gasUsed, and receipt gasUsed keyed by hash."""
    header = rpc_call(endpoint, "eth_getBlockByNumber", [hex(block), False])
    receipts = rpc_call(endpoint, "eth_getBlockReceipts", [hex(block)])
    order = [hex_hash(tx) for tx in header["transactions"]]
    gas_by_tx = {hex_hash(receipt["transactionHash"]): as_int(receipt["gasUsed"]) for receipt in receipts}
    return order, as_int(header["gasUsed"]), gas_by_tx


def merge_ops(dst: dict, src: dict) -> None:
    for name, row in src.items():
        current = dst.setdefault(name, [0] * len(row))
        if len(current) < len(row):
            current.extend([0] * (len(row) - len(current)))
        for index, value in enumerate(row):
            current[index] += value


def has_trace(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _drop_parts(block_dir: Path) -> None:
    parts = block_dir / "parts"
    if parts.is_dir():
        shutil.rmtree(parts)
    leftover = block_dir / "opcodes.jsonl"
    if leftover.is_file():
        leftover.unlink()


def summarize_blocks(endpoint: str, trace_dir: Path, data_dir: Path, summary_dir: Path, blocks: list[int], jobs: int, resume: bool) -> None:
    """Write txs.jsonl and block.json for each block. One process per transaction."""
    all_txs = all_gas = analysis_txs = analysis_gas = 0
    for block in blocks:
        block_dir = summary_dir / str(block)
        txs_path = block_dir / "txs.jsonl"
        block_path = block_dir / "block.json"
        if resume and txs_path.is_file() and block_path.is_file():
            record = json.loads(block_path.read_text(encoding="utf-8"))
            _drop_parts(block_dir)
            print(
                f"  block {block}: resume  txs {record['txs']}  simple {record['simpleTxs']}  "
                f"gas {record['gasUsed']}  denom {record['denomGas']}"
            )
            all_txs += record["txs"]
            all_gas += record["gasUsed"]
            analysis_txs += record["txs"] - record["simpleTxs"]
            analysis_gas += record["denomGas"]
            continue

        order, header_gas, gas_by_tx = fetch_block(endpoint, block)
        missing = [tx for tx in order if tx not in gas_by_tx]
        if missing:
            raise SystemExit(f"block {block} receipts have no gas for {missing[:4]}")
        receipt_gas = sum(gas_by_tx[tx] for tx in order)
        if receipt_gas != header_gas:
            print(f"  warning: block {block} receipts {receipt_gas} != header gas {header_gas}", file=sys.stderr)

        block_dir.mkdir(parents=True, exist_ok=True)
        cached: dict[str, dict] = {}
        tasks = []
        simple = set()
        for tx in order:
            trace = trace_dir / str(block) / tx / "txTraceEIP3155.json"
            if gas_by_tx[tx] == 21000 or not has_trace(trace):
                simple.add(tx)
                continue
            tasks.append((str(trace), str(data_dir / str(block) / tx), tx))

        if tasks:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = {pool.submit(summarize_tx, *task): task[2] for task in tasks}
                for future in as_completed(futures):
                    tx = futures[future]
                    cached[tx] = future.result()

        with txs_path.open("w", encoding="utf-8") as handle:
            for index, tx in enumerate(order):
                record = {"tx": tx, "n": index, "gasUsed": gas_by_tx[tx], "simple": tx in simple}
                if tx not in simple:
                    record["ops"] = cached[tx]["ops"]
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")

        merged: dict = {}
        exec_gas = mismatches = unmatched = 0
        for tx, totals in cached.items():
            merge_ops(merged, totals["op"])
            exec_gas += totals["exec"]
            mismatches += totals["sstore_mismatch"]
            unmatched += totals["static_unmatched"]
        simple_gas = sum(gas_by_tx[tx] for tx in simple)
        denom_gas = sum(gas_by_tx[tx] for tx in order if tx not in simple)
        record = {
            "block": block,
            "gasUsed": header_gas,
            "txs": len(order),
            "simpleTxs": len(simple),
            "simpleGas": simple_gas,
            "denomGas": denom_gas,
            "execGas": exec_gas,
            "opcodes": merged,
            "sstoreMismatch": mismatches,
            "staticUnmatched": unmatched,
        }
        (block_dir / "block.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        _drop_parts(block_dir)
        print(
            f"  block {block}: txs {len(order)}  simple {len(simple)}  "
            f"gas {header_gas}  denom {denom_gas}  exec {exec_gas}"
        )
        if mismatches or unmatched:
            print(f"    sstore mismatches {mismatches}  staticcall frames unmatched {unmatched}", file=sys.stderr)
        all_txs += len(order)
        all_gas += header_gas
        analysis_txs += len(order) - len(simple)
        analysis_gas += record["denomGas"]
    print(f"transactions {all_txs}  gas used {all_gas}")
    print(f"analysis transactions {analysis_txs}  analysis gas {analysis_gas}")


def opcode_gas(row: list[int], call_gas: str) -> int:
    """`own` is every opcode's own gas. `inside` uses call `direct` and other opcodes' depth-1 gas."""
    if call_gas == "inside":
        return row[3] if len(row) == 4 else row[2]
    return row[1]


def load_summary(summary_dir: Path, blocks: list[int], call_gas: str) -> dict:
    """Pool block.json files. Gas share uses denomGas, which excludes simple transfers."""
    counts: Counter = Counter()
    gas: Counter = Counter()
    all_txs = all_gas = analysis_txs = denom = 0
    for block in blocks:
        path = summary_dir / str(block) / "block.json"
        if not path.is_file():
            raise SystemExit(f"No summary for block {block} ({path}). Run --summarize first.")
        record = json.loads(path.read_text(encoding="utf-8"))
        all_txs += record["txs"]
        all_gas += record["gasUsed"]
        analysis_txs += record["txs"] - record["simpleTxs"]
        denom += record["denomGas"]
        for name, row in record["opcodes"].items():
            counts[name] += row[0]
            gas[name] += opcode_gas(row, call_gas)
    return {
        "counts": counts,
        "gas": gas,
        "all_txs": all_txs,
        "all_gas": all_gas,
        "analysis_txs": analysis_txs,
        "denom": denom,
    }


def ranked_positive(values: Counter) -> list[tuple[str, int]]:
    return sorted(((name, value) for name, value in values.items() if value > 0), key=lambda item: -item[1])


def percent(value: int, total: int) -> float:
    return 100.0 * value / total if total else 0.0


def format_percent(pct: float) -> str:
    return f"{pct:.2f}%" if pct < 1 else f"{pct:.1f}%"


def pie_slices(values: Counter, denominator: int, top: int, forced: tuple[str, ...]):
    """Largest `top` names, plus forced names with a positive value. Rest is Other."""
    ranked = ranked_positive(values)
    keep = {name for name, _ in ranked[:top]}
    keep.update(name for name in forced if values[name] > 0)
    labeled = [(name, values[name]) for name, _ in ranked if name in keep]
    other = sum(value for name, value in ranked if name not in keep)
    slices = labeled + ([("Other", other)] if other else [])
    percents = [percent(value, denominator) for _, value in slices]
    return slices, percents


def paired_rows(counts: Counter, gas: Counter, top: int):
    """The `top` opcodes with the most gas, plus Other. Sorted by gas descending."""
    keep = [name for name, _ in ranked_positive(gas)[:top]]
    rows = [(name, counts[name], gas[name]) for name in keep]
    other_count = sum(value for name, value in counts.items() if name not in keep)
    other_gas = sum(value for name, value in gas.items() if name not in keep and value > 0)
    if other_count or other_gas:
        rows.append(("Other", other_count, other_gas))
    rows.sort(key=lambda row: -row[2])
    return rows


def draw_pie(ax, slices, percents, title: str, font_size: int, gap: int = 0) -> None:
    if not slices:
        ax.set_title(title)
        ax.axis("off")
        return
    colors = []
    for index, (name, _) in enumerate(slices):
        colors.append(OTHER_COLOR if name == "Other" else PALETTE[index % len(PALETTE)])
    values = [value for _, value in slices]
    # An unlabeled gap keeps each wedge equal to its share of all gas consumed.
    if gap > 0:
        values.append(gap)
        colors.append("white")
    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"linewidth": 0.4, "edgecolor": "white"},
    )
    ax.set_title(title, fontsize=font_size + 1, pad=8)
    labels = [f"{name}  {format_percent(pct)}" for (name, _), pct in zip(slices, percents)]
    ax.legend(
        wedges[: len(slices)],
        labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        fontsize=font_size,
        handlelength=1.0,
        handleheight=1.0,
        labelspacing=0.35,
    )


def style_figure(font_size: float) -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": font_size,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.6,
        }
    )


def save_figure(fig, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {output}")


def plot_pies(counts: Counter, gas: Counter, block_gas: int, output: Path, top: int, font_size: float, width: float, height: float):
    style_figure(font_size)
    steps = sum(counts.values())
    opcode_gas = sum(gas.values())
    count_slices, count_pct = pie_slices(counts, steps, top, FORCED)
    gas_slices, gas_pct = pie_slices(gas, block_gas, top, FORCED)

    fig, axes = plt.subplots(1, 2, figsize=(width, height))
    fig.patch.set_facecolor("white")
    draw_pie(axes[0], count_slices, count_pct, "(a) Opcode frequency", font_size)
    draw_pie(axes[1], gas_slices, gas_pct, "(b) Opcode gas", font_size, gap=max(0, block_gas - opcode_gas))
    fig.tight_layout()
    save_figure(fig, output)
    print(f"  steps {steps}  opcode gas {opcode_gas}  denom gas {block_gas}")
    return count_pct, gas_pct


def plot_bars(
    counts: Counter,
    gas: Counter,
    block_gas: int,
    output: Path,
    top: int,
    font_size: float,
    width: float,
    bar_height: float,
):
    """One row per opcode: frequency on the left, gas share of block gas on the right."""
    style_figure(font_size)
    steps = sum(counts.values())
    rows = paired_rows(counts, gas, top)
    # barh draws the first row at the bottom; invert so the largest gas is on top.
    rows = list(reversed(rows))
    freq = [percent(count, steps) for _, count, _ in rows]
    share = [percent(opcode_gas, block_gas) for _, _, opcode_gas in rows]
    labels = [name for name, _, _ in rows]
    figure_height = bar_height * len(rows) + 0.42

    fig, (ax_freq, ax_gas) = plt.subplots(
        1, 2, sharey=True, figsize=(width, figure_height), layout="constrained", gridspec_kw={"wspace": 0.05}
    )
    fig.patch.set_facecolor("white")
    y = list(range(len(rows)))
    ax_freq.barh(y, freq, color=FREQ_COLOR, height=0.68)
    ax_gas.barh(y, share, color=GAS_COLOR, height=0.68)
    ax_freq.set_yticks(y, labels)
    ax_freq.tick_params(axis="y", length=0, pad=2)
    ax_freq.margins(y=0.04)
    _label_bars(ax_freq, freq, font_size)
    _label_bars(ax_gas, share, font_size)
    _plain_axis(ax_freq, "Frequency (%)")
    _plain_axis(ax_gas, "Gas (%)")
    ax_gas.tick_params(axis="y", left=False, labelleft=False)
    fig.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, color=FREQ_COLOR, label="Frequency"),
            plt.Rectangle((0, 0), 1, 1, color=GAS_COLOR, label="Gas"),
        ],
        loc="outside upper center",
        ncol=2,
        frameon=False,
        fontsize=font_size,
        handlelength=1.2,
        handleheight=0.7,
        columnspacing=1.2,
    )
    save_figure(fig, output)
    print(f"  steps {steps}  opcode gas {sum(gas.values())}  denom gas {block_gas}")


def _label_bars(ax, values: list[float], font_size: float) -> None:
    peak = max(values) if values else 1.0
    ax.set_xlim(0, peak * 1.28)
    for y, value in enumerate(values):
        ax.text(value + peak * 0.02, y, format_percent(value), va="center", ha="left", fontsize=font_size, color="#222222")


def _plain_axis(ax, title: str) -> None:
    ax.set_title(title, fontsize=plt.rcParams["font.size"], pad=2)
    ax.set_xlabel("")
    ax.tick_params(axis="x", length=0, labelbottom=False)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_facecolor("white")


def parse_args():
    parser = argparse.ArgumentParser(description="Opcode frequency and gas pies across block intervals.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--collect-gas", action="store_true", help="Save RPC header gasUsed and tx count for the intervals")
    mode.add_argument("--summarize", action="store_true", help="Reduce traces to opcode_summary (parallel per transaction)")
    mode.add_argument("--plot", action="store_true", help="Plot opcode pies from a summary directory")
    mode.add_argument("--horizontal-bar", action="store_true", help="Paired horizontal bars of frequency and gas")
    parser.add_argument(
        "--start-block",
        type=parse_start_blocks,
        required=True,
        dest="start_blocks",
        help="First block, or a comma-separated list of interval starts",
    )
    parser.add_argument("--range", dest="n_blocks", type=int, required=True, help="Blocks per interval")
    parser.add_argument("--endpoint", default="http://localhost:8545", help="RPC endpoint for --collect-gas and --summarize")
    parser.add_argument("--trace-dir", default="raw_block", help="Full-trace root (txTraceEIP3155.json)")
    parser.add_argument("--data-dir", default="block_result", help="Fast-mode root (txCalls.json and storage)")
    parser.add_argument("--summary-dir", default="opcode_summary", help="Stage-1 output read by the plot modes")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="Worker processes for --summarize")
    parser.add_argument("--resume", action="store_true", help="Skip a block that already has txs.jsonl and block.json")
    parser.add_argument(
        "--call-gas",
        choices=("own", "inside"),
        default="own",
        help="own: each opcode's gas. inside: call gas includes the frame, other opcodes keep depth-1 gas",
    )
    parser.add_argument("--gas-file", help="Block gas JSON written by --collect-gas")
    parser.add_argument("-o", "--output", help="Gas JSON (--collect-gas) or PDF (--plot, --horizontal-bar)")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Bar chart: opcodes with the most gas. Pies: largest slices (default 10)",
    )
    parser.add_argument("--font-size", type=float, default=9, help="Legend font size")
    parser.add_argument("--width", type=float, default=11.0, help="Figure width in inches")
    parser.add_argument("--height", type=float, default=5.2, help="Pie figure height in inches")
    parser.add_argument(
        "--bar-height",
        type=float,
        default=0.22,
        help="Height of one bar row in inches (default 0.22)",
    )
    args = parser.parse_args()
    if args.n_blocks < 1:
        parser.error("--range must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    if args.bar_height <= 0:
        parser.error("--bar-height must be > 0")
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    if (args.collect_gas or args.plot or args.horizontal_bar) and not args.output:
        parser.error("-o is required")
    return args


def main():
    args = parse_args()
    blocks = interval_blocks(args.start_blocks, args.n_blocks)
    if args.collect_gas:
        collect_block_gas(normalize_endpoint(args.endpoint), blocks, Path(args.output))
        return 0
    if args.summarize:
        trace_dir = Path(args.trace_dir)
        if not trace_dir.is_dir():
            print(f"No such trace directory: {trace_dir}", file=sys.stderr)
            return 1
        summarize_blocks(
            normalize_endpoint(args.endpoint),
            trace_dir,
            Path(args.data_dir),
            Path(args.summary_dir),
            blocks,
            args.jobs,
            args.resume,
        )
        return 0

    summary = load_summary(Path(args.summary_dir), blocks, args.call_gas)
    counts, gas = summary["counts"], summary["gas"]
    if not counts:
        print("No opcode steps found", file=sys.stderr)
        return 1
    print(f"  txs {summary['analysis_txs']}  blocks {len(blocks)}  call-gas {args.call_gas}")
    if args.horizontal_bar:
        plot_bars(
            counts, gas, summary["denom"], Path(args.output), args.top, args.font_size, args.width, args.bar_height
        )
    else:
        plot_pies(counts, gas, summary["denom"], Path(args.output), args.top, args.font_size, args.width, args.height)
    print(f"transactions {summary['all_txs']}  gas used {summary['all_gas']}")
    print(f"analysis transactions {summary['analysis_txs']}  analysis gas {summary['denom']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
