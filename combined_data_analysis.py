#!/usr/bin/env python3
"""Opcode frequency and gas over one or more block intervals.

Collect header gas first, then plot. Counts and gas are pooled across every
interval (not averaged per interval).

Opcode gas is the gas the trace actually consumed. A call's gasCost includes
gas forwarded to the callee; the negative gasCost on return is the unused part
coming back, so it is subtracted from that call. The callee's own steps keep
their gasCost. Gas share is that sum divided by header gasUsed (all gas the
blocks consumed).

  python combined_data_analysis.py --collect-gas --start-block 24000000,24100000 --range 5 -o block_gas.json
  python combined_data_analysis.py --plot --gas-file block_gas.json --start-block 24000000,24100000 --range 5 -o opcodes.pdf
  python combined_data_analysis.py --horizontal-bar --gas-file block_gas.json --start-block 24000000,24100000 --range 5 -o opcodes.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_analysis import as_int, iter_txs
from gas_sharing_analysis import parse_start_blocks
from utils.opcodes import get_opcode_name
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


def opcode_name(op: int) -> str:
    name = get_opcode_name(hex(op)[2:])
    return "KECCAK256" if name == "SHA3" else name


def collect_block_gas(endpoint: str, blocks: list[int], output: Path) -> None:
    gas = {}
    for block in blocks:
        header = rpc_call(endpoint, "eth_getBlockByNumber", [hex(block), False])
        gas[str(block)] = as_int(header["gasUsed"])
        print(f"  block {block}: gasUsed {gas[str(block)]}")
    output.write_text(json.dumps(gas, indent=2), encoding="utf-8")
    print(f"Wrote {output}")


CALLS = {"CALL", "STATICCALL", "DELEGATECALL", "CALLCODE", "CREATE", "CREATE2"}


def scan_opcodes(trace_dir: Path, blocks: list[int]) -> tuple[Counter, Counter, int]:
    """Return (step counts, gas actually consumed, number of txs).

    gasCost on a call is the drop from the caller's gas to the callee's gas, so
    it includes the gas forwarded to the callee. The unused part is a negative
    gasCost on the first opcode after the call returns; that amount is subtracted
    from the call. A precompile never changes depth, so the same negative on the
    next opcode is subtracted from that call. The total equals the gas counter
    drop across the trace.
    """
    counts: Counter = Counter()
    gas: Counter = Counter()
    txs = 0
    for block in blocks:
        for _block, tx_dir in iter_txs(trace_dir, block, 1):
            path = tx_dir / "txTraceEIP3155.json"
            if not path.is_file():
                continue
            txs += 1
            open_calls: list[str] = []
            prev_name = None
            prev_depth = None
            with path.open(encoding="utf-8") as trace:
                for line in trace:
                    if not line.strip():
                        continue
                    step = json.loads(line)
                    name = opcode_name(as_int(step["op"]))
                    cost = int(str(step["gasCost"]), 0)
                    depth = step["depth"]
                    if prev_name is not None:
                        if depth == prev_depth + 1 and prev_name in CALLS:
                            open_calls.append(prev_name)
                        elif depth < prev_depth:
                            if cost < 0 and open_calls:
                                gas[open_calls[-1]] += cost
                                cost = 0
                            for _ in range(prev_depth - depth):
                                if open_calls:
                                    open_calls.pop()
                        elif cost < 0 and prev_name in CALLS and depth == prev_depth:
                            gas[prev_name] += cost
                            cost = 0
                    counts[name] += 1
                    if cost > 0:
                        gas[name] += cost
                    prev_name, prev_depth = name, depth
    return counts, gas, txs


def load_block_gas(path: Path, blocks: list[int]) -> int:
    saved = json.loads(path.read_text(encoding="utf-8"))
    missing = [block for block in blocks if str(block) not in saved]
    if missing:
        raise SystemExit(f"gas file {path} has no entry for blocks {missing[:8]}")
    return sum(int(saved[str(block)]) for block in blocks)


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
    print(f"  steps {steps}  opcode gas {opcode_gas}  block gas {block_gas}")
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
    print(f"  steps {steps}  opcode gas {sum(gas.values())}  block gas {block_gas}")


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
    mode.add_argument("--collect-gas", action="store_true", help="Save RPC header gasUsed for the intervals")
    mode.add_argument("--plot", action="store_true", help="Plot opcode pies from traces and a gas file")
    mode.add_argument("--horizontal-bar", action="store_true", help="Paired horizontal bars of frequency and gas")
    parser.add_argument(
        "--start-block",
        type=parse_start_blocks,
        required=True,
        dest="start_blocks",
        help="First block, or a comma-separated list of interval starts",
    )
    parser.add_argument("--range", dest="n_blocks", type=int, required=True, help="Blocks per interval")
    parser.add_argument("--endpoint", default="http://localhost:8545", help="RPC endpoint for --collect-gas")
    parser.add_argument("--trace-dir", default="raw_block", help="Full-trace root (txTraceEIP3155.json)")
    parser.add_argument("--gas-file", help="Block gas JSON written by --collect-gas")
    parser.add_argument("-o", "--output", required=True, help="Gas JSON (--collect-gas) or PDF (--plot)")
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
    if (args.plot or args.horizontal_bar) and not args.gas_file:
        parser.error("--gas-file is required")
    return args


def main():
    args = parse_args()
    blocks = interval_blocks(args.start_blocks, args.n_blocks)
    if args.collect_gas:
        collect_block_gas(normalize_endpoint(args.endpoint), blocks, Path(args.output))
        return 0

    trace_dir = Path(args.trace_dir)
    if not trace_dir.is_dir():
        print(f"No such trace directory: {trace_dir}", file=sys.stderr)
        return 1
    counts, gas, txs = scan_opcodes(trace_dir, blocks)
    if not counts:
        print("No opcode steps found", file=sys.stderr)
        return 1
    block_gas = load_block_gas(Path(args.gas_file), blocks)
    print(f"  txs {txs}  blocks {len(blocks)}")
    if args.horizontal_bar:
        plot_bars(
            counts, gas, block_gas, Path(args.output), args.top, args.font_size, args.width, args.bar_height
        )
    else:
        plot_pies(counts, gas, block_gas, Path(args.output), args.top, args.font_size, args.width, args.height)
    return 0


if __name__ == "__main__":
    sys.exit(main())
