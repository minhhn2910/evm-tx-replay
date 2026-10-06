"""RPC helpers: HTTP/IPC providers, cast binary, and JSON-RPC calls."""

import json
import os
import subprocess
from web3 import Web3

cast_bin = os.environ.get("CAST_BIN", "cast")

_w3_cache = {}


def normalize_endpoint(endpoint):
    """Keep IPC paths as-is; add http:// to host:port endpoints."""
    if not endpoint:
        return endpoint
    if endpoint.startswith(("http://", "https://", "ws://", "wss://", "file://")):
        return endpoint
    endpoint = os.path.expanduser(endpoint)
    if endpoint.endswith(".ipc") or endpoint.startswith("/"):
        return endpoint
    return f"http://{endpoint}"


def is_ipc(endpoint):
    if not endpoint:
        return False
    return (
        endpoint.startswith("file://")
        or endpoint.endswith(".ipc")
        or (endpoint.startswith("/") and "://" not in endpoint)
    )


def get_w3(endpoint):
    if endpoint not in _w3_cache:
        if is_ipc(endpoint):
            _w3_cache[endpoint] = Web3(Web3.IPCProvider(endpoint.removeprefix("file://")))
        else:
            _w3_cache[endpoint] = Web3(Web3.HTTPProvider(endpoint))
    return _w3_cache[endpoint]


def rpc_call(endpoint, method, params):
    return get_w3(endpoint).manager.request_blocking(method, params)


def unwrap_cast_json(text):
    """Parse JSON from cast stdout, unwrapping Foundry's {schema_version, data} envelope."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("cast produced no JSON output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = json.loads(text.rsplit("\n", 1)[-1])
    if isinstance(payload, dict) and "schema_version" in payload:
        if not payload.get("success", True):
            raise RuntimeError(_envelope_error(payload))
        payload = payload.get("data")
    return payload


def _envelope_error(payload):
    messages = []
    for item in payload.get("errors") or []:
        if isinstance(item, dict):
            messages.append(item.get("message") or str(item))
        else:
            messages.append(str(item))
    return "; ".join(messages) or "cast --json reported failure"


def _cast_error_detail(result):
    for text in (result.stderr, result.stdout):
        if not text:
            continue
        last = text.rstrip().rsplit("\n", 1)[-1]
        try:
            payload = json.loads(last)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("errors"):
            return _envelope_error(payload)
    if result.stderr and result.stderr.strip():
        return result.stderr.strip()[-2000:]
    if result.stdout and result.stdout.strip():
        return result.stdout.rstrip().rsplit("\n", 1)[-1]
    return f"cast exited {result.returncode}"


def run_cast(*args, rpc_timeout=300):
    """
    Run `cast` and return stdout.

    Timeout is passed as `--rpc-timeout` (default 45s in cast). A host env var
    is not enough: `docker exec` wrappers do not forward ETH_RPC_TIMEOUT.

    stdin is closed so `docker exec -i ... cast "$@"` does not inherit the
    parent stdin. On failure, raise with a short error (not the full trace).
    """
    cmd = [cast_bin, *args]
    if "--rpc-timeout" not in args:
        cmd.extend(["--rpc-timeout", str(rpc_timeout)])
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(_cast_error_detail(result))
    return result.stdout

