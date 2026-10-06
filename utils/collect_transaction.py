from utils.tools import make_hex_even
from utils.rpc import rpc_call


def collect_transaction(transaction_hash, rpc):
    try:
        tx = rpc_call(rpc, "eth_getTransactionByHash", [transaction_hash])
    except Exception as e:
        raise RuntimeError(f"An unexpected error occurred: {e}")

    if not tx:
        raise RuntimeError(f"transaction not found: {transaction_hash}")

    block_num = tx.get("blockNumber")
    if isinstance(block_num, str):
        block_num = int(block_num, 16)

    to = tx.get("to")
    transaction_data = {
        "data": [tx.get("input")],
        "gasLimit": [make_hex_even(tx.get("gas"))],
        "gasPrice": make_hex_even(tx.get("gasPrice")),
        "nonce": make_hex_even(tx.get("nonce")),
        "sender": tx.get("from").lower(),
        "to": to.lower() if to else "0x",
        "value": [make_hex_even(tx.get("value"))],
    }
    return transaction_data, block_num
