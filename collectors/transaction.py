"""
Transaction collector for complete transaction data.

Combines environment information and statistics collection.
"""

import os
import json
from utils.tools import is_tx, convert_hexbytes_to_str, cast_trace_run_with_steps
from utils.eip3155_simple import save_trace_lines_as_eip3155
from .envinfo import collect_envinfo


def collect_transaction_data(
    transaction_hash, output_folder="result", overwrite=False, endpoint="http://localhost:8545"
):
    """
    Collect complete data (envinfo + stats + EIP-3155 trace) for a transaction.

    Args:
        transaction_hash: The transaction hash to collect data for
        output_folder: Base folder for output files
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        True if successful
    """
    os.makedirs(output_folder, exist_ok=True)
    tx_folder_prefix = f"{output_folder}/{transaction_hash}"

    # Create result directory if it doesn't exist
    if overwrite and os.path.exists(tx_folder_prefix):
        print(f"Deleting existing folder to overwrite {transaction_hash}")
        os.system(f"rm -rf {tx_folder_prefix}")

    os.makedirs(tx_folder_prefix, exist_ok=True)

    # Collect environment info
    env_info = collect_envinfo(transaction_hash, endpoint)

    # Collect trace data with ordered steps and arena
    print(f"Collecting trace data for {transaction_hash}")
    trace_lines, arena = cast_trace_run_with_steps(transaction_hash, endpoint)

    # Generate statistics from arena
    from .txstats import collect_lists

    tx_stats = collect_lists(arena)

    # Convert HexBytes to strings before JSON serialization
    env_info = convert_hexbytes_to_str(env_info)
    tx_stats = convert_hexbytes_to_str(tx_stats)

    # Save environment information
    json_string_env = json.dumps(env_info, indent=2)
    with open(f"{tx_folder_prefix}/txTest.json", "w", encoding="utf-8") as json_file:
        json_file.write(json_string_env)

    # Save statistics
    json_string_stats = json.dumps(tx_stats, indent=2)
    with open(f"{tx_folder_prefix}/txStats.json", "w", encoding="utf-8") as json_file:
        json_file.write(json_string_stats)

    # Save EIP-3155 trace from ordered trace lines (without opName for CuEVM compatibility)
    # Pass arena data for accurate gas values (fixes cast -t gas bugs after RETURN)
    print(f"Generating EIP-3155 trace for {transaction_hash}")
    eip3155_file = f"{tx_folder_prefix}/txTraceEIP3155.json"
    step_count = save_trace_lines_as_eip3155(trace_lines, eip3155_file, arena, include_opname=False)
    print(f"Saved {step_count} trace steps to txTraceEIP3155.json")

    return True


def collect_multiple_transactions(tx_hashes, output_folder="result", overwrite=False, endpoint="http://localhost:8545"):
    """
    Collect data for multiple transactions.

    Args:
        tx_hashes: List of transaction hashes or comma-separated string
        output_folder: Base folder for output files
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        Number of successfully processed transactions
    """
    # Handle comma-separated string
    if isinstance(tx_hashes, str):
        if "," in tx_hashes:
            tx_hashes = tx_hashes.split(",")
        else:
            tx_hashes = [tx_hashes]

    # Filter valid transaction hashes
    valid_hashes = [tx.strip() for tx in tx_hashes if is_tx(tx.strip())]

    print(f"Collecting data for {len(valid_hashes)} transactions")

    success_count = 0
    for tx_hash in valid_hashes:
        try:
            print(f"Collecting data for {tx_hash}")
            collect_transaction_data(tx_hash, output_folder, overwrite, endpoint)
            success_count += 1
        except Exception as e:
            print(f"Error collecting data for {tx_hash}: {e}")

    return success_count
