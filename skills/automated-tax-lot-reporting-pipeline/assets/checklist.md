# Checklist for Tax Lot Pipelines

- [ ] Confirm the engine accurately splits lots when `sell_quantity` < `lot_quantity`.
- [ ] Confirm HIFO strategy successfully minimizes capital gains compared to FIFO for the identical dataset.
- [ ] Verify that completely depleted lots are popped/removed from the open ledger memory space to prevent OOM errors on high-frequency portfolios.
- [ ] Run test suite: `python scripts/test_automated_tax_lot_reporting_pipeline.py`.

## Sign-off
- Quant/Accounting Engineer: ___________________________
- Date: ___________________________