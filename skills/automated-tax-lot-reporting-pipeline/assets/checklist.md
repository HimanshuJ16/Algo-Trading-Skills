# Checklist for Tax Lot Pipelines

- [ ] Confirm the engine accurately splits lots when `sell_quantity` < `lot_quantity`.
- [ ] Confirm HIFO strategy successfully minimizes capital gains compared to FIFO for the identical dataset.
- [ ] Verify that completely depleted lots are popped/removed from the open ledger memory space to prevent OOM errors on high-frequency portfolios.
- [ ] Confirm input validation rejects invalid trade IDs (empty strings, None values).
- [ ] Confirm input validation rejects invalid symbols (empty strings).
- [ ] Confirm input validation rejects invalid actions (non-TradeAction values).
- [ ] Confirm input validation rejects non-positive quantities (zero, negative).
- [ ] Confirm input validation rejects NaN quantities.
- [ ] Confirm input validation rejects negative prices.
- [ ] Confirm input validation rejects NaN prices.
- [ ] Confirm input validation rejects negative timestamps.
- [ ] Confirm input validation rejects non-finite prices and quantities (+inf, -inf, NaN) so no `nan` can reach `realized_pnl`.
- [ ] Confirm input validation rejects boolean quantities and timestamps (`True` is an `int` in Python).
- [ ] Verify that selling without matching lots raises ValueError instead of returning empty list.
- [ ] Verify that overselling raises ValueError instead of just logging warning.

## Ledger integrity
- [ ] Verify a rejected oversell consumes **no** lots: every open lot retains its prior `remaining_quantity`.
- [ ] Verify a rejected oversell writes **no** `RealizedGainRecord` to `engine.realized_gains`.
- [ ] Verify the ledger is still usable after a rejected oversell — a corrected, smaller sell matches normally.
- [ ] Verify a fractional position bought once and sold in pieces (0.3 sold as 3 x 0.1) closes to zero open lots and totals the independently derived PnL.
- [ ] Confirm `trade_id` values are deduplicated upstream: the engine is not idempotent and a replayed BUY creates a second lot with a duplicate `lot_id`.

## Tax reporting correctness
- [ ] Confirm the reporting jurisdiction actually permits the configured method. Canada requires weighted-average ACB for identical properties and the UK requires same-day / 30-day / s.104 pool matching -- FIFO/HIFO output is invalid for both.
- [ ] Confirm every `RealizedGainRecord` carries `acquired_timestamp_ms` and `disposed_timestamp_ms` (Form 8949 columns (b) and (c)).
- [ ] Confirm the short-term / long-term split is applied downstream: more than one year, counted from the day after acquisition through the disposal date.
- [ ] Confirm Form 8949 columns (f)/(g) adjustments are sourced elsewhere -- this engine models no wash sale, corporate action, or fee adjustment.
- [ ] If HIFO: confirm a contemporaneous standing identification exists, recorded before the sales, not derived from executions after the fact.
- [ ] If digital assets: confirm one engine instance per wallet/account for dispositions on or after 1 Jan 2025 (Treas. Reg. 1.1012-1(j)), not one pooled ledger.

## Test suite
- [ ] Run test suite: `python -m unittest discover -s skills/automated-tax-lot-reporting-pipeline/scripts`.
- [ ] Verify that all tests pass including validation and memory management tests.
- [ ] Confirm error messages are descriptive and actionable for debugging.
- [ ] Verify monitoring functions return correct open lot counts.
- [ ] Confirm deterministic behavior: identical trade sequences produce identical results.
- [ ] Verify strategy switching works correctly (FIFO vs HIFO produce different results).
- [ ] Confirm engine handles fractional share quantities correctly.
- [ ] Verify memory usage doesn't grow unbounded in long-running simulations.
- [ ] Check that sorted lot order is maintained correctly for both strategies.

## Sign-off
- Quant/Accounting Engineer: ___________________________
- Date: ___________________________