"""RPC helpers: HTTP/IPC providers, cast binary, and JSON-RPC calls."""

import os
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
