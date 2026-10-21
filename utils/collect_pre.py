# Function to collect transaction traces and save them as JSON files
from web3 import Web3
from utils.tools import make_hex_even, cast_trace_run, add_to_dict, strict_extend, extend_dict
from utils.rpc import rpc_call


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


# Newer foundry dropped per-step `contract`/`depth`; they live on the parent call.
CALL_OPS = {"F1", "F2", "F4", "FA", "CALL", "CALLCODE", "DELEGATECALL", "STATICCALL"}
SLOAD_SSTORE = {"54", "55", "SLOAD", "SSTORE"}


def step_address(step, fallback=None):
    addr = step.get("contract") or step.get("address") or fallback
    return addr.lower() if isinstance(addr, str) else addr


def step_stack(step):
    stack = step.get("stack")
    if not stack:
        return []
    if isinstance(stack, dict):
        stack = stack.get("data") or stack.get("values") or []
    return list(stack)


def step_op_key(step):
    op = step.get("op", 0)
    if isinstance(op, dict):
        op = op.get("code") or op.get("name") or 0
    if isinstance(op, str):
        if op.startswith(("0x", "0X")):
            return op[2:].upper()
        if op.isdigit():
            return hex(int(op))[2:].upper()
        return op.upper()
    return hex(int(op))[2:].upper()


def step_memory_size(step):
    mem = step.get("memory")
    if mem is None:
        return 0
    if isinstance(mem, dict):
        mem = mem.get("bytes") or mem.get("data") or ""
    if isinstance(mem, str):
        hex_len = len(mem) - 2 if mem.startswith("0x") else len(mem)
        return hex_len / 2
    return 0


# collect all state changes in the steps of a call trace
def collect_state_changes(steps, contract=None):
    address_list = []
    storage_change_dict = {}
    second_storage_dict = {}
    for step_index, step in enumerate(steps):
        addr = step_address(step, contract)
        if addr:
            address_list = strict_extend(address_list, [addr])
        op_code = step_op_key(step)
        stack = step_stack(step)
        if op_code in CALL_OPS:
            address_list = strict_extend(address_list, find_address_in_list(stack))
        storage_change = step.get("storage_change")
        if storage_change and addr:
            add_to_dict(storage_change_dict, addr, storage_change)
        if op_code in SLOAD_SSTORE and step_index != len(steps) - 1 and addr and stack:
            add_to_dict(second_storage_dict, addr, [stack[-1], step_stack(steps[step_index + 1])[-1]])
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
    for storage_change in storage_list or []:
        if not isinstance(storage_change, dict):
            continue
        key = storage_change.get("key")
        had_value = storage_change.get("had_value")
        if key is None:
            continue
        even_key = make_hex_even(key)
        if even_key not in key_dict:
            key_dict[even_key] = make_hex_even(had_value)
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
    for element in json_output or []:
        if not isinstance(element, dict):
            continue
        new_trace = element.get("trace") or {}
        address_list, storage_change_dict, second_storage_dict = collect_state_changes(
            new_trace.get("steps") or [], contract=new_trace.get("address")
        )
        caller = (new_trace.get("caller") or "0x").lower()
        address = (new_trace.get("address") or "0x").lower()
        address_list = strict_extend(address_list, [caller, address])
        idx = element.get("idx", 0)
        trace_address_dict[idx] = address_list
        trace_storage_dict[idx] = storage_change_dict
        for key in second_storage_dict:
            for value in second_storage_dict[key]:
                add_to_dict(second_dict, key, value)
    # summarize all addresses and storage changes
    address_list = collect_address(trace_address_dict)
    storage_dict = get_storage_keys(trace_storage_dict)
    return address_list, storage_dict, second_dict


def retrieve_accounts(rpc_url, address_list, block_number):
    block = hex(block_number) if isinstance(block_number, int) else block_number
    accounts = {}
    for address in address_list:
        checksum = Web3.to_checksum_address(address)
        balance = rpc_call(rpc_url, "eth_getBalance", [checksum, block])
        nonce = rpc_call(rpc_url, "eth_getTransactionCount", [checksum, block])
        code = rpc_call(rpc_url, "eth_getCode", [checksum, block])
        if not isinstance(code, str):
            code = "0x" + code.hex()
        accounts[address] = {
            "balance": make_hex_even(balance),
            "nonce": make_hex_even(nonce),
            "code": code,
        }
    return accounts


# collect all the pre-transaction information
def collect_pre(transaction_hash, block_number, rpc_url, trace_list=None):
    # reuse an already collected trace when available, otherwise run cast
    if trace_list is None:
        trace_list = cast_trace_run(transaction_hash, rpc_url)
    address_list, storage_dict, second_dict = collect_from_steps(trace_list)

    # read every account's state from the previous block
    accounts = retrieve_accounts(rpc_url, address_list, block_number - 1)

    pre_dict = {}

    # merge the storage dict to address dict
    for address in address_list:
        if address in storage_dict:
            storage = storage_dict[address]
        else:
            storage = {}

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
        pre_dict[address] = {**accounts[address], "storage": storage}

    return pre_dict
