# Agent handoff: opcode gas analysis

User-facing design is `DATA_ANALYSIS.md`. This file is the continuation note.

## What this chat built

Two-stage opcode analysis. Stage 1 summarizes traces once. Stage 2 only plots.

- `tx_opcode_summary.py` is the summarizer CLI (moved here from `combined_data_analysis.py`). One process per transaction. `--resume` skips a block that already has both `txs.jsonl` and `block.json`, and deletes leftover `parts/` and `opcodes.jsonl`.
- `combined_data_analysis.py` only draws the horizontal bar chart from `opcode_summary/`. Pie charts, `--collect-gas`, and `--summarize` were removed from this file.
- `gas_sharing_analysis.py` reads `txs.jsonl` and `block.json` for storage and hashing, and `block_result/<block>/<tx>/txCalls.json` for STATICCALL frame gas. No RPC and no re-summary. `parse_start_blocks` still lives there. Summarize still uses RPC to order transactions and read receipts.

```bash
python tx_opcode_summary.py --trace-dir raw_block --data-dir block_result \
  --summary-dir opcode_summary --start-block 24000000 --range 2 --jobs 8

python combined_data_analysis.py --summary-dir opcode_summary \
  --start-block 24000000 --range 2 --call-gas own --top 10 --ignore-calls -o opcodes.pdf

python gas_sharing_analysis.py --summary-dir opcode_summary --data-dir block_result \
  --start-block 24000000 --range 2 --ignore-simple -o gas_sharing.json
```

RPC for summarize: `http://localhost:8545` (`eth_getBlockByNumber` false for tx order, `eth_getBlockReceipts`). Fast-mode dir is `block_result` (symlink to `../evm-tx-analysis/block_result`). This machine's block 24000000 fast data is legacy `txCalls.json` + `txStateDiff.json` only. Code also accepts `txStateRead.json` / `txStateWrite.json`.

## Summary format

`opcode_summary/<block>/block.json`: header `gasUsed`, `txs`, `simpleTxs`, `simpleGas`, `denomGas`, `execGas`, `opcodes`, `sstoreMismatch`, `staticUnmatched`.

Opcode rows: non-calls `[count, own, top]`. Calls `[count, own, inside, direct]`. Call set is `CALLS` in `tx_opcode_summary.py`: CALL, STATICCALL, DELEGATECALL, CALLCODE, CREATE, CREATE2.

`txs.jsonl`: one JSON object per tx in header order. Simple tx is a border only. Other txs have `ops` for SLOAD, SSTORE, KECCAK256, and `CALLS` only, in trace order, with step index `i`. Params: SLOAD result and KECCAK output from the next stack word; SSTORE pre (seeded from fast-mode pre, then replayed) and post (stack); STATICCALL to/input/output from the matching `txCalls` frame; other calls get `to` from the stack. Each call also has `d` (depth), `inside` (net gas until return), `direct` (that window excluding nested calls), and `error` when the paired frame reverted. Do not store every opcode. Do not keep `parts/`. Summaries written before those call fields existed still load, but static-call savings then use own gas `g` and do not skip nested hits. Re-summarize before trusting static-call numbers.

Simple tx: receipt gas == 21000, or missing/empty trace. `denomGas` is receipt gas of the other txs. Opcode gas % uses `denomGas`, not header gas and not a labeled remainder.

## Gas accounting

Trace `gasCost` for a call includes the forwarded stipend. The negative `gasCost` on the return is netted onto that call (`net_step_costs`). After that, `own` sums to `execGas` (trace consumption). `inside` overlaps when nested; do not sum it. `direct` does not overlap. `top` is non-call gas at depth 1. `top + all direct = execGas`.

`--call-gas own` (default) plots index 1. `--call-gas inside` plots `direct` (index 3) for calls and `top` (index 2) for everything else.

`execGas` is less than `denomGas` (intrinsic gas and refunds). Opcode 0x20 is named KECCAK256.

## Bar chart (do not regress)

`combined_data_analysis.py`. One group per opcode: frequency bar above the gas bar. Named opcodes sorted by gas descending. **Other** is always the bottom group, even when its gas is larger than a named opcode. `--top` (default 10) keeps the largest gas opcodes; the rest is Other.

Frequency % is of all steps. Gas % is of `denomGas`. The two series are scaled to their own maxima; the text is the true percent. Colors: Wong blue `#0072B2` frequency, orange `#E69F00` gas. Hatch `//` and `\\`, face alpha `BAR_ALPHA` (0.6), solid edge so the hatch stays visible. Serif, `pdf.fonttype` 42. Legend outside upper center.

Style constants at the top of the file (user edits these, not CLI flags): `FIG_SIZE` currently `(8, 3.5)`, `FONT_SIZE`, `BAR_SIZE`, `BAR_DISTANCE`, `GROUP_DISTANCE`, `BAR_ALPHA`, colors, hatches. The three spacing numbers are weights (two bars, gap between them, gap between groups) and are normalized to one group.

`--ignore-calls` drops CALL, STATICCALL, DELEGATECALL, CALLCODE from the bars and from Other, and subtracts their plotted gas from the gas denominator. CREATE and CREATE2 stay. Frequency denominator is still all steps.

Check a render with `pdftoppm -png` and read the PNG. Last known good input: `/tmp/opcode_summary_small/{24000000,24000001}`. Two-block totals: 373 txs, header gas 85,521,218, analysis txs 270, denom 83,356,938, steps 3,471,001, opcode gas 76,391,676. With `--ignore-calls` and `--call-gas own`, removed call gas is 4,208,655 and the chart denom is 79,148,283. SSTORE goes from 43.9% to 46.2%. Other stays at the bottom.

## Diagnostics, not bugs

`sstore mismatches`: replayed final slot value != fast-mode post. Typical cause is CREATE's placeholder address, or a reverted SSTORE whose frame had no error so the journal did not roll back. Does not change opcode gas. `staticcall frames unmatched`: a STATICCALL whose next unused call-tree frame was not STATICCALL (DFS preorder, root excluded). User's run of blocks 23500006–23500035 had mostly 1 SSTORE mismatch per flagged block (7 in 23500027, 11 in 23500035) and 0 unmatched static calls. Do not "fix" those counts unless asked.

## Gas sharing

`gas_sharing_analysis.py --summary-dir`. One pass over `txs.jsonl` per interval. The cache starts empty each interval. Block totals come from `block.json` (`gasUsed`, `txs`, `simpleTxs`, `simpleGas`, `denomGas`). Do not add an RPC check back.

- `--ignore-simple` drops simple transfers from the block-gas denominator. A simple tx has `simple: true` or `gasUsed == 21000`. On `/tmp/opcode_summary_small` that denominator is 83,356,938 (`denomGas`); without the flag it is 85,521,218. Opcode counts still match `block.json`: SLOAD 15569, SSTORE 5808, KECCAK256 10292, STATICCALL 1571, 373 txs.
- STATICCALL has one cache, ideal `(to, input, output)`, read from fast-mode `txCalls.json`. There is no consecutive-repeat case. A hit saves that frame's `gasUsed` minus `--cache-hit-cost` (default 3) and does not walk the children. `error` frames are not hits. Do not tell the user to re-summarize for this; the old `txs.jsonl` has no `inside`, and the call tree already has the inclusive gas.
- SLOAD/SSTORE use only pure `(c, slot)` counting. No access-list / `gasCost` warm model. SLOAD saves on a range hit that was not seen earlier in the same tx. SSTORE saves on every range hit. KECCAK256 saves when `output` was seen.
- Hash savings and static-call savings still overlap. `inside` does not include precompile gas that exists only in `txCalls.json`.

## Left undone

No commit was requested. Static-call savings no longer depend on re-summarizing. `d` and `inside` on call ops are unused by the sharing script while `txCalls.json` is present.
