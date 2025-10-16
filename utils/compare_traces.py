#!/usr/bin/env python3
"""
Simple utility to compare two EIP-3155 trace files.

Compares traces line by line, checking pc, opcode, gas, and stack content.
Reports the first mismatch found.
"""

import argparse
import json
import sys
from typing import List, Dict, Any, Tuple


def load_trace(file_path: str) -> tuple[List[Dict[str, Any]], int]:
    """
    Load trace file (one JSON object per line).

    Returns:
        (traces, skipped_lines) tuple
    """
    traces = []
    skipped = 0
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Only include objects with trace fields (pc, op, gas, etc.)
                if "pc" in obj and "op" in obj:
                    traces.append(obj)
                else:
                    skipped += 1
            except json.JSONDecodeError:
                skipped += 1
    return traces, skipped


def normalize_value(value: Any) -> str:
    """Normalize values for comparison (handle hex with/without 0x prefix)."""
    if isinstance(value, str):
        # Ensure hex values have 0x prefix and are lowercase
        if value.startswith("0x"):
            return value.lower()
        # Try to convert decimal to hex for comparison
        try:
            return hex(int(value)).lower()
        except (ValueError, TypeError):
            return value.lower()
    elif isinstance(value, int):
        return hex(value).lower()
    return str(value).lower()


def normalize_stack(stack: List) -> List[str]:
    """Normalize stack values for comparison."""
    return [normalize_value(v) for v in stack]


def compare_traces(trace1: Dict[str, Any], trace2: Dict[str, Any], skip_gas: bool = False) -> Tuple[bool, str, bool]:
    """
    Compare two trace steps.

    Args:
        trace1: First trace step
        trace2: Second trace step
        skip_gas: If True, skip gas comparison for pass/fail determination

    Returns:
        (matches, error_message, gas_mismatch) tuple
        - matches: True if traces match (ignoring gas if skip_gas=True)
        - error_message: Description of mismatch if any
        - gas_mismatch: True if gas values differ
    """
    gas_mismatch = False

    # Compare PC
    if normalize_value(trace1.get("pc", 0)) != normalize_value(trace2.get("pc", 0)):
        return False, f"PC mismatch: {trace1.get('pc')} vs {trace2.get('pc')}", gas_mismatch

    # Compare opcode
    if normalize_value(trace1.get("op", 0)) != normalize_value(trace2.get("op", 0)):
        return False, f"Opcode mismatch: {trace1.get('op')} vs {trace2.get('op')}", gas_mismatch

    # Check gas (always check for reporting, but only fail if not skipped)
    if normalize_value(trace1.get("gas", "0x0")) != normalize_value(trace2.get("gas", "0x0")):
        gas_mismatch = True
        if not skip_gas:
            return False, f"Gas mismatch: {trace1.get('gas')} vs {trace2.get('gas')}", gas_mismatch

    # Compare stack
    stack1 = normalize_stack(trace1.get("stack", []))
    stack2 = normalize_stack(trace2.get("stack", []))
    if stack1 != stack2:
        return False, f"Stack mismatch: {len(stack1)} items vs {len(stack2)} items", gas_mismatch

    return True, "", gas_mismatch


def main():
    """Main comparison function."""
    parser = argparse.ArgumentParser(
        description="Compare two EIP-3155 trace files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
  python compare_traces.py gethTrace.log txTraceEIP3155.json
  python compare_traces.py --no-gas gethTrace.log txTraceEIP3155.json
        """,
    )
    parser.add_argument("file1", help="First trace file")
    parser.add_argument("file2", help="Second trace file")
    parser.add_argument(
        "--no-gas",
        action="store_true",
        help="Skip gas comparison (only check PC, opcode, and stack)",
    )

    args = parser.parse_args()

    print("Loading traces...")
    print(f"  File 1: {args.file1}")
    print(f"  File 2: {args.file2}")
    if args.no_gas:
        print("  Mode: Skipping gas comparison (checking PC, opcode, stack only)")

    traces1, skipped1 = load_trace(args.file1)
    traces2, skipped2 = load_trace(args.file2)

    print("\nTrace lengths:")
    print(f"  File 1: {len(traces1)} steps", end="")
    if skipped1 > 0:
        print(f" ({skipped1} non-trace lines skipped)", end="")
    print()
    print(f"  File 2: {len(traces2)} steps", end="")
    if skipped2 > 0:
        print(f" ({skipped2} non-trace lines skipped)", end="")
    print()

    if len(traces1) != len(traces2):
        print("\n⚠️  WARNING: Trace lengths differ!")

    print("\nComparing traces...")

    min_length = min(len(traces1), len(traces2))
    all_match = True
    gas_mismatch_count = 0

    for i in range(min_length):
        matches, error, gas_mismatch = compare_traces(traces1[i], traces2[i], skip_gas=args.no_gas)

        if gas_mismatch:
            gas_mismatch_count += 1

        if not matches:
            all_match = False
            print(f"\n❌ MISMATCH at step {i + 1} (index {i}):")
            print(f"   {error}")
            print(f"\n   File 1: {args.file1}")
            print(f"   {json.dumps(traces1[i], indent=4)}")
            print(f"\n   File 2: {args.file2}")
            print(f"   {json.dumps(traces2[i], indent=4)}")
            break

    if all_match:
        if len(traces1) == len(traces2):
            print(f"\n✅ SUCCESS: All {len(traces1)} trace steps match perfectly!")
            if args.no_gas and gas_mismatch_count > 0:
                print(f"   (Note: {gas_mismatch_count} steps had gas mismatches, but were ignored)")
        else:
            print(f"\n⚠️  First {min_length} steps match, but trace lengths differ.")
            if len(traces1) > len(traces2):
                print(f"   File 1 has {len(traces1) - len(traces2)} extra steps")
            else:
                print(f"   File 2 has {len(traces2) - len(traces1)} extra steps")
            if args.no_gas and gas_mismatch_count > 0:
                print(f"   (Note: {gas_mismatch_count} steps had gas mismatches, but were ignored)")

    return 0 if all_match and len(traces1) == len(traces2) else 1


if __name__ == "__main__":
    sys.exit(main())
