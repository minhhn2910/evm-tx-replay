import json
import statistics
import time
from collections import Counter
from collections.abc import Mapping
from hexbytes import HexBytes
from utils.rpc import run_cast


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

    # remove duplicates, sorted so repeated runs produce identical output
    list1 = sorted(set(list1), key=str)

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
    """Recursively convert HexBytes and mapping objects to JSON-safe values."""
    if isinstance(obj, HexBytes):
        return obj.hex()
    if isinstance(obj, bytes):
        return "0x" + obj.hex()
    if isinstance(obj, Mapping):
        return {k: convert_hexbytes_to_str(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_hexbytes_to_str(item) for item in obj]
    return obj


def report_timing(start_time, total_transactions, successful, label):
    """Print and return the timing statistics shared by the collection commands."""
    total_time = time.time() - start_time
    avg_time_per_tx = total_time / total_transactions if total_transactions > 0 else 0

    print(f"\n{label} complete:")
    print(f"  Total time: {total_time:.2f} seconds")
    print(f"  Average time per transaction: {avg_time_per_tx:.2f} seconds")
    print(f"  Successful: {successful}/{total_transactions}")

    return {
        "total_time": total_time,
        "avg_time_per_tx": avg_time_per_tx,
        "total_transactions": total_transactions,
        "successful": successful,
        "failed": total_transactions - successful,
    }


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
def cast_run(transaction_hash, rpc_url, trace_printer=False):
    """
    Run cast command and extract the trace steps and arena JSON.

    `--json` prints the arena as the last line of stdout; with `-t` the ordered
    opcode trace lines precede it.

    Args:
        transaction_hash: Transaction hash
        rpc_url: RPC endpoint URL (HTTP, WS or IPC socket path)
        trace_printer: Whether to also collect ordered opcode steps (cast -t)

    Returns:
        Tuple of (trace_lines, arena)
    """
    args = [
        "run",
        transaction_hash,
        "-r",
        rpc_url,
        "-vvvvv",
        "--json",
        "--no-rate-limit",
        "--prestate-tracer",
    ]
    if trace_printer:
        args.append("-t")
    stdout = run_cast(*args)

    # The arena is the final line, so everything before it is the opcode trace
    json_start = stdout.rstrip().rfind("\n") + 1
    arena = json.loads(stdout[json_start:]).get("arena", [])
    trace_lines = stdout[:json_start].splitlines() if trace_printer else []

    return trace_lines, arena


def cast_trace_run(transaction_hash, rpc_url):
    """Return only the arena (list of trace nodes) for statistics."""
    return cast_run(transaction_hash, rpc_url)[1]


def cast_trace_run_with_steps(transaction_hash, rpc_url):
    """Return (trace_lines, arena) with ordered opcode steps for EIP-3155."""
    return cast_run(transaction_hash, rpc_url, trace_printer=True)
