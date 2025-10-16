# Function to collect transaction traces and save them as JSON files
import subprocess
from web3 import Web3
from utils.tools import make_hex_even, remove_extra_zeros, cast_trace_run, add_to_dict, strict_extend, extend_dict


# detect whether a string is an address
def is_address(evm_str):
    if isinstance(evm_str, str) and evm_str.startswith("0x") and len(evm_str) == 42:
        return True
    else:
        return False


# get all addresses in a list
def find_address_in_list(stack_list):
    address_list = []
    for element in stack_list:
        if is_address(element) and element not in address_list:
            address_list.append(element)
    return address_list


# collect all state changes in the steps of a call trace
def collect_state_changes(steps):
    address_list = []
    storage_change_dict = {}
    second_storage_dict = {}
    step_index = 0
    for step in steps:
        # collect all steps' contracts
        address_list = strict_extend(address_list, [step["contract"]])
        op_code = hex(step["op"])[2:].upper()
        # collect targets of call steps
        if op_code in ["F1", "F2", "F4", "FA"]:
            address_list = strict_extend(address_list, find_address_in_list(step["stack"]))
        # if a step has storage change
        if step["storage_change"]:
            add_to_dict(storage_change_dict, step["contract"], step["storage_change"])

        # if the step's opcode is "54" or "55".
        if op_code in ["54", "55"] and step_index != len(steps) - 1:
            add_to_dict(second_storage_dict, step["contract"], [step["stack"][-1], steps[step_index + 1]["stack"][-1]])
        step_index += 1
    return address_list, storage_change_dict, second_storage_dict


# collect all addresses from the trace
def collect_address(trace_address_dict):
    address_list = []
    for key in trace_address_dict:
        address_list = strict_extend(address_list, trace_address_dict[key])
    return address_list


# collect all keys and corresponded values in the storage change list
def get_all_keys(storage_list):
    key_dict = {}
    for storage_change in storage_list:
        # make sure that hex is even
        if make_hex_even(storage_change["key"]) not in key_dict:
            key_dict[make_hex_even(storage_change["key"])] = make_hex_even(storage_change["had_value"])
    return key_dict


# get all the storage changed slots and values from trace
def get_storage_keys(trace_storage_dict):
    storage_dict = {}
    for key in trace_storage_dict:
        address_storage_dict = trace_storage_dict[key]
        for address in address_storage_dict:
            storage_list = address_storage_dict[address]
            extend_dict(storage_dict, address, get_all_keys(storage_list))
    return storage_dict


# get all the pre-transaction value from the steps
def collect_from_steps(json_output):
    # get addresses from trace
    trace_address_dict = {}
    # get storage changes from trace
    trace_storage_dict = {}
    # get unrecorded storage changes from trace
    second_dict = {}
    for element in json_output:
        # collect trace and related information and store individually
        new_trace = element["trace"]
        address_list, storage_change_dict, second_storage_dict = collect_state_changes(new_trace["steps"])
        address_list = strict_extend(address_list, [new_trace["caller"].lower(), new_trace["address"].lower()])
        idx = element["idx"]
        trace_address_dict[idx] = address_list
        trace_storage_dict[idx] = storage_change_dict
        for key in second_storage_dict:
            for value in second_storage_dict[key]:
                add_to_dict(second_dict, key, value)
    # summarize all addresses and storage changes
    address_list = collect_address(trace_address_dict)
    storage_dict = get_storage_keys(trace_storage_dict)
    return address_list, storage_dict, second_dict


# retrieve balance from certain block
def retrieve_balance(w3, contract_address, block_number):
    try:
        balance = w3.eth.get_balance(contract_address, block_identifier=block_number)
        return make_hex_even(hex(balance))
    # if no response, set balance as 0
    except Exception as e:
        print("balance retrival error:", e)
        return "0x00"


# retrieve nonce from certain block
def retrieve_nonce(w3, contract_address, block_number):
    try:
        nonce = w3.eth.get_transaction_count(contract_address, block_identifier=block_number)
        return make_hex_even(hex(nonce))
    # if no response, set nonce as 1
    except Exception as e:
        print("nonce retrival error:", e)
        return "0x01"


# retrieve code from certain block
def retrieve_code(w3, contract_address, block_number):
    try:
        bytecode = w3.eth.get_code(contract_address, block_identifier=block_number)
        return "0x" + bytecode.hex()
    # if no response, set code as empty
    except Exception as e:
        print("code retrival error:", e)
        return "0x"


# collect all the pre-transaction information
def collect_pre(transaction_hash, block_number, rpc_url):
    # collect trace and get addresses and storages in the trace
    trace_list = cast_trace_run(transaction_hash, rpc_url)
    address_list, storage_dict, second_dict = collect_from_steps(trace_list)

    # set the previous block and w3
    previous_block = block_number - 1
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    pre_dict = {}

    # merge the storage dict to address dict
    for address in address_list:
        if address in storage_dict:
            storage = storage_dict[address]
        else:
            storage = {}

        # get address related information
        checksum_address = Web3.to_checksum_address(address)
        balance = retrieve_balance(w3, checksum_address, previous_block)
        nonce = retrieve_nonce(w3, checksum_address, previous_block)
        code = retrieve_code(w3, checksum_address, previous_block)

        # add new storage in second dict to storage dict
        if address in second_dict:
            checked_address = address
            for kv_pair in second_dict[checked_address]:
                key = kv_pair[0]
                even_key = make_hex_even(key)
                if even_key not in storage:
                    storage[even_key] = make_hex_even(kv_pair[1])

        # remove zero values
        storage = {k: v for k, v in storage.items() if v != "0x00"}

        # add information to pre-transaction dict
        pre_dict[address] = {"balance": balance, "nonce": nonce, "code": code, "storage": storage}

    return pre_dict
