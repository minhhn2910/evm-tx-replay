# Extract historical transaction data for replay on EVM executables

A tool for extracting Ethereum transaction data from mainnet that can be simulated/replay on any EVM executables. This tool generates JSON files conforming to the [eth-tests json](https://github.com/ethereum/legacytests/tree/1f581b8ccdc4c63acf5f2c5c1b155c690c32a8eb/Cancun/GeneralStateTests) EVM test format with minimal state, enabling simulation of mainnet transactions from any EVM implementation(e.g. used as an input for geth's`evm --json statetest` command or exporting trace to be used in [`goevmlab` tools](https://github.com/holiman/goevmlab.git)).

## Features

- 🔍 **Transaction Data Collection**: Collect complete transaction data including environment info, mimized world state, execution traces, and statistics
- 📊 **EIP-3155 Compatible Traces**: Generate execution traces in standard EIP-3155 format (simplified some fields)
- 🔄 **Batch Processing**: Process multiple transactions or entire blocks 
- 📈 **Statistics Generation**: Automatic generation of opcode usage, call depth, gas consumption, and storage access statistics
- ✅ **Trace Validation**: Built-in simple trace comparison tool for accuracy verification


The main feature is converting `cast`'s trace from mainnet fork to `txTest.json` and a reference trace `txTraceEIP3155.json` for comparison.  

The `txTest.json` contains the minimal set of storage slots and prestate's balance, code, etc. necessary for replicating the exact execution of the transaction from a sanbox world state with a few accounts declared in the json file itself.

## Installation

### Prerequisites

- Python 3.8 or higher
- [Foundry](https://book.getfoundry.sh/getting-started/installation) (for `cast` command)
- Access to an Ethereum RPC endpoint (local or remote)

### Setup

```bash
# Clone the repository
git clone https://github.com/minhhn2910/evm-tx-replay.git
cd evm-tx-replay

# Install dependencies
pip install -r requirements.txt

# Or install as a package
pip install -e .
```

## Quick Start

```bash
# Collect a single transaction
python main.py --tx 0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b

# Using a custom RPC endpoint
python main.py --endpoint http://localhost:8545 --tx 0x8b6b...

# Collect entire block
python main.py --block 18000000

# Process multiple transactions from file
python main.py --file transactions.txt
```

## Usage

### Command Line Interface

```bash
python main.py [OPTIONS] COMMAND
```

#### Options

- `--endpoint URL`: RPC endpoint URL (default: http://localhost:8545)
- `-o, --output DIR`: Output directory for results
- `--overwrite`: Overwrite existing results
- `--max-attempts N`: Maximum retry attempts for batch operations (default: 3)

#### Commands

- `--tx HASH`: Collect data for a transaction (supports comma-separated list)
- `--block NUMBER`: Collect all transactions from a specific block
- `--file PATH`: Process transactions listed in a text file
- `--env HASH`: Collect only environment information
- `--stats HASH`: Collect only transaction statistics


### Output Structure

#### Single Transaction

```
result/
└── <tx_hash>/
    ├── txTest.json           # Environment and transaction info
    ├── txStats.json          # Execution statistics
    └── txTraceEIP3155.json   # EIP-3155 execution trace
```


### Output Data Format

#### txTest.json (Environment Info)

Contains complete transaction environment and state:

```json
{
  "envinfo": {
    "env": {
      "currentNumber": "0x...",
      "currentTimestamp": "0x...",
      ...
    },
    "transaction": {
      "data": ["0x..."],
      "gasLimit": ["0x..."],
      "gasPrice": "0x...",
      "nonce": "0x...",
      "sender": "0x...",
      "to": "0x...",
      "value": ["0x..."]
    },
    "pre": {
      "0xaddress...": {
        "balance": "0x...",
        "nonce": "0x...",
        "code": "0x...",
        "storage": {
          "0xkey...": "0xvalue..."
        }
      },
      "0xcoffee...": {
        "balance": "0x...",
        "nonce": "0x...",
        ...
      }
    }
  }
}
```

#### txStats.json (Statistics)

Contains execution statistics:

```json
{
  "opcodes_count": {
    "PUSH1": 1234,
    "ADD": 567,
    "SSTORE": 89
  },
  "stack_size_statistics": {
    "Mean": 5.2,
    "Median": 4.0,
    "Maximum": 16,
    "Minimum": 0
  },
  "memory_size_statistics": { ... },
  "max_depth": 3,
  "storage_accessed": {
    "0xaddress...": ["0xkey1...", "0xkey2..."]
  }
}
```

#### txTraceEIP3155.json (Execution Trace)

Contains step-by-step execution in [EIP-3155](https://eips.ethereum.org/EIPS/eip-3155) format:

```json
{"pc":0,"op":96,"gas":"0x89f5","gasCost":"0x3","memSize":0,"stack":[],"depth":1,"refund":0}
{"pc":2,"op":96,"gas":"0x89f2","gasCost":"0x3","memSize":0,"stack":["0x80"],"depth":1,"refund":0}
```

### Trace Validation

Compare generated traces with reference traces:

```bash
python compare_traces.py <reference_trace> <generated_trace>
```
#### Example extract and run validation against geth's evm statetest

```bash
# let's try a sample uniswap's swap from vitalik.eth
# add --endpoint <your rpc url> if it's not localhost
python main.py --tx 0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b 

# check result files 
ls result/0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b/

# run goethereum evm statetest, change to your own evm executable path
../go-ethereum-1.14.13/evm --json statetest result/0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b/txTest.json 2&> geth.log

# compare log trace between geth and reference result/0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b/txTraceEIP3155.json
python compare_traces.py result/0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b/txTraceEIP3155.json geth.log --no-gas

# Output : 
# Loading traces...
#  File 1: result/0x88244bc267fb3fafd5333bb4f707d0905c0893026534332b7ea269849531991b/txTraceEIP3155.json
#  File 2: geth.log
#  Mode: Skipping gas comparison (checking PC, opcode, stack only)

# Trace lengths:
#  File 1: 8935 steps
#  File 2: 8935 steps (11 non-trace lines skipped)

# Comparing traces...
#✅ SUCCESS: All 8935 trace steps match perfectly!
#   (Note: 1 steps had gas mismatches, but were ignored)

```

### Limitations 

The below are known limitations of the tool:
  * Not support access list
  * Gas trace difference has 1-2 corner cases from `cast -t` where the gas of the immediate instruction after returning from call has wrong value. Use `--no-gas` in `compare_trace.py` to ignore those small differences
  * Post state root is not calculated, a dummy value is used instead. `statetest` will not result in a success match but we can check line by line in the trace to make sure the state is can replay correctly.


### Acknowledgements and contributors

This project is largely based on the work of ZhangTong ([Ztong55](https://github.com/Ztong55)). We thank ZhangTong for his contribution to the first version of this tool.

### License

MIT License

### Support

For issues, questions, or contributions, please open an issue on GitHub.
