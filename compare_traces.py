#!/usr/bin/env python3
"""
Simple utility to compare two EIP-3155 trace files.

Usage:
    python compare_traces.py <trace_file1> <trace_file2>

Example:
    python compare_traces.py gethTrace.log txTraceEIP3155.json
"""

from utils.compare_traces import main
import sys

if __name__ == "__main__":
    sys.exit(main())
