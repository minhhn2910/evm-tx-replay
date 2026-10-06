"""
Batch collectors for processing multiple transactions.

Supports collecting from text files and entire blocks, optionally in parallel.
"""

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from utils.tools import is_tx, report_timing
from utils.collect_env import cast_block_run
from .transaction import collect_transaction_data
from .fast import collect_fast_transaction


def make_collector(output_folder, endpoint, fast=False):
    """Return a one-argument collector that writes into output_folder."""
    collect = collect_fast_transaction if fast else collect_transaction_data
    return lambda tx: collect(tx, output_folder, overwrite=False, endpoint=endpoint)


def collect_with_retry(tx, collect, max_attempts):
    """Collect a single transaction, retrying on failure."""
    for attempt in range(1, max_attempts + 1):
        try:
            collect(tx)
            print(f"Success: collected transaction {tx}")
            return True
        except Exception as e:
            print(f"Failure: attempt {attempt} failed for transaction {tx}: {e}")
            if attempt < max_attempts:
                time.sleep(2)

    print(f"Failure: skipping transaction {tx} after {max_attempts} failed attempts")
    return False


def run_batch(tx_list, collect, label, max_attempts=3, jobs=1):
    """
    Collect every transaction in tx_list, in parallel when jobs > 1.

    Args:
        tx_list: Transaction hashes to collect
        collect: One-argument collector, as built by make_collector
        label: Name used in the summary output
        max_attempts: Maximum retry attempts per transaction
        jobs: Number of transactions to collect concurrently

    Returns:
        Dictionary with timing statistics
    """
    start_time = time.time()
    print(f"Processing {len(tx_list)} transactions with {jobs} job(s)")

    def collect_one(tx):
        return collect_with_retry(tx, collect, max_attempts)

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(collect_one, tx_list))
    else:
        results = [collect_one(tx) for tx in tx_list]

    return report_timing(start_time, len(tx_list), sum(results), label)


def collect_from_file(
    file_name,
    output_folder=None,
    overwrite=False,
    max_attempts=3,
    endpoint="http://localhost:8545",
    jobs=1,
    fast=False,
):
    """
    Collect data for all transactions listed in a text file.

    Args:
        file_name: Path to text file containing transaction hashes (one per line)
        output_folder: Output folder (default: derived from file_name)
        overwrite: Whether to overwrite existing results
        max_attempts: Maximum retry attempts per transaction
        endpoint: RPC endpoint URL
        jobs: Number of transactions to collect concurrently
        fast: Collect call tree and state diff instead of an EIP-3155 trace

    Returns:
        Dictionary with timing statistics
    """
    # Use filename (without extension) as default folder
    if output_folder is None:
        output_folder = file_name.rsplit(".", 1)[0] if "." in file_name else file_name

    if overwrite and os.path.exists(output_folder):
        print(f"Deleting existing folder to overwrite {output_folder}")
        shutil.rmtree(output_folder)

    # Read transaction hashes from file
    with open(file_name, "r", encoding="utf-8") as file:
        tx_list = [line.strip() for line in file if is_tx(line.strip())]

    collect = make_collector(output_folder, endpoint, fast)

    return run_batch(tx_list, collect, f"Collection from {file_name}", max_attempts, jobs)


def collect_from_block(
    block_number,
    output_folder="block_result",
    overwrite=False,
    max_attempts=3,
    endpoint="http://localhost:8545",
    jobs=1,
    fast=False,
):
    """
    Collect data for all transactions from a specific block.

    Args:
        block_number: The block number to collect transactions from
        output_folder: Base output folder
        overwrite: Whether to overwrite existing results
        max_attempts: Maximum retry attempts per transaction
        endpoint: RPC endpoint URL
        jobs: Number of transactions to collect concurrently
        fast: Collect call tree and state diff instead of an EIP-3155 trace

    Returns:
        Dictionary with timing statistics
    """
    # Get all transactions from the block
    block_tx_list = cast_block_run(block_number, endpoint).get("transactions", [])

    os.makedirs(output_folder, exist_ok=True)
    block_folder_prefix = f"{output_folder}/{block_number}"

    # Create result directory if it doesn't exist
    if overwrite and os.path.exists(block_folder_prefix):
        print(f"Deleting existing block folder to overwrite {block_number}")
        shutil.rmtree(block_folder_prefix)

    collect = make_collector(block_folder_prefix, endpoint, fast)

    return run_batch(block_tx_list, collect, f"Block {block_number} collection", max_attempts, jobs)
