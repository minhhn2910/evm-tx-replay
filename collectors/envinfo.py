"""
Environment information collector for Ethereum transactions.

Collects transaction details, block environment, and pre-transaction state values.
"""

import os
import json
from utils.tools import convert_hexbytes_to_str
from utils.collect_transaction import collect_transaction
from utils.collect_env import collect_env
from utils.collect_pre import collect_pre


def collect_envinfo(transaction_hash, endpoint="http://localhost:8545"):
    """
    Collect environment information for a transaction.

    Args:
        transaction_hash: The transaction hash to collect data for
        endpoint: RPC endpoint URL (default: localhost:8545)

    Returns:
        Dictionary containing transaction environment information
    """
    # Collect transaction information, block environment, pre-transaction values
    transaction, block_num = collect_transaction(transaction_hash, endpoint)
    transaction["secretKey"] = "0x45a915e4d060149eb4365960e6a7a45f334393093061116b197e3240065ff2d8"

    block_env = collect_env(block_num, endpoint)
    pre_dict = collect_pre(transaction_hash, block_num, endpoint)

    # Post value remains unchanged
    post_value = {
        "Shanghai": [
            {
                "hash": "0xb71a1e992df50c04d0247829676adce5898a419e0c00de9a76141765e22fdbc9",
                "logs": "0xb71a1e992df50c04d0247829676adce5898a419e0c00de9a76141765e22fdbc9",
                "indexes": {"data": 0, "gas": 0, "value": 0},
            },
        ],
    }

    # Generate environment information output
    envinfo = {"envinfo": {"env": block_env, "post": post_value, "pre": pre_dict, "transaction": transaction}}

    return envinfo


def save_envinfo(transaction_hash, output_folder="envInfoResult", overwrite=False, endpoint="http://localhost:8545"):
    """
    Collect and save environment information to a JSON file.

    Args:
        transaction_hash: The transaction hash to collect data for
        output_folder: Base folder for output files
        overwrite: Whether to overwrite existing results
        endpoint: RPC endpoint URL

    Returns:
        True if successful
    """
    os.makedirs(output_folder, exist_ok=True)
    folder_prefix = f"{output_folder}/{transaction_hash}"

    # Create result directory if it doesn't exist
    if overwrite and os.path.exists(folder_prefix):
        print(f"Deleting existing folder to overwrite {transaction_hash}")
        os.system(f"rm -rf {folder_prefix}")

    # Generate environment information output
    envinfo = collect_envinfo(transaction_hash, endpoint)
    os.makedirs(folder_prefix, exist_ok=True)

    # Convert HexBytes to strings and save as JSON
    envinfo = convert_hexbytes_to_str(envinfo)
    json_string = json.dumps(envinfo, indent=2)
    with open(f"{folder_prefix}/txTest.json", "w", encoding="utf-8") as json_file:
        json_file.write(json_string)

    return True
