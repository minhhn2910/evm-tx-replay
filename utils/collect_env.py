# Function to collect transaction traces and save them as JSON files
import json
import os
import subprocess
from utils.tools import make_hex_even

cast_bin = os.environ.get("CAST_BIN", "cast")


# collect block using foundry cast
def cast_block_run(block_number, rpc_url):
    # define the command
    command = ["cast", "block", str(block_number), "--rpc-url", rpc_url, "--json"]

    # run the command and capture the output
    block_result = subprocess.run(command, capture_output=True, text=True, check=True)

    # load block related data
    text_output = block_result.stdout.strip()
    block_data = json.loads(text_output)

    return block_data


def collect_env(block_number, rpc_url):
    # get block data
    block_data = cast_block_run(block_number, rpc_url)

    # collect block data from foundry output
    block_env = {
        "currentBaseFee": make_hex_even(block_data.get("baseFeePerGas", None)),
        "currentCoinbase": block_data.get("miner", None),
        "currentDifficulty": make_hex_even(block_data.get("difficulty", None)),
        "currentGasLimit": make_hex_even(block_data.get("gasLimit", None)),
        "currentNumber": make_hex_even(block_data.get("number", None)),
        "currentRandom": "0x0000000000000000000000000000000000000000000000000000000000000000",  # Placeholder value
        "currentTimestamp": make_hex_even(block_data.get("timestamp", None)),
        "previousHash": make_hex_even(block_data.get("parentHash", None)),
    }

    return block_env
