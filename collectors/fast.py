"""
Fast collectors built on the node's debug_trace* tracers.

Captures the call tree and the state diff instead of the full EIP-3155 opcode
trace, which avoids running cast and is an order of magnitude cheaper. A whole
block costs two RPC calls rather than one cast process per transaction.
"""

import json
import os
import shutil
import time
from utils.rpc import rpc_call
from utils.tools import convert_hexbytes_to_str, report_timing

CALL_TRACER = {"tracer": "callTracer", "tracerConfig": {"withLog": True}}
DIFF_TRACER = {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}


def save_fast_results(tx_folder_prefix, calls, state_diff, overwrite=False):
    """Write the call tree and state diff for one transaction."""
    if overwrite and os.path.exists(tx_folder_prefix):
        shutil.rmtree(tx_folder_prefix)

    os.makedirs(tx_folder_prefix, exist_ok=True)

    for file_name, data in (("txCalls.json", calls), ("txStateDiff.json", state_diff)):
        with open(f"{tx_folder_prefix}/{file_name}", "w", encoding="utf-8") as json_file:
            json.dump(convert_hexbytes_to_str(data), json_file, indent=2)


def collect_fast_transaction(
    transaction_hash, output_folder="result", overwrite=False, endpoint="http://localhost:8545"
):
    """
    Collect the call tree and state diff for a single transaction.

    Args:
        transaction_hash: The transaction hash to collect data for
        output_folder: Base folder for output files
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        True if successful
    """
    print(f"Collecting call tree and state diff for {transaction_hash}")
    calls = rpc_call(endpoint, "debug_traceTransaction", [transaction_hash, CALL_TRACER])
    state_diff = rpc_call(endpoint, "debug_traceTransaction", [transaction_hash, DIFF_TRACER])
    save_fast_results(f"{output_folder}/{transaction_hash}", calls, state_diff, overwrite)

    return True


def collect_fast_block(block_number, output_folder="block_result", overwrite=False, endpoint="http://localhost:8545"):
    """
    Collect call trees and state diffs for an entire block in two RPC calls.

    Args:
        block_number: The block number to collect transactions from
        output_folder: Base output folder
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        Dictionary with timing statistics
    """
    block_folder_prefix = f"{output_folder}/{block_number}"
    if overwrite and os.path.exists(block_folder_prefix):
        print(f"Deleting existing block folder to overwrite {block_number}")
        shutil.rmtree(block_folder_prefix)

    start_time = time.time()
    block = hex(block_number)
    print(f"Tracing block {block_number}")
    calls = rpc_call(endpoint, "debug_traceBlockByNumber", [block, CALL_TRACER])
    diffs = rpc_call(endpoint, "debug_traceBlockByNumber", [block, DIFF_TRACER])

    # Both tracers report results per transaction hash, but not necessarily in the same order
    diff_by_hash = {entry["txHash"]: entry.get("result") for entry in diffs}
    for entry in calls:
        tx_hash = entry["txHash"]
        save_fast_results(f"{block_folder_prefix}/{tx_hash}", entry.get("result"), diff_by_hash.get(tx_hash))

    return report_timing(start_time, len(calls), len(calls), f"Block {block_number} fast collection")
