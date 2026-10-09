"""
Fast collectors built on the node's debug_trace* tracers.

Captures the call tree plus storage reads and writes instead of the full
EIP-3155 opcode trace. Reads come from prestateTracer (touched slots);
writes come from the same tracer in diffMode. A whole block is three RPC
calls rather than one cast process per transaction.
"""

import json
import os
import shutil
import time
from utils.rpc import rpc_call
from utils.tools import convert_hexbytes_to_str, report_timing

CALL_TRACER = {"tracer": "callTracer", "tracerConfig": {"withLog": True}}
PRESTATE_TRACER = {"tracer": "prestateTracer"}
DIFF_TRACER = {"tracer": "prestateTracer", "tracerConfig": {"diffMode": True}}


def _as_hex(value):
    if value is None:
        return "0x0"
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if hasattr(value, "hex") and callable(value.hex) and not isinstance(value, str):
        text = value.hex()
    else:
        text = str(value)
    if not text.startswith(("0x", "0X")):
        text = "0x" + text
    return text.lower()


def _storage(account):
    if not account:
        return {}
    return {_as_hex(slot): _as_hex(value) for slot, value in (account.get("storage") or {}).items()}


def _accounts(mapping):
    """Address -> storage, keyed by lowercase hex so pre/post/prestate align."""
    return {_as_hex(address): _storage(account) for address, account in (mapping or {}).items()}


def _read_records(entries):
    return [
        {"address": address, "slot": slot, "value": value}
        for (address, slot), value in sorted(entries.items())
    ]


def _write_records(entries):
    return [
        {"address": address, "slot": slot, "old_value": old_value, "value": value}
        for (address, slot), (old_value, value) in sorted(entries.items())
    ]


def classify_state(prestate, diff):
    """Split tracer output into read-only slots and written slots.

    Reads: slots in default prestate that do not appear in the diff. Value is
    the pre-tx value that was loaded. Writes: slots in the diff. `old_value`
    is the pre-tx value (0x0 if the slot was created); `value` is the post-tx
    value (0x0 if the slot was cleared).
    """
    writes = {}
    pre = _accounts((diff or {}).get("pre"))
    post = _accounts((diff or {}).get("post"))
    touched = _accounts(prestate)
    for address in set(pre) | set(post):
        before = pre.get(address) or {}
        after = post.get(address) or {}
        prior = touched.get(address) or {}
        for slot in set(before) | set(after):
            # New slot/account: omitted from diff pre and from prestate → 0x0
            old_value = before.get(slot, prior.get(slot, "0x0"))
            writes[(address, slot)] = (old_value, after.get(slot, "0x0"))

    reads = {}
    for address, slots in touched.items():
        for slot, value in slots.items():
            key = (address, slot)
            if key not in writes:
                reads[key] = value

    return _read_records(reads), _write_records(writes)


def save_fast_results(tx_folder_prefix, calls, reads, writes, overwrite=False):
    """Write the call tree and storage read/write lists for one transaction."""
    if overwrite and os.path.exists(tx_folder_prefix):
        shutil.rmtree(tx_folder_prefix)

    os.makedirs(tx_folder_prefix, exist_ok=True)

    for file_name, data in (
        ("txCalls.json", calls),
        ("txStateRead.json", reads),
        ("txStateWrite.json", writes),
    ):
        with open(f"{tx_folder_prefix}/{file_name}", "w", encoding="utf-8") as json_file:
            json.dump(convert_hexbytes_to_str(data), json_file, indent=2)


def collect_fast_transaction(
    transaction_hash, output_folder="result", overwrite=False, endpoint="http://localhost:8545"
):
    """
    Collect the call tree and storage reads/writes for a single transaction.

    Args:
        transaction_hash: The transaction hash to collect data for
        output_folder: Base folder for output files
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        True if successful
    """
    print(f"Collecting call tree and state read/write for {transaction_hash}")
    calls = rpc_call(endpoint, "debug_traceTransaction", [transaction_hash, CALL_TRACER])
    prestate = rpc_call(endpoint, "debug_traceTransaction", [transaction_hash, PRESTATE_TRACER])
    diff = rpc_call(endpoint, "debug_traceTransaction", [transaction_hash, DIFF_TRACER])
    reads, writes = classify_state(prestate, diff)
    save_fast_results(f"{output_folder}/{transaction_hash}", calls, reads, writes, overwrite)

    return True


def collect_fast_block(block_number, output_folder="block_result", overwrite=False, endpoint="http://localhost:8545"):
    """
    Collect call trees and storage reads/writes for an entire block.

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
    prestates = rpc_call(endpoint, "debug_traceBlockByNumber", [block, PRESTATE_TRACER])
    diffs = rpc_call(endpoint, "debug_traceBlockByNumber", [block, DIFF_TRACER])

    # Tracers report results per transaction hash, but not necessarily in the same order
    prestate_by_hash = {entry["txHash"]: entry.get("result") for entry in prestates}
    diff_by_hash = {entry["txHash"]: entry.get("result") for entry in diffs}
    for entry in calls:
        tx_hash = entry["txHash"]
        reads, writes = classify_state(prestate_by_hash.get(tx_hash), diff_by_hash.get(tx_hash))
        save_fast_results(f"{block_folder_prefix}/{tx_hash}", entry.get("result"), reads, writes)

    return report_timing(start_time, len(calls), len(calls), f"Block {block_number} fast collection")
