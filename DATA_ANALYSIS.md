# Data analysis

Scripts that measure how gas is spent in collected Ethereum blocks, and how much of it could be saved by sharing repeated storage, hashing, and static calls. Collection itself is `python main.py` (full EIP-3155 trace) and `python main.py --fast` (call tree and storage). This document covers the analysis side.

Two inputs:

| Path | Produced by | Contents |
| --- | --- | --- |
| `raw_block/<block>/<tx>/` | full collection | `txTraceEIP3155.json` (one step per line: `pc`, `op`, `gas`, `gasCost`, `stack`, `depth`, …), plus `txTest.json` and `txStats.json` |
| `block_result/<block>/<tx>/` | `python main.py --fast` | `txCalls.json` (callTracer tree), and storage either as `txStateRead.json` / `txStateWrite.json` or the older `txStateDiff.json` (`pre` / `post`) |

`--start-block` on the gas-sharing and opcode scripts accepts one block or a comma-separated list of interval starts. `--range N` is the number of consecutive blocks in each interval. `24000000,24100000 --range 5` is blocks 24000000–24000004 and 24100000–24100004. Each interval is a fresh cache. The opcode charts pool every interval into one figure. Gas sharing prints each interval and, when there is more than one, an unweighted average.

## Scripts

| Script | Reads | Writes |
| --- | --- | --- |
| `data_analysis.py` | fast traces | storage-distance and STATICCALL histograms (PDF + JSON) |
| `gas_sharing_analysis.py` | `opcode_summary/` (`txs.jsonl`, `block.json`) | text report and optional JSON of cache savings |
| `tx_opcode_summary.py` | full traces, fast traces, RPC | `opcode_summary/` (`block.json`, `txs.jsonl`) |
| `combined_data_analysis.py` | `opcode_summary/` | opcode frequency and gas bars (PDF) |

## Fast-trace reports (`data_analysis.py`)

```bash
python data_analysis.py --storage --start-block 24200000 --range 12 -o storage
python data_analysis.py --staticcall --start-block 24200000 --range 12 -o static --top 20
```

`--storage` walks `txStateWrite.json` (or a legacy diff). Each rewritten slot is one write. The histogram is the distance, in writes, since the same `(address, slot)` was written, with distances above 10 merged into one bar. It also reports the share of rewrites that store the same value.

`--staticcall` walks `txCalls.json`. It reports what fraction of calls are `STATICCALL`, how often the same `(to, input)` returns the same output on consecutive calls, a histogram of that run length, and the top `--top` `(contract, input, output)` groups by gas.

Both modes write `<prefix>.pdf` and `<prefix>.json`. `--data-dir` defaults to `block_result`.

## Gas sharing (`gas_sharing_analysis.py`)

```bash
python gas_sharing_analysis.py --summary-dir opcode_summary \
  --start-block 24000000,24100000 --range 5 -o gas_sharing.json
```

This reads `txs.jsonl` and `block.json` for storage and hashing, and `block_result/<block>/<tx>/txCalls.json` for static calls. It does not re-read the full trace and it does not call RPC. The cache is empty at the start of each interval. An old summary is enough: static-call frame gas comes from fast mode.

**Static calls** use one cache: the first `(to, input, output)` pays full gas, and a later identical triple is a hit. Saved gas is that frame’s `gasUsed` — the whole call, including the opcodes inside it — minus `--cache-hit-cost` (default 3). A hit does not descend into that frame. Frames with `error` are not hits. Static-call gas in the denominator is the `gasUsed` of a `STATICCALL` that is not itself nested in a `STATICCALL`. `--data-dir` defaults to `block_result`.

**Storage and hashing** use `(c, slot)` for SLOAD and SSTORE, and `output` for KECCAK256. A repeat is a key seen earlier in the interval. Same-transaction warm means it was seen earlier in this transaction. There is no access-list model. An SLOAD saves `max(0, g - --warm-cost)` on an interval hit that is not already warm in the transaction (`--warm-cost` default 100). An SSTORE saves `max(0, g - --sstore-warm-cost)` on every interval hit (`--sstore-warm-cost` default 100). KECCAK256 saves `max(0, g - --cache-hit-cost)` when its output was already seen.

Block gas defaults to the sum of receipt `gasUsed`. `--ignore-simple` drops simple transfers (the `simple` flag, or `gasUsed == 21000`) from that denominator. On the two-block summary that is `denomGas`, 83,356,938 instead of 85,521,218. `block.json` supplies `gasUsed`, tx count, simple txs, and `denomGas`; the script checks that the tx records sum to `gasUsed`.

Savings are not additive. A hash inside a cached static call is counted by both.

## Opcode frequency and gas

Two stages. `tx_opcode_summary.py` reads each full trace once and writes a small summary. `combined_data_analysis.py` only reads that summary, so a different `--start-block` / `--range` does not scan the traces again.

```bash
python tx_opcode_summary.py \
  --trace-dir raw_block --data-dir block_result \
  --summary-dir opcode_summary \
  --start-block 24000000,24100000 --range 5 --jobs 8

python combined_data_analysis.py \
  --summary-dir opcode_summary \
  --start-block 24000000 --range 2 --call-gas own \
  --top 10 -o opcodes.pdf
```

`--jobs` is the number of processes (default: CPU count). One transaction per process. `--resume` skips a block that already has both summary files. `--endpoint` is used to read the block’s transaction list and `eth_getBlockReceipts`.

### What stage 1 writes

`opcode_summary/<block>/block.json` is the plot input. It has header `gasUsed`, transaction count, how many transactions were simple, their gas, `denomGas`, `execGas`, and every opcode that ran. Non-call opcodes are `[count, own, top]`. Call opcodes (`CALL`, `STATICCALL`, `DELEGATECALL`, `CALLCODE`, `CREATE`, `CREATE2`) are `[count, own, inside, direct]`.

`opcode_summary/<block>/txs.jsonl` is one JSON object per transaction, in header order. A simple transfer is only the border. Any other transaction has an `ops` array of the steps a later sharing analysis needs, in trace order. `i` is the step index in that transaction.

```json
{"tx":"0x...","n":0,"gasUsed":21000,"simple":true}
{"tx":"0x...","n":1,"gasUsed":122401,"simple":false,"ops":[
  {"op":"SLOAD","i":40,"g":2100,"c":"0xcontract","slot":"0xslot","result":"0xvalue"},
  {"op":"KECCAK256","i":55,"g":42,"output":"0xhash"},
  {"op":"SSTORE","i":80,"g":20000,"c":"0xcontract","slot":"0xslot","pre":"0xold","post":"0xnew"},
  {"op":"STATICCALL","i":90,"g":100,"d":1,"inside":4200,"direct":2600,"to":"0xcallee","input":"0xcalldata","output":"0xreturn"},
  {"op":"CALL","i":200,"g":2600,"d":1,"inside":2600,"direct":2600,"to":"0xtarget"}
]}
```

Kept opcodes are SLOAD, SSTORE, KECCAK256, and the call family. PUSH and the other opcodes are counted in `block.json` and are not repeated in `txs.jsonl`.

SLOAD’s result and KECCAK256’s output are the word pushed on the next step. SSTORE’s `pre` starts from the fast-mode pre-state and then follows each write in the transaction; `post` is the value on the stack. The final value of each slot is checked against the fast-mode post-state. STATICCALL `to`, `input`, and `output` are copied from the matching frame in `txCalls.json`. Other calls record the target address from the stack.

### Gas accounting

A call’s `gasCost` in these traces is the drop from the caller’s gas to the callee’s gas, so it includes gas forwarded into the call. The unused part comes back as a negative `gasCost` on the next opcode and is subtracted from that call. After that adjustment, `execGas` is the gas the trace actually consumed. It matches the drop in the trace’s gas counter. It is smaller than receipt gas: intrinsic gas (21000 plus calldata) is charged before the first opcode, and refunds are applied after the last one.

Call opcodes keep two figures:

- `own`: only that opcode’s net cost. Every other opcode keeps its own gas. These sum to `execGas`.
- `inside`: all net gas from that opcode until execution returns to its caller, including nested calls. Nested windows overlap, so `inside` values are not added together.
- `direct`: the part of that window that is not inside a nested call. `top` is the gas of non-call opcodes at depth 1. `top` plus every call’s `direct` also sums to `execGas`.

`--call-gas own` (default) plots `own`. `--call-gas inside` plots `direct` for each call type and `top` for every other opcode, so a rare SSTORE inside a call shows up on the call rather than on SSTORE, and the slices still add up once.

Opcode percent is opcode gas divided by `denomGas`: receipt `gasUsed` of transactions that are not simple. A transaction is simple when its receipt gas is 21000 or it has no non-empty trace. Those transactions stay in the printed block totals (`transactions`, `gas used`) and are left out of the denominator (`analysis transactions`, `analysis gas`). The charts do not draw a remainder slice. The gap between `execGas` and `denomGas` is intrinsic gas and refunds of the transactions that were traced.

### Charts

The chart is one group per opcode, with the frequency bar directly above the gas bar. `--top` (default 10) keeps the opcodes with the most gas under the selected `--call-gas`; everything else is **Other**. Named opcodes are sorted by gas, descending, and **Other** is always the bottom group. Frequency and gas are scaled to their own maxima, and the percentage is written beside each bar. Figure size, bar spacing, group spacing, colors, hatch, and alpha are the constants at the top of `combined_data_analysis.py`. `--ignore-calls` drops `CALL`, `STATICCALL`, `DELEGATECALL`, and `CALLCODE`. Their gas is subtracted from the gas denominator and is not added to **Other**. `CREATE` and `CREATE2` stay.

The last lines of a plot run are the full block totals and the non-simple totals used as the denominator.

### Summarizer warnings

`sstore mismatches` is the number of `(contract, slot)` pairs whose replayed final value differs from the fast-mode post-state. The usual causes are a write inside a contract creation (the replay uses a placeholder address, the diff uses the created address) or a reverted write whose frame was not marked with an error. Opcode gas is unaffected. The `pre` / `post` on those particular writes can be wrong.

`staticcall frames unmatched` is the number of `STATICCALL` opcodes that could not be paired with the next `STATICCALL` frame in `txCalls.json`, so they have no `input` or `output`. Zero means every static call was paired.

Call ops also store `d` (trace depth), `inside` (net gas until the call returns), and `direct` (the part of that window that is not inside a nested call). `error` is set when the paired fast-mode frame reverted. `gas_sharing_analysis.py` uses those fields.
