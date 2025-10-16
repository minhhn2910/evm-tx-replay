"""
Batch collectors for processing multiple transactions.

Supports collecting from text files and entire blocks.
"""

import os
import time
from utils.tools import is_tx
from utils.collect_env import cast_block_run
from utils.plot_block_stats import generate_statistics
from .transaction import collect_transaction_data


def collect_from_file(file_name, output_folder=None, overwrite=False, max_attempts=3, endpoint="http://localhost:8545"):
    """
    Collect data for all transactions listed in a text file.

    Args:
        file_name: Path to text file containing transaction hashes (one per line)
        output_folder: Output folder (default: derived from file_name)
        overwrite: Whether to overwrite existing results
        max_attempts: Maximum retry attempts per transaction
        endpoint: RPC endpoint URL

    Returns:
        Dictionary with timing statistics
    """
    # Use filename (without extension) as default folder
    if output_folder is None:
        output_folder = file_name.rsplit(".", 1)[0] if "." in file_name else file_name

    if overwrite and os.path.exists(output_folder):
        print(f"Deleting existing folder to overwrite {output_folder}")
        os.system(f"rm -rf {output_folder}")

    # Read transaction hashes from file
    with open(file_name, "r", encoding="utf-8") as file:
        tx_list = [line.strip() for line in file.readlines() if is_tx(line.strip())]

    start_time = time.time()
    total_transactions = len(tx_list)
    successful = 0

    print(f"Processing {total_transactions} transactions from {file_name}")

    for tx in tx_list:
        attempts = 0
        while attempts < max_attempts:
            try:
                collect_transaction_data(tx, output_folder, overwrite=False, endpoint=endpoint)
                print(f"Success: collected transaction {tx}")
                successful += 1
                break
            except Exception as e:
                attempts += 1
                print(f"Failure: attempt {attempts} failed for transaction {tx}: {e}")
                if attempts < max_attempts:
                    time.sleep(2)
                else:
                    print(f"Failure: skipping transaction {tx} after {max_attempts} failed attempts")

    end_time = time.time()
    total_time = end_time - start_time
    avg_time_per_tx = total_time / total_transactions if total_transactions > 0 else 0

    stats = {
        "total_time": total_time,
        "avg_time_per_tx": avg_time_per_tx,
        "total_transactions": total_transactions,
        "successful": successful,
        "failed": total_transactions - successful,
    }

    print("\nCollection complete:")
    print(f"  Total time: {total_time:.2f} seconds")
    print(f"  Average time per transaction: {avg_time_per_tx:.2f} seconds")
    print(f"  Successful: {successful}/{total_transactions}")

    return stats


def collect_from_block(
    block_number,
    output_folder="block_result",
    overwrite=False,
    max_attempts=3,
    endpoint="http://localhost:8545",
):
    """
    Collect data for all transactions from a specific block.

    Args:
        block_number: The block number to collect transactions from
        output_folder: Base output folder
        overwrite: Whether to overwrite existing results
        max_attempts: Maximum retry attempts per transaction
        endpoint: RPC endpoint URL

    Returns:
        Dictionary with timing statistics
    """
    # Get all transactions from the block
    block_data = cast_block_run(block_number, endpoint)
    block_tx_list = block_data.get("transactions", [])

    os.makedirs(output_folder, exist_ok=True)
    block_folder_prefix = f"{output_folder}/{block_number}"

    # Create result directory if it doesn't exist
    if overwrite and os.path.exists(block_folder_prefix):
        print(f"Deleting existing block folder to overwrite {block_number}")
        os.system(f"rm -rf {block_folder_prefix}")

    start_time = time.time()
    total_transactions = len(block_tx_list)
    successful = 0

    print(f"Processing {total_transactions} transactions from block {block_number}")

    for tx in block_tx_list:
        attempts = 0
        while attempts < max_attempts:
            try:
                collect_transaction_data(tx, block_folder_prefix, overwrite=False, endpoint=endpoint)
                print(f"Success: collected transaction {tx} from block {block_number}")
                successful += 1
                break
            except Exception as e:
                attempts += 1
                print(f"Failure: attempt {attempts} failed for transaction {tx}: {e}")
                if attempts < max_attempts:
                    time.sleep(2)
                else:
                    print(f"Failure: skipping transaction {tx} after {max_attempts} failed attempts")

    end_time = time.time()
    total_time = end_time - start_time
    avg_time_per_tx = total_time / total_transactions if total_transactions > 0 else 0

    stats = {
        "total_time": total_time,
        "avg_time_per_tx": avg_time_per_tx,
        "total_transactions": total_transactions,
        "successful": successful,
        "failed": total_transactions - successful,
    }

    print("\nBlock collection complete:")
    print(f"  Total time: {total_time:.2f} seconds")
    print(f"  Average time per transaction: {avg_time_per_tx:.2f} seconds")
    print(f"  Successful: {successful}/{total_transactions}")

    # Generate block statistics
    try:
        generate_statistics([block_number], output_folder)
        print("  Statistics generated successfully")
    except Exception as e:
        print(f"  Warning: Failed to generate statistics: {e}")

    return stats
