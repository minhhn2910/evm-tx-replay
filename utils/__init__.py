"""
Utility functions for CuEVM data collection.

This package contains helper functions for working with Ethereum traces,
EIP-3155 format conversion, and blockchain data collection.
"""

from .tools import (
    is_tx,
    add_to_dict,
    strict_extend,
    extend_dict,
    convert_hexbytes_to_str,
    count_and_sort,
    make_hex_even,
    remove_extra_zeros,
    get_statistics,
    cast_trace_run,
    cast_trace_run_with_steps,
)
from .opcodes import OPCODE_MAP, get_opcode_name
from .eip3155_simple import (
    parse_trace_line,
    convert_trace_lines_to_eip3155,
    save_trace_lines_as_eip3155,
)
from .compare_traces import load_trace, compare_traces
from .collect_env import collect_env, cast_block_run
from .collect_pre import collect_pre
from .collect_transaction import collect_transaction

__all__ = [
    # Tools
    "is_tx",
    "add_to_dict",
    "strict_extend",
    "extend_dict",
    "convert_hexbytes_to_str",
    "count_and_sort",
    "make_hex_even",
    "remove_extra_zeros",
    "get_statistics",
    "cast_trace_run",
    "cast_trace_run_with_steps",
    # Opcodes
    "OPCODE_MAP",
    "get_opcode_name",
    # EIP-3155
    "parse_trace_line",
    "convert_trace_lines_to_eip3155",
    "save_trace_lines_as_eip3155",
    # Compare traces
    "load_trace",
    "compare_traces",
    # Collection
    "collect_env",
    "collect_pre",
    "collect_transaction",
    "cast_block_run",
]
