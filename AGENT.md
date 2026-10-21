# Output format

Each transaction is written under `<output>/<tx_hash>/`.

## Default

```
txTest.json
txStats.json
txTraceEIP3155.json
```

**txTest.json** — eth-tests style fixture for replay (`evm statetest`).

```
envinfo.env          block context (number, timestamp, coinbase, gas limit, base fee, …)
envinfo.transaction  sender, to, data, value, gasLimit, gasPrice, nonce
envinfo.pre          touched accounts: balance, nonce, code, storage
envinfo.post         dummy post hash (not computed)
```

**txStats.json** — aggregates from the opcode trace.

```
opcodes_count
stack / memory / call / return size statistics
addresses, max_depth
storage_accessed
call_traces          per-call caller, address, kind, data, output, success
```

**txTraceEIP3155.json** — one JSON object per line (opcode steps).

```
pc, op, gas, gasCost, memSize, stack, depth, refund
```

## `--fast`

Skips EIP-3155. Uses `debug_traceTransaction` / `debug_traceBlockByNumber`.

```
txCalls.json
txStateRead.json
txStateWrite.json
```

**txCalls.json** — `callTracer` tree (`withLog`).

```
from, to, gas, gasUsed, input, output, value, type, calls[], logs[]
```

**txStateRead.json** / **txStateWrite.json** — flattened storage from two `prestateTracer` runs (default + `diffMode`). Reads are `{address, slot, value}` for slots in the default prestate that are not in the diff (`value` = pre-tx). Writes are `{address, slot, old_value, value}` for slots in the diff (`old_value` = pre-tx, or `0x0` if created; `value` = post-tx, or `0x0` if cleared).
