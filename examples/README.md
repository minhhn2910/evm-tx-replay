# Examples

This directory contains example scripts demonstrating how to use the CuEVM Data Collection library.

## Basic Usage

See `basic_usage.py` for common usage patterns:

```bash
python examples/basic_usage.py
```

Edit the script to uncomment the example you want to run.

## Available Examples

1. **Single Transaction**: Collect complete data for one transaction
2. **Environment Info Only**: Collect just the environment/state data
3. **Statistics Only**: Collect just execution statistics
4. **Block Collection**: Process all transactions in a block
5. **Trace Comparison**: Validate trace accuracy

## Custom Scripts

You can use these examples as templates for your own data collection scripts.

### Example: Custom Collector

```python
from collectors import collect_transaction_data

# Your transaction hashes
transactions = [
    "0x8b6b8478f95fde9e00026422772ba24a65a3d7d4f7e00376220aa53308e6b5b0",
    "0x33f782f87badf5f6ac56ae278e911742dae80b9fbaa2f9c0755c32d1a79966a6",
]

# Collect each transaction
for tx_hash in transactions:
    try:
        collect_transaction_data(
            tx_hash,
            output_folder="my_results",
            endpoint="http://localhost:8545"
        )
        print(f"✓ {tx_hash}")
    except Exception as e:
        print(f"✗ {tx_hash}: {e}")
```

### Example: Analysis Script

```python
from collectors.txstats import collect_statistics

tx_hash = "0x8b6b8478f95fde9e00026422772ba24a65a3d7d4f7e00376220aa53308e6b5b0"
stats = collect_statistics(tx_hash, "http://localhost:8545")

# Analyze opcode usage
print("Top 10 opcodes:")
for opcode, count in list(stats['opcodes_count'].items())[:10]:
    print(f"  {opcode}: {count}")

# Analyze stack usage
stack_stats = stats['stack_size_statistics']
print(f"\nStack depth: {stack_stats['Mean']:.1f} (max: {stack_stats['Maximum']})")

# Analyze storage access
print(f"\nAddresses with storage access: {len(stats['storage_accessed'])}")
for addr, slots in stats['storage_accessed'].items():
    print(f"  {addr}: {len(slots)} slots")
```

