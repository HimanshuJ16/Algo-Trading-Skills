# Operational Checklist for Tax Reconciliation

## Pre-Reconciliation
- [ ] Ensure all internal pricing data for Dec 31st is finalized.
- [ ] Verify that all corporate actions (splits, mergers, spin-offs) for the tax year have been successfully processed in the internal ledger.
- [ ] Confirm no open tickets remain for unresolved P&L breaks.

## Execution
- [ ] Download official 1099-B data directly from the broker portal (ensure it is the "Final", not "Preliminary" version).
- [ ] Run the reconciliation engine with standard tolerances (e.g., $0.05).

## Post-Reconciliation
- [ ] Manually review all flagged discrepancies over $100.00.
- [ ] Document justification for any unresolvable discrepancies for audit defense.
- [ ] Export verified Form 8949 data payload.