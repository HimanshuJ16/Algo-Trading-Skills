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
- [ ] Verify that selling without matching lots raises ValueError instead of returning empty list.
- [ ] Verify that overselling raises ValueError instead of just logging warning.
- [ ] Run test suite: `python scripts/test_automated_tax_lot_reporting_pipeline.py`.
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