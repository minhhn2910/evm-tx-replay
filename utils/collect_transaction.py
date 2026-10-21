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
    sender = tx.get("from") or "0x"
    transaction_data = {
        "data": [tx.get("input") or "0x"],
        "gasLimit": [make_hex_even(tx.get("gas"))],
        "gasPrice": make_hex_even(tx.get("gasPrice") or tx.get("maxFeePerGas")),
        "nonce": make_hex_even(tx.get("nonce")),
        "sender": sender.lower(),
        "to": to.lower() if to else "0x",
        "value": [make_hex_even(tx.get("value"))],
    }
    return transaction_data, block_num
