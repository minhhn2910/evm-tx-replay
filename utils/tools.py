import json
import statistics
import subprocess
from collections import Counter
from hexbytes import HexBytes


def is_tx(tx_line: str):
    if isinstance(tx_line, str):
        if len(tx_line) == 66 and tx_line.startswith("0x"):
            return True
    return False


# for adding a key-value pair to a dict with auto choose appending
def add_to_dict(input_dict, key, value):
    if key not in input_dict:
        input_dict[key] = [value]
    else:
        if value not in input_dict[key]:
            input_dict[key].append(value)
    return input_dict


# for extending a list with another list and remove duplicates
def strict_extend(list1, list2):
    # extend list1 with list2
    list1.extend(list2)

    # remove duplicates by converting to a set and back to a list
    list1 = list(set(list1))

    return list1


# for adding a dict to an exist dict and not change value of exist key
def extend_dict(input_dict, key, value_dict):
    if key not in input_dict:
        input_dict[key] = value_dict
    else:
        for v_key in value_dict:
            if v_key not in input_dict[key]:
                input_dict[key][v_key] = value_dict[v_key]
    return input_dict


class HexBytesEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles HexBytes objects."""

    def default(self, obj):
        if isinstance(obj, HexBytes):
            return obj.hex()
        return super().default(obj)


def json_dumps_with_hexbytes(obj, **kwargs):
    """JSON dumps that handles HexBytes objects."""
    return json.dumps(obj, cls=HexBytesEncoder, **kwargs)


def convert_hexbytes_to_str(obj):
    """Recursively convert HexBytes objects to hex strings."""
    if isinstance(obj, HexBytes):
        return obj.hex()
    elif isinstance(obj, dict):
        return {k: convert_hexbytes_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return type(obj)(convert_hexbytes_to_str(item) for item in obj)
    return obj


def count_and_sort(lst):
    counts = Counter(lst)
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def make_hex_even(value):
    # Convert the value to hex and remove the '0x' prefix
    if isinstance(value, int):
        hex_value = hex(value)[2:]
    else:
        hex_value = value[2:]

    # If the length of the hex value is odd, add a leading '0'
    if len(hex_value) % 2 != 0:
        hex_value = "0" + hex_value

    # Return the hex value with '0x' prefix
    return "0x" + hex_value


# remove extra 0 in a hex
def remove_extra_zeros(hex_str):
    if not hex_str.startswith("0x"):
        print("Input must start with '0x'")
        return "0x00"

    stripped = hex_str[2:].lstrip("0")
    str_output = "0x" + (stripped if stripped else "0")
    return make_hex_even(str_output)


# get statistics from a list of numbers
def get_statistics(numbers):
    if len(numbers) == 0:
        return {}

    # if list is not all numbers
    if not all(isinstance(num, (int, float)) for num in numbers):
        print("List contains non-numeric values.")
        return {}

    try:
        # collect various stats
        stats = {
            "Mean": statistics.mean(numbers),
            "Median": statistics.median(numbers) if len(numbers) > 1 else numbers[0],
            "Mode": statistics.mode(numbers) if len(set(numbers)) < len(numbers) else None,
            "Standard Deviation": statistics.stdev(numbers) if len(numbers) > 1 else 0,
            "Variance": statistics.variance(numbers) if len(numbers) > 1 else 0,
            "Range": max(numbers) - min(numbers),
            "Minimum": min(numbers),
            "Maximum": max(numbers),
            "Q1": statistics.median(sorted(numbers)[: len(numbers) // 2]) if len(numbers) > 1 else numbers[0],
            "Q3": statistics.median(sorted(numbers)[(len(numbers) + 1) // 2 :]) if len(numbers) > 1 else numbers[0],
            "Count": len(numbers),
            "Sum": sum(numbers),
        }

        return stats
    except Exception as e:
        print(f"Error: {e}")
        return {}


# use foundry collect trace
def cast_trace_run(transaction_hash, rpc_url):
    """
    Run cast command and extract arena JSON for statistics.

    Args:
        transaction_hash: Transaction hash
        rpc_url: RPC endpoint URL

    Returns:
        Arena data (list of trace nodes)
    """
    command = [
        "cast",
        "run",
        transaction_hash,
        "-r",
        rpc_url,
        "--decode-internal",
        "-vvvvv",
        "--json",
        "--no-rate-limit",
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=True)
    lines = result.stdout.strip().split("\n")
    traces_index = -1

    for index, line in enumerate(lines):
        if "Traces:" in line:
            traces_index = index
            break
    output_lines = lines[traces_index + 1 :]

    filtered_output = "\n".join(output_lines)
    json_output = json.loads(filtered_output)

    return json_output["arena"]


def cast_trace_run_with_steps(transaction_hash, rpc_url):
    """
    Run cast command with -t flag to get ordered trace steps and arena JSON.

    Args:
        transaction_hash: Transaction hash
        rpc_url: RPC endpoint URL

    Returns:
        Tuple of (trace_lines, arena) where trace_lines are the ordered step traces
        and arena is the JSON data for statistics
    """
    command = [
        "cast",
        "run",
        transaction_hash,
        "-r",
        rpc_url,
        "--decode-internal",
        "-vvvvv",
        "--json",
        "--no-rate-limit",
        "-t",
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=True)
    lines = result.stdout.strip().split("\n")

    # Find the last line that starts with "depth:" - this is the last trace line
    last_trace_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("depth:"):
            last_trace_index = index

    # Split into trace lines and JSON
    trace_lines = []
    if last_trace_index >= 0:
        trace_lines = lines[: last_trace_index + 1]
        json_lines = lines[last_trace_index + 1 :]
    else:
        # No trace lines found, everything is JSON
        json_lines = lines

    # Parse JSON from remaining lines
    json_str = "\n".join(json_lines)
    json_output = json.loads(json_str)

    return trace_lines, json_output.get("arena", [])
