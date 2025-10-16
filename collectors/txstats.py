"""
Transaction statistics collector for Ethereum transactions.

Collects opcodes, stack sizes, memory sizes, and storage access statistics.
"""

import os
import json
from utils.tools import (
    add_to_dict,
    remove_extra_zeros,
    cast_trace_run,
    count_and_sort,
    get_statistics,
    convert_hexbytes_to_str,
)
from utils.collect_pre import collect_from_steps
from utils.opcodes import OPCODE_MAP


def process_reference(opcode_counts):
    """Transform opcode hex codes into readable names."""
    result = {}
    for key, value in opcode_counts.items():
        new_key = OPCODE_MAP.get(key, key)
        if new_key in result:
            result[new_key] += value
        else:
            result[new_key] = value
    return dict(sorted(result.items(), key=lambda x: (-x[1], x[0])))


def collect_steps(steps):
    """Collect information from execution steps."""
    opcode_list = []
    stack_size_list = []
    memory_size_list = []
    accessed_addresses = set()
    max_depth = 0

    for step in steps:
        op_code = hex(step["op"])[2:].upper()
        opcode_list.append(op_code)
        max_depth = max(max_depth, step["depth"])
        stack_size_list.append(len(step["stack"]))
        memory_size_list.append((len(step["memory"]) - 2) / 2)
        accessed_addresses.add(step.get("contract"))

    accessed_addresses = list(accessed_addresses)

    return {
        "opcodes": opcode_list,
        "stack_sizes": stack_size_list,
        "memory_sizes": memory_size_list,
        "addresses": accessed_addresses,
        "max_depth": max_depth,
    }


def process_call(call_trace):
    """Get information from each call."""
    data_length = len(call_trace["data"])
    return_length = len(call_trace["output"])
    return data_length, return_length


def collect_storage_accessed(trace_list):
    """Collect storage slots accessed during execution."""
    _, storage_dict, second_dict = collect_from_steps(trace_list)
    address_storage_dict = {}

    # Process the known slots
    for address in storage_dict:
        single_storage_dict = storage_dict.get(address, {})
        if isinstance(single_storage_dict, dict):
            key_list = list(single_storage_dict.keys())
            for key in key_list:
                key = remove_extra_zeros(key)
                add_to_dict(address_storage_dict, address, key)

    # Process the unknown keys
    for address in second_dict:
        for kv_pair in second_dict[address]:
            key = remove_extra_zeros(kv_pair[0])
            add_to_dict(address_storage_dict, address, key)

    return address_storage_dict


def collect_lists(result_list):
    """Collect statistics from traces."""
    trace_list = [arena["trace"] for arena in result_list]
    full_opcode_list = []
    full_stack_size_list = []
    full_memory_size_list = []
    address_list = []
    depth_list = []
    call_data_length = []
    call_return_length = []

    # Add results to each list
    for t in trace_list:
        steps = t["steps"]
        output_dict = collect_steps(steps)
        full_opcode_list.extend(output_dict["opcodes"])
        full_stack_size_list.extend(output_dict["stack_sizes"])
        full_memory_size_list.extend(output_dict["memory_sizes"])
        address_list.extend(output_dict["addresses"])
        depth_list.append(output_dict["max_depth"])

        data_length, return_length = process_call(t)
        call_data_length.append(data_length)
        call_return_length.append(return_length)

    # Get storage from the trace
    storage_accessed = collect_storage_accessed(result_list)

    # Get statistics from all lists
    address_list = list(set(address_list))
    opcode_count = count_and_sort(full_opcode_list)
    opcode_name_count = process_reference(opcode_count)
    stack_size_statistics = get_statistics(full_stack_size_list)
    memory_size_statistics = get_statistics(full_memory_size_list)
    call_size_statistics = get_statistics(call_data_length)
    return_size_statistics = get_statistics(call_return_length)
    storage_accessed_count = {key: len(value) for key, value in storage_accessed.items()}
    storage_accessed_statistics = get_statistics(list(storage_accessed_count.values()))
    max_depth = max(depth_list)

    # Put information into a dict
    return {
        "opcodes_count": opcode_name_count,
        "stack_size_statistics": stack_size_statistics,
        "memory_size_statistics": memory_size_statistics,
        "call_size_statistics": call_size_statistics,
        "return_size_statistics": return_size_statistics,
        "addresses": address_list,
        "count_address": len(address_list),
        "max_depth": max_depth,
        "storage_accessed": storage_accessed,
        "storage_accessed_count": storage_accessed_count,
        "storage_accessed_statistics": storage_accessed_statistics,
    }


def collect_statistics(transaction_hash, endpoint="http://localhost:8545"):
    """
    Collect statistics for an Ethereum transaction.

    Args:
        transaction_hash: The transaction hash to collect statistics for
        endpoint: RPC endpoint URL (default: localhost:8545)

    Returns:
        Dictionary containing transaction statistics
    """
    result_list = cast_trace_run(transaction_hash, endpoint)
    transaction_statistics = collect_lists(result_list)
    return transaction_statistics


def save_statistics(transaction_hash, output_folder="statsResult", overwrite=False, endpoint="http://localhost:8545"):
    """
    Collect and save transaction statistics to a JSON file.

    Args:
        transaction_hash: The transaction hash to collect statistics for
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

    transaction_statistics = collect_statistics(transaction_hash, endpoint)
    os.makedirs(folder_prefix, exist_ok=True)

    # Convert HexBytes to strings and save as JSON
    transaction_statistics = convert_hexbytes_to_str(transaction_statistics)
    json_string = json.dumps(transaction_statistics, indent=2)
    with open(f"{folder_prefix}/txStats.json", "w", encoding="utf-8") as json_file:
        json_file.write(json_string)

    return True
