"""Reduce one EIP-3155 trace to opcode totals and the storage, hash, and call steps.

Call opcodes keep two gas figures. `own` is only that opcode's net cost.
`inside` is every net gas from the opcode until execution returns to the
caller, including nested calls. `direct` is the part of that window that is
not inside a nested call, so the pieces add up once.

SLOAD results and KECCAK256 outputs come from the next step. SSTORE pre/post
values start from the fast-mode pre-state and follow each write. STATICCALL
input and output come from the fast-mode call tree.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.opcodes import get_opcode_name

CALLS = {"CALL", "STATICCALL", "DELEGATECALL", "CALLCODE", "CREATE", "CREATE2"}
# The per-transaction stream keeps these, in trace order. Other opcodes stay in the block totals only.
KEPT = {"SLOAD", "SSTORE", "KECCAK256"} | CALLS
CALL_TO = {"CALL", "STATICCALL"}
CALL_SELF = {"CALLCODE", "DELEGATECALL"}
CREATE = {"CREATE", "CREATE2"}
ADDRESS_MASK = (1 << 160) - 1


def as_int(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith(("0x", "0X")) else int(text)


def load_json(path: Path):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def opcode_name(op) -> str:
    name = get_opcode_name(hex(as_int(op))[2:])
    return "KECCAK256" if name == "SHA3" else name


def hex_word(value) -> str:
    if isinstance(value, str):
        text = value.lower()
        if text.startswith("0x"):
            return text
    return hex(as_int(value))


def hex_addr(value) -> str:
    return f"0x{as_int(value) & ADDRESS_MASK:040x}"


def iter_steps(path: Path) -> list[dict]:
    steps = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                steps.append(json.loads(line))
    return steps


def net_step_costs(steps: list[dict], names: list[str]) -> list[int]:
    """Net gas of each step. A call's returned gas is subtracted from that call."""
    nets = [0] * len(steps)
    open_idx: list[int] = []
    prev_name = None
    prev_depth = None
    for index, step in enumerate(steps):
        name = names[index]
        cost = int(str(step["gasCost"]), 0)
        depth = step["depth"]
        if prev_name is not None:
            if depth == prev_depth + 1 and prev_name in CALLS:
                open_idx.append(index - 1)
            elif depth < prev_depth:
                if cost < 0 and open_idx:
                    nets[open_idx[-1]] += cost
                    cost = 0
                for _ in range(prev_depth - depth):
                    if open_idx:
                        open_idx.pop()
            elif cost < 0 and prev_name in CALLS and depth == prev_depth:
                nets[index - 1] += cost
                cost = 0
        if cost < 0:
            cost = 0
        nets[index] += cost
        prev_name, prev_depth = name, depth
    return nets


def iter_call_frames(node: dict, is_root: bool = True):
    """Call-tree frames in execution order. The transaction root is not a frame."""
    if not node:
        return
    if not is_root:
        yield node
    for child in node.get("calls") or []:
        yield from iter_call_frames(child, False)


def seed_storage(fast_dir: Path) -> dict[tuple, int]:
    """Pre-tx slot values from txStateRead/txStateWrite, or legacy txStateDiff."""
    storage: dict[tuple, int] = {}
    reads = load_json(fast_dir / "txStateRead.json")
    writes = load_json(fast_dir / "txStateWrite.json")
    if isinstance(reads, list) or isinstance(writes, list):
        for item in reads or []:
            storage[(hex_addr(item.get("address")), as_int(item.get("slot")))] = as_int(item.get("value"))
        for item in writes or []:
            storage[(hex_addr(item.get("address")), as_int(item.get("slot")))] = as_int(item.get("old_value"))
        return storage

    diff = load_json(fast_dir / "txStateDiff.json") or {}
    pre = diff.get("pre") or {}
    post = diff.get("post") or {}
    for address in set(pre) | set(post):
        before = ((pre.get(address) or {}).get("storage") or {})
        after = ((post.get(address) or {}).get("storage") or {})
        for slot, value in before.items():
            storage[(hex_addr(address), as_int(slot))] = as_int(value)
        for slot in after:
            storage.setdefault((hex_addr(address), as_int(slot)), 0)
    return storage


def expected_posts(fast_dir: Path) -> dict[tuple, int]:
    """Committed post-tx slot values, used to check the SSTORE replay."""
    writes = load_json(fast_dir / "txStateWrite.json")
    if isinstance(writes, list):
        return {
            (hex_addr(item.get("address")), as_int(item.get("slot"))): as_int(item.get("value"))
            for item in writes
        }
    diff = load_json(fast_dir / "txStateDiff.json") or {}
    posts = {}
    for address, account in (diff.get("post") or {}).items():
        for slot, value in ((account or {}).get("storage") or {}).items():
            posts[(hex_addr(address), as_int(slot))] = as_int(value)
    return posts


class Storage:
    """Slot values with a journal per call, so a reverted frame rolls back."""

    def __init__(self, seed: dict[tuple, int]):
        self.values = dict(seed)
        self.journal: list[list[tuple]] = []

    def enter(self) -> None:
        self.journal.append([])

    def leave(self, revert: bool) -> None:
        journal = self.journal.pop() if self.journal else []
        if not revert:
            return
        for key, old in reversed(journal):
            if old is None:
                self.values.pop(key, None)
            else:
                self.values[key] = old

    def get(self, key: tuple) -> int:
        return self.values.get(key, 0)

    def set(self, key: tuple, value: int) -> None:
        old = self.values.get(key)
        if self.journal:
            self.journal[-1].append((key, old))
        self.values[key] = value


def _add(op: dict, name: str, kind: str, amount: int) -> None:
    row = op.setdefault(name, [0, 0, 0, 0] if name in CALLS else [0, 0, 0])
    if kind == "count":
        row[0] += amount
    elif kind == "own":
        row[1] += amount
    elif kind == "top":
        row[2] += amount
    elif kind == "inside":
        row[2] += amount
    elif kind == "direct":
        row[3] += amount


def summarize_transaction(trace_path: Path, fast_dir: Path, tx_hash: str) -> dict:
    """Opcode lines and plot totals for one traced transaction."""
    steps = iter_steps(trace_path)
    names = [opcode_name(step["op"]) for step in steps]
    costs = net_step_costs(steps, names)
    calls = load_json(fast_dir / "txCalls.json") or {}
    frames_left = list(iter_call_frames(calls))
    root_to = calls.get("to")
    ctx = [hex_addr(root_to) if root_to else f"create:{tx_hash}"]
    creates = 0
    storage = Storage(seed_storage(fast_dir))
    open_frames: list[dict] = []
    lines = []
    op: dict[str, list[int]] = {}
    static_unmatched = 0

    def close_until(next_depth: int) -> None:
        while open_frames and open_frames[-1]["child_depth"] > next_depth:
            frame = open_frames.pop()
            _add(op, frame["name"], "inside", frame["inside"])
            _add(op, frame["name"], "direct", frame["direct"])
            storage.leave(frame["revert"])

    for index, step in enumerate(steps):
        name = names[index]
        cost = costs[index]
        depth = step["depth"]
        nxt = steps[index + 1] if index + 1 < len(steps) else None
        next_depth = nxt["depth"] if nxt else 0
        stack = step.get("stack") or []
        del ctx[depth:]
        contract = ctx[-1] if ctx else f"create:{tx_hash}"

        call_frame = None
        if name in CALLS:
            if frames_left and frame_type(frames_left[0]) == name:
                call_frame = frames_left.pop(0)
            elif name == "STATICCALL":
                static_unmatched += 1

        sstore = None
        if name == "SSTORE" and len(stack) >= 2:
            slot = as_int(stack[-1])
            post = as_int(stack[-2])
            key = (contract, slot)
            sstore = (contract, slot, storage.get(key), post)
            storage.set(key, post)
        if name in KEPT:
            line = {"op": name, "i": index, "g": cost}
            if name == "SLOAD" and stack:
                slot = as_int(stack[-1])
                result = as_int(nxt["stack"][-1]) if nxt and nxt.get("stack") else 0
                line.update(c=contract, slot=hex_word(slot), result=hex_word(result))
            elif sstore is not None:
                contract_s, slot, pre, post = sstore
                line.update(c=contract_s, slot=hex_word(slot), pre=hex_word(pre), post=hex_word(post))
            elif name == "KECCAK256" and nxt and nxt.get("stack"):
                line["output"] = hex_word(nxt["stack"][-1])
            elif name in CALL_TO or name in CALL_SELF:
                if len(stack) >= 2:
                    line["to"] = hex_addr(stack[-2])
            if name == "STATICCALL" and call_frame is not None:
                line.update(
                    to=str(call_frame.get("to") or ""),
                    input=str(call_frame.get("input") or "0x"),
                    output=str(call_frame.get("output") or "0x"),
                )
            lines.append(line)

        _add(op, name, "count", 1)
        if cost:
            _add(op, name, "own", cost)
        opens = name in CALLS and nxt is not None and next_depth > depth
        if opens:
            for frame in open_frames:
                frame["inside"] += cost
            open_frames.append(
                {
                    "name": name,
                    "inside": cost,
                    "direct": cost,
                    "child_depth": next_depth,
                    "revert": bool(call_frame and call_frame.get("error")),
                }
            )
            storage.enter()
            if name in CALL_TO and len(stack) >= 2:
                ctx.append(hex_addr(stack[-2]))
            elif name in CALL_SELF and ctx:
                ctx.append(ctx[-1])
            elif name in CREATE:
                creates += 1
                ctx.append(f"create:{tx_hash}:{creates}")
        elif cost:
            for frame in open_frames:
                frame["inside"] += cost
            if name in CALLS:
                _add(op, name, "inside", cost)
                _add(op, name, "direct", cost)
            elif open_frames and depth == open_frames[-1]["child_depth"]:
                open_frames[-1]["direct"] += cost
            elif not open_frames:
                _add(op, name, "top", cost)
        close_until(next_depth)

    posts = expected_posts(fast_dir)
    mismatches = sum(1 for key, value in posts.items() if storage.values.get(key, 0) != value)
    return {
        "lines": lines,
        "exec": sum(costs),
        "op": op,
        "sstore_mismatch": mismatches,
        "static_unmatched": static_unmatched,
    }


def summarize_tx(trace_path: str, fast_dir: str, tx_hash: str) -> dict:
    """Summarize one transaction. The returned ops list is only storage, hash, and calls."""
    result = summarize_transaction(Path(trace_path), Path(fast_dir), tx_hash)
    return {
        "ops": result["lines"],
        "exec": result["exec"],
        "op": result["op"],
        "sstore_mismatch": result["sstore_mismatch"],
        "static_unmatched": result["static_unmatched"],
    }


def frame_type(node: dict) -> str:
    return str(node.get("type") or "").upper()
