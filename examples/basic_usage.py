#!/usr/bin/env python3
"""
Basic usage examples for CuEVM Data Collection.

This script demonstrates common usage patterns.
"""

from collectors import collect_transaction_data, collect_from_block
from collectors.envinfo import collect_envinfo
from collectors.txstats import collect_statistics
from utils.compare_traces import load_trace, compare_traces


# Example 1: Collect single transaction
def example_single_transaction():
    """Collect data for a single transaction."""
    tx_hash = "0x8b6b8478f95fde9e00026422772ba24a65a3d7d4f7e00376220aa53308e6b5b0"
    endpoint = "http://localhost:8545"

    print(f"Collecting transaction {tx_hash}")
    collect_transaction_data(tx_hash, output_folder="result", overwrite=False, endpoint=endpoint)
    print("Done!")


# Example 2: Collect only environment info
def example_envinfo_only():
    """Collect only environment information."""
    tx_hash = "0x8b6b8478f95fde9e00026422772ba24a65a3d7d4f7e00376220aa53308e6b5b0"
    endpoint = "http://localhost:8545"

    print(f"Collecting environment info for {tx_hash}")
    env_info = collect_envinfo(tx_hash, endpoint)
    print(f"Block number: {env_info['envinfo']['env']['currentNumber']}")
    print("Done!")


# Example 3: Collect only statistics
def example_stats_only():
    """Collect only transaction statistics."""
    tx_hash = "0x8b6b8478f95fde9e00026422772ba24a65a3d7d4f7e00376220aa53308e6b5b0"
    endpoint = "http://localhost:8545"

    print(f"Collecting statistics for {tx_hash}")
    stats = collect_statistics(tx_hash, endpoint)
    print(f"Total opcodes executed: {sum(stats['opcodes_count'].values())}")
    print(f"Max call depth: {stats['max_depth']}")
    print("Done!")


# Example 4: Collect entire block
def example_block_collection():
    """Collect all transactions from a block."""
    block_number = 18000000
    endpoint = "http://localhost:8545"

    print(f"Collecting block {block_number}")
    result = collect_from_block(
        block_number, output_folder="block_result", overwrite=False, max_attempts=3, endpoint=endpoint
    )
    print(f"Collected {result['successful']}/{result['total_transactions']} transactions")
    print("Done!")


# Example 5: Compare traces
def example_compare_traces():
    """Compare two trace files."""
    trace1_path = "reference_trace.log"
    trace2_path = "result/0x.../txTraceEIP3155.json"

    print("Loading traces...")
    traces1, _ = load_trace(trace1_path)
    traces2, _ = load_trace(trace2_path)

    print(f"Comparing {len(traces1)} vs {len(traces2)} steps...")

    for i in range(min(len(traces1), len(traces2))):
        matches, error = compare_traces(traces1[i], traces2[i])
        if not matches:
            print(f"Mismatch at step {i}: {error}")
            return

    print("All traces match!")


if __name__ == "__main__":
    # Uncomment the example you want to run

    # example_single_transaction()
    # example_envinfo_only()
    # example_stats_only()
    # example_block_collection()
    # example_compare_traces()

    print("Uncomment an example function to run it")
