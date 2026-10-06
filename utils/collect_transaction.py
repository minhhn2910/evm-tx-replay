from utils.tools import make_hex_even
from utils.rpc import get_w3


def collect_transaction(transaction_hash, rpc):
    try:
        # get transaction details
        basic_transaction = get_w3(rpc).eth.get_transaction(transaction_hash)

    except Exception as e:
        # handle other unexpected exceptions
        raise RuntimeError(f"An unexpected error occurred: {e}")

    # collect transaction data from web3 output
    transaction_data = {
        "data": [basic_transaction.get("input", None)],
        "gasLimit": [make_hex_even(basic_transaction.get("gas", None))],
        "gasPrice": make_hex_even(basic_transaction.get("gasPrice", None)),
        "nonce": make_hex_even(basic_transaction.get("nonce", None)),
        "sender": basic_transaction.get("from", None).lower(),
        "to": basic_transaction.get("to", None).lower() if basic_transaction["to"] else "0x",
        "value": [make_hex_even(basic_transaction.get("value", None))],
    }
    # collect block number for next steps
    block_num = basic_transaction.get("blockNumber", None)

    return transaction_data, block_num
