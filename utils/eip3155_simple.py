#!/usr/bin/env python3
"""
Simple EIP-3155 converter from cast -t output format.

Uses both trace lines and arena JSON to get accurate gas values,
since cast -t has incorrect gas when depth changes (after RETURN, REVERT, etc.).
"""

import json
import re
from typing import List, Dict, Any, Tuple, Optional


def parse_trace_line(line: str) -> Dict[str, Any] | None:
    """
    Parse a single trace line from cast -t output.

    Format: depth:1, PC:0, gas:0x89f5(35317), OPCODE: "PUSH1"(96)  refund:0x0(0) Stack:[], Data size:0
    """
    line = line.strip()

    # Skip lines that don't match trace format
    if not line or not line.startswith("depth:"):
        return None

    try:
        # Parse depth
        depth_match = re.search(r"depth:(\d+)", line)
        depth = int(depth_match.group(1)) if depth_match else 1

        # Parse PC
        pc_match = re.search(r"PC:(\d+)", line)
        pc = int(pc_match.group(1)) if pc_match else 0

        # Parse gas (hex value before parenthesis)
        gas_match = re.search(r"gas:(0x[0-9a-f]+)", line, re.IGNORECASE)
        gas = gas_match.group(1) if gas_match else "0x0"

        # Parse opcode number (in parenthesis after OPCODE)
        op_match = re.search(r'OPCODE:\s*"[^"]+"\((\d+)\)', line)
        op = int(op_match.group(1)) if op_match else 0

        # Parse refund (hex value before parenthesis)
        refund_match = re.search(r"refund:(0x[0-9a-f]+)", line, re.IGNORECASE)
        refund = int(refund_match.group(1), 16) if refund_match else 0

        # Parse stack
        stack_match = re.search(r"Stack:\[([^\]]*)\]", line)
        stack = []
        if stack_match:
            stack_str = stack_match.group(1).strip()
            if stack_str:
                # Split by comma and clean up each value
                stack = [hex(int(s.strip())) for s in stack_str.split(",") if s.strip()]

        # Parse data size (this becomes memSize)
        data_size_match = re.search(r"Data size:\s*(\d+)", line)
        mem_size = int(data_size_match.group(1)) if data_size_match else 0

        return {"depth": depth, "pc": pc, "gas": gas, "op": op, "refund": refund, "stack": stack, "memSize": mem_size}
    except Exception:
        # Skip lines that can't be parsed
        return None


def build_gas_lookup_from_arena(arena: List[Dict[str, Any]]) -> tuple[Dict[Tuple, int], Dict[Tuple, int]]:
    """
    Build lookup dictionaries from arena JSON.

    Returns:
        (gas_lookup, gas_cost_lookup) where:
        - gas_lookup: (depth, pc, op, stack_tuple) -> gas_remaining
        - gas_cost_lookup: (depth, pc, op, stack_tuple) -> gas_cost

    This provides accurate gas values from arena to correct cast -t bugs.
    Uses full stack to ensure precise matching.
    """
    gas_lookup = {}
    gas_cost_lookup = {}

    for node in arena:
        trace = node.get("trace")
        if not trace:
            continue

        steps = trace.get("steps", [])
        for step in steps:
            depth = step.get("depth", 1)
            pc = step.get("pc", 0)
            op = step.get("op", 0)
            stack = step.get("stack", [])
            gas_remaining = step.get("gas_remaining", 0)
            gas_cost = step.get("gas_cost", 0)

            # Convert stack to tuple for hashability
            stack_tuple = tuple(stack)
            key = (depth, pc, op, stack_tuple)
            gas_lookup[key] = gas_remaining
            gas_cost_lookup[key] = gas_cost

    return gas_lookup, gas_cost_lookup


def convert_trace_lines_to_eip3155(
    trace_lines: List[str], arena: Optional[List[Dict[str, Any]]] = None, include_opname: bool = False
) -> List[Dict[str, Any]]:
    """
    Convert trace lines from cast -t output to EIP-3155 format.

    Args:
        trace_lines: List of trace lines from cast -t output
        arena: Optional arena data for gas correction when depth changes
        include_opname: Whether to include opName field (default: False for CuEVM compatibility)

    Returns:
        List of EIP-3155 formatted steps

    Note:
        Uses arena gas values for instructions after depth changes (RETURN, REVERT, STOP, etc.)
        because cast -t has incorrect gas in these cases.
    """
    # Build gas lookup from arena (provides accurate gas values)
    if arena:
        arena_gas, arena_gas_cost = build_gas_lookup_from_arena(arena)
    else:
        arena_gas, arena_gas_cost = {}, {}

    # Load opcode reference if needed
    opcode_map = None
    if include_opname:
        from .opcodes import OPCODE_MAP

        opcode_map = OPCODE_MAP

    # First pass: parse all trace lines
    parsed_steps = []
    for line in trace_lines:
        step = parse_trace_line(line)
        if step:
            parsed_steps.append(step)

    # Second pass: build EIP-3155 steps with corrected gas values
    eip3155_steps = []
    for i, step in enumerate(parsed_steps):
        # Use gas from trace line by default
        current_gas = int(step["gas"], 16)

        # Check if depth decreased from previous step (call/create returned)
        # This happens for RETURN, REVERT, STOP, SELFDESTRUCT, or running out of gas
        if i > 0:
            prev_step = parsed_steps[i - 1]
            # If depth decreased, this is first instruction after a call/create - use arena gas
            if step["depth"] < prev_step["depth"]:
                stack_tuple = tuple(step["stack"])
                lookup_key = (step["depth"], step["pc"], step["op"], stack_tuple)
                if lookup_key in arena_gas:
                    current_gas = arena_gas[lookup_key]

        # Calculate gasCost
        stack_tuple = tuple(step["stack"])
        lookup_key = (step["depth"], step["pc"], step["op"], stack_tuple)

        # Check if next step has depth decrease (current op exits call/create)
        # Use arena gas_cost for these ops (RETURN, REVERT, STOP, etc.)
        has_depth_decrease = False
        if i + 1 < len(parsed_steps):
            next_step = parsed_steps[i + 1]
            has_depth_decrease = next_step["depth"] < step["depth"]

        if has_depth_decrease and lookup_key in arena_gas_cost:
            # Use arena gas_cost for ops that exit call/create
            gas_cost = arena_gas_cost[lookup_key]
        elif i + 1 < len(parsed_steps):
            # Normal case: calculate from gas difference
            next_step = parsed_steps[i + 1]
            next_gas = int(next_step["gas"], 16)

            # If next step has depth decrease, use arena gas for it
            if next_step["depth"] < step["depth"]:
                next_stack_tuple = tuple(next_step["stack"])
                next_lookup_key = (next_step["depth"], next_step["pc"], next_step["op"], next_stack_tuple)
                if next_lookup_key in arena_gas:
                    next_gas = arena_gas[next_lookup_key]

            gas_cost = current_gas - next_gas
        else:
            # Last step
            gas_cost = 0

        # Build EIP-3155 step
        eip3155_step = {
            "pc": step["pc"],
            "op": step["op"],
            "gas": hex(current_gas),
            "gasCost": hex(gas_cost),
            "memSize": step["memSize"],
            "stack": step["stack"],
            "depth": step["depth"],
            "refund": step["refund"],
        }

        # Add opName if requested
        if include_opname and opcode_map:
            op_hex = hex(step["op"])[2:].upper()
            eip3155_step["opName"] = opcode_map.get(op_hex, f"UNKNOWN_{op_hex}")

        eip3155_steps.append(eip3155_step)

    return eip3155_steps


def save_trace_lines_as_eip3155(
    trace_lines: List[str], output_file: str, arena: Optional[List[Dict[str, Any]]] = None, include_opname: bool = False
) -> int:
    """
    Convert trace lines and save as EIP-3155 trace file.

    Args:
        trace_lines: List of trace lines from cast -t output
        output_file: Path to output trace file
        arena: Optional arena data for accurate gas values
        include_opname: Whether to include opName field (default: False)

    Returns:
        Number of steps converted
    """
    eip3155_steps = convert_trace_lines_to_eip3155(trace_lines, arena, include_opname)

    with open(output_file, "w", encoding="utf-8") as f:
        for step in eip3155_steps:
            f.write(json.dumps(step) + "\n")

    return len(eip3155_steps)
