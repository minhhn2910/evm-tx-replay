#!/usr/bin/env python3
"""Horizontal bars of opcode frequency and gas, read from an opcode summary.

  python combined_data_analysis.py --summary-dir opcode_summary --start-block 24000000 --range 2 --call-gas own -o opcodes.pdf
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

from gas_sharing_analysis import parse_start_blocks

# Figure size in inches, and spacing inside one opcode group.
# BAR_SIZE, BAR_DISTANCE, and GROUP_DISTANCE are weights: two bars, the gap
# between those bars, and the gap between groups. They are scaled to fill the group.
FIG_SIZE = (8, 3.5)
FONT_SIZE = 8
BAR_SIZE = 0.28
BAR_DISTANCE = 0.08
GROUP_DISTANCE = 0.36
BAR_ALPHA = 0.6

FREQ_COLOR = "#0072B2"
FREQ_HATCH = "//"
GAS_COLOR = "#E69F00"
GAS_HATCH = "\\\\"
LABEL_COLOR = "#333333"

IGNORED_CALLS = ("CALL", "STATICCALL", "DELEGATECALL", "CALLCODE")


def interval_blocks(starts: list[int], n_blocks: int) -> list[int]:
    blocks = []
    for start in starts:
        blocks.extend(range(start, start + n_blocks))
    return blocks


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
            raise SystemExit(f"No summary for block {block} ({path}).")
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


def chart_gas_denom(block_gas: int, gas: Counter, skip: tuple[str, ...]) -> tuple[int, int]:
    """Gas denominator after subtracting the gas attributed to the ignored call opcodes."""
    removed = sum(gas[name] for name in skip)
    return block_gas - removed, removed


def percent(value: int, total: int) -> float:
    return 100.0 * value / total if total else 0.0


def format_percent(pct: float) -> str:
    return f"{pct:.2f}%" if pct < 1 else f"{pct:.1f}%"


def paired_rows(counts: Counter, gas: Counter, top: int, skip: tuple[str, ...]):
    """Top opcodes by gas, highest first, then Other. Skipped opcodes are omitted."""
    skipped = set(skip)
    keep = [name for name, _ in ranked_positive(gas) if name not in skipped][:top]
    rows = [(name, counts[name], gas[name]) for name in keep]
    other_count = sum(value for name, value in counts.items() if name not in keep and name not in skipped)
    other_gas = sum(value for name, value in gas.items() if name not in keep and name not in skipped and value > 0)
    if other_count or other_gas:
        rows.append(("Other", other_count, other_gas))
    return rows


def rgba(color: str, alpha: float) -> tuple[float, float, float, float]:
    color = color.lstrip("#")
    red, green, blue = (int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (red, green, blue, alpha)


def group_geometry() -> tuple[float, float, float]:
    """Bar thickness, gap between the two bars, and gap between groups, in group units."""
    total = 2 * BAR_SIZE + BAR_DISTANCE + GROUP_DISTANCE
    if min(BAR_SIZE, BAR_DISTANCE, GROUP_DISTANCE) <= 0 or total <= 0:
        raise SystemExit("BAR_SIZE, BAR_DISTANCE, and GROUP_DISTANCE must be positive")
    return BAR_SIZE / total, BAR_DISTANCE / total, GROUP_DISTANCE / total


def plot_bars(counts: Counter, gas: Counter, block_gas: int, output: Path, top: int, skip: tuple[str, ...]) -> None:
    """One group per opcode: frequency bar above the gas bar. Each series is scaled to its own maximum."""
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": FONT_SIZE,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.6,
            "hatch.linewidth": 0.6,
        }
    )
    steps = sum(counts.values())
    block_gas, removed = chart_gas_denom(block_gas, gas, skip)
    rows = list(reversed(paired_rows(counts, gas, top, skip)))
    freq = [percent(count, steps) for _, count, _ in rows]
    share = [percent(opcode_gas, block_gas) for _, _, opcode_gas in rows]
    labels = [name for name, _, _ in rows]
    freq_peak = max(freq) if freq else 1.0
    gas_peak = max(share) if share else 1.0
    thickness, bar_gap, group_gap = group_geometry()
    groups = range(len(rows))
    gas_y = [index + group_gap / 2 + thickness / 2 for index in groups]
    freq_y = [center + thickness + bar_gap for center in gas_y]

    fig, ax = plt.subplots(figsize=FIG_SIZE, layout="constrained")
    fig.patch.set_facecolor("white")
    series = (
        (freq_y, freq, freq_peak, FREQ_COLOR, FREQ_HATCH, "Frequency"),
        (gas_y, share, gas_peak, GAS_COLOR, GAS_HATCH, "Gas"),
    )
    for centers, values, peak, color, hatch, _label in series:
        ax.barh(
            centers,
            [value / peak for value in values],
            height=thickness,
            facecolor=rgba(color, BAR_ALPHA),
            edgecolor=color,
            hatch=hatch,
            linewidth=0.4,
        )
    for freq_center, gas_center, freq_pct, gas_pct in zip(freq_y, gas_y, freq, share):
        ax.text(freq_pct / freq_peak + 0.02, freq_center, format_percent(freq_pct), va="center", ha="left", color=LABEL_COLOR)
        ax.text(gas_pct / gas_peak + 0.02, gas_center, format_percent(gas_pct), va="center", ha="left", color=LABEL_COLOR)
    ax.set_yticks([index + 0.5 for index in groups], labels)
    ax.set_xlim(0, 1.32)
    ax.set_ylim(0, len(rows))
    ax.tick_params(axis="y", length=0, pad=2)
    ax.tick_params(axis="x", length=0, labelbottom=False)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.set_facecolor("white")
    fig.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, facecolor=rgba(color, BAR_ALPHA), edgecolor=color, hatch=hatch, linewidth=0.4, label=label)
            for _centers, _values, _peak, color, hatch, label in series
        ],
        loc="outside upper center",
        ncol=2,
        frameon=False,
        handlelength=1.6,
        handleheight=0.8,
        columnspacing=1.2,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {output}")
    print(f"  steps {steps}  opcode gas {sum(gas.values())}  denom gas {block_gas}  call gas removed {removed}")


def parse_args():
    parser = argparse.ArgumentParser(description="Opcode frequency and gas bars from an opcode summary.")
    parser.add_argument("--start-block", type=parse_start_blocks, required=True, dest="start_blocks", help="First block, or a comma-separated list of interval starts")
    parser.add_argument("--range", dest="n_blocks", type=int, required=True, help="Blocks per interval")
    parser.add_argument("--summary-dir", default="opcode_summary", help="Directory of per-block block.json summaries")
    parser.add_argument("--call-gas", choices=("own", "inside"), default="own", help="own: each opcode's gas. inside: call gas includes the frame, other opcodes keep depth-1 gas")
    parser.add_argument("--ignore-calls", action="store_true", help="Omit CALL, STATICCALL, DELEGATECALL, and CALLCODE, and subtract their gas from the denominator")
    parser.add_argument("--top", type=int, default=10, help="Opcodes with the most gas; the rest are Other")
    parser.add_argument("-o", "--output", required=True, help="PDF output path")
    args = parser.parse_args()
    if args.n_blocks < 1:
        parser.error("--range must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    return args


def main():
    args = parse_args()
    blocks = interval_blocks(args.start_blocks, args.n_blocks)
    summary = load_summary(Path(args.summary_dir), blocks, args.call_gas)
    if not summary["counts"]:
        print("No opcode steps found", file=sys.stderr)
        return 1
    skip = IGNORED_CALLS if args.ignore_calls else ()
    print(f"  txs {summary['analysis_txs']}  blocks {len(blocks)}  call-gas {args.call_gas}")
    plot_bars(summary["counts"], summary["gas"], summary["denom"], Path(args.output), args.top, skip)
    print(f"transactions {summary['all_txs']}  gas used {summary['all_gas']}")
    print(f"analysis transactions {summary['analysis_txs']}  analysis gas {summary['denom']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
