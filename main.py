#!/usr/bin/env python3
"""
CuEVM Data Collection Tool

A unified CLI tool for collecting Ethereum transaction data from mainnet.
Supports collecting environment information, transaction statistics, and batch processing.
"""

import sys
import argparse
from collectors import collect_transaction_data, collect_multiple_transactions, collect_from_file, collect_from_block
from collectors.envinfo import save_envinfo
from collectors.txstats import save_statistics


def setup_parser():
    """Setup the argument parser with simple flags."""
    parser = argparse.ArgumentParser(
        description="Tx Extraction Tool - Collect Ethereum transaction data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Collect transaction data
  python main.py --tx 0x33f782f87badf5f6ac56ae278e911742dae80b9fbaa2f9c0755c32d1a79966a6
  
  # With custom endpoint (auto-adds http://)
  python main.py --endpoint localhost:8545 --tx 0x33f782f...
  
  # Multiple transactions (comma-separated)
  python main.py --tx 0xabc...,0xdef...,0x123...
  
  # From text file
  python main.py --file transactions.txt
  
  # Entire block
  python main.py --block 18000000
  
  # Only environment info
  python main.py --env 0x33f782f...
  
  # Only statistics
  python main.py --stats 0x33f782f...
        """,
    )

    # Global options
    parser.add_argument(
        "--endpoint",
        default="http://localhost:8545",
        help="RPC endpoint URL (default: localhost:8545, auto-adds http:// if missing)",
    )
    parser.add_argument("-o", "--output", help="Output folder (default varies by command)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing results")
    parser.add_argument(
        "--max-attempts", type=int, default=3, help="Maximum retry attempts for batch/block operations (default: 3)"
    )

    # Command options (mutually exclusive)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument(
        "--tx", "--transaction", dest="tx", metavar="HASH", help="Transaction hash or comma-separated list"
    )
    commands.add_argument(
        "--file", "--batch", dest="file", metavar="FILE", help="Text file with transaction hashes (one per line)"
    )
    commands.add_argument("--block", type=int, metavar="NUMBER", help="Block number to collect all transactions from")
    commands.add_argument(
        "--env", "--envinfo", dest="env", metavar="HASH", help="Collect only environment info for transaction"
    )
    commands.add_argument("--stats", metavar="HASH", help="Collect only statistics for transaction")

    return parser


def normalize_endpoint(endpoint):
    """Normalize RPC endpoint by adding http:// if missing."""
    if endpoint and not endpoint.startswith(("http://", "https://", "ws://", "wss://")):
        return f"http://{endpoint}"
    return endpoint


def main():
    """Main entry point for the CLI."""
    parser = setup_parser()
    args = parser.parse_args()

    # Normalize endpoint
    args.endpoint = normalize_endpoint(args.endpoint)

    try:
        # Handle transaction collection
        if args.tx:
            output = args.output or "result"
            if "," in args.tx:
                # Multiple transactions
                count = collect_multiple_transactions(
                    args.tx, output_folder=output, overwrite=args.overwrite, endpoint=args.endpoint
                )
                print(f"\nSuccessfully collected {count} transaction(s)")
            else:
                # Single transaction
                collect_transaction_data(
                    args.tx, output_folder=output, overwrite=args.overwrite, endpoint=args.endpoint
                )
                print(f"\nSuccessfully collected transaction {args.tx}")

        # Handle file/batch collection
        elif args.file:
            stats = collect_from_file(
                args.file,
                output_folder=args.output,
                overwrite=args.overwrite,
                max_attempts=args.max_attempts,
                endpoint=args.endpoint,
            )
            print(f'\nBatch collection completed: {stats["successful"]}/{stats["total_transactions"]} successful')

        # Handle block collection
        elif args.block:
            output = args.output or "block_result"
            stats = collect_from_block(
                args.block,
                output_folder=output,
                overwrite=args.overwrite,
                max_attempts=args.max_attempts,
                endpoint=args.endpoint,
            )
            print(f'\nBlock collection completed: {stats["successful"]}/{stats["total_transactions"]} successful')

        # Handle environment info only
        elif args.env:
            output = args.output or "envInfoResult"
            save_envinfo(args.env, output_folder=output, overwrite=args.overwrite, endpoint=args.endpoint)
            print(f"\nSuccessfully collected environment info for {args.env}")

        # Handle statistics only
        elif args.stats:
            output = args.output or "statsResult"
            save_statistics(args.stats, output_folder=output, overwrite=args.overwrite, endpoint=args.endpoint)
            print(f"\nSuccessfully collected statistics for {args.stats}")

        return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 130

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
