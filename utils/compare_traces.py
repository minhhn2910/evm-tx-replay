#!/usr/bin/env python3
"""
Simple utility to compare two EIP-3155 trace files.

Compares traces line by line, checking pc, opcode, gas, and stack content.
Reports the first mismatch found.
"""

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


def compare_traces(trace1: Dict[str, Any], trace2: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Compare two trace steps.

    Returns:
        (matches, error_message) tuple
    """
    # Compare PC
    if normalize_value(trace1.get("pc", 0)) != normalize_value(trace2.get("pc", 0)):
        return False, f"PC mismatch: {trace1.get('pc')} vs {trace2.get('pc')}"

    # Compare opcode
    if normalize_value(trace1.get("op", 0)) != normalize_value(trace2.get("op", 0)):
        return False, f"Opcode mismatch: {trace1.get('op')} vs {trace2.get('op')}"

    # Compare gas
    if normalize_value(trace1.get("gas", "0x0")) != normalize_value(trace2.get("gas", "0x0")):
        return False, f"Gas mismatch: {trace1.get('gas')} vs {trace2.get('gas')}"

    # Compare stack
    stack1 = normalize_stack(trace1.get("stack", []))
    stack2 = normalize_stack(trace2.get("stack", []))
    if stack1 != stack2:
        return False, f"Stack mismatch: {len(stack1)} items vs {len(stack2)} items"

    return True, ""


def main():
    """Main comparison function."""
    if len(sys.argv) != 3:
        print("Usage: python compare_traces.py <trace_file1> <trace_file2>")
        print("\nExample:")
        print("  python compare_traces.py gethTrace.log txTraceEIP3155.json")
        sys.exit(1)

    file1, file2 = sys.argv[1], sys.argv[2]

    print("Loading traces...")
    print(f"  File 1: {file1}")
    print(f"  File 2: {file2}")

    traces1, skipped1 = load_trace(file1)
    traces2, skipped2 = load_trace(file2)

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

    for i in range(min_length):
        matches, error = compare_traces(traces1[i], traces2[i])
        if not matches:
            all_match = False
            print(f"\n❌ MISMATCH at step {i + 1} (index {i}):")
            print(f"   {error}")
            print(f"\n   File 1: {file1}")
            print(f"   {json.dumps(traces1[i], indent=4)}")
            print(f"\n   File 2: {file2}")
            print(f"   {json.dumps(traces2[i], indent=4)}")
            break

    if all_match:
        if len(traces1) == len(traces2):
            print(f"\n✅ SUCCESS: All {len(traces1)} trace steps match perfectly!")
        else:
            print(f"\n⚠️  First {min_length} steps match, but trace lengths differ.")
            if len(traces1) > len(traces2):
                print(f"   File 1 has {len(traces1) - len(traces2)} extra steps")
            else:
                print(f"   File 2 has {len(traces2) - len(traces1)} extra steps")

    return 0 if all_match and len(traces1) == len(traces2) else 1


if __name__ == "__main__":
    sys.exit(main())
