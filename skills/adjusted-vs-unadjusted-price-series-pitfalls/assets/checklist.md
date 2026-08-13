# Deployment and Sign-off Checklist — Adjusted vs Unadjusted Price Series

## Prerequisites

- [ ] Declare `UNADJUSTED`, `SPLIT_ADJUSTED`, `TOTAL_RETURN_ADJUSTED`, or `UNKNOWN` before auditing.
- [ ] Document vendor, field definitions, factor source/version, publication/as-of timestamps, and adjustment ratio convention.
- [ ] Supply aligned dates, closes, volumes, and actual next-session opens where available.
- [ ] Preserve raw data and corporate-action records before transformation.
- [ ] Define price, volume, notional, dividend, and total-return reconciliation tolerances.

## Validation

- [ ] Run `python -m unittest discover -s skills/adjusted-vs-unadjusted-price-series-pitfalls/scripts`.
- [ ] Verify dates are ISO-formatted and strictly increasing.
- [ ] Verify prices are finite and positive, volumes are finite and non-negative, and all arrays align.
- [ ] Verify split and dividend actions use the documented ratio/amount convention.
- [ ] Verify close-to-open detection when opens are available; record any close-only fallback.
- [ ] Verify known actions, unexplained discontinuities, and mode conflicts are separately reported.

## Transformation and Deployment

- [ ] Apply split adjustments only once and record the factor source and split-index convention.
- [ ] Confirm historical prices divide by the split ratio and volumes multiply by the split ratio before the split date.
- [ ] Keep cash dividends separate unless a documented total-return factor intentionally embeds them.
- [ ] Reject mixed declared series modes before cross-asset feature generation.
- [ ] Persist raw inputs, action records, mode, tolerances, audit report, and transformed-series checksum.

## Rollback and Recovery

- [ ] Retain the raw unadjusted dataset and prior factor/version for rollback.
- [ ] Quarantine symbols with unexplained jumps, ratio mismatches, or failed reconciliation.
- [ ] Re-run audits after vendor corrections, symbol changes, delistings, or action revisions.
- [ ] Rebuild affected features and backtests after changing adjustment mode or factors.
- [ ] Require review of point-in-time availability before releasing revised historical data.

## Post-Deployment Verification

- [ ] Reconcile raw versus transformed price and volume histories.
- [ ] Reconcile split share counts and cash-dividend ledger entries.
- [ ] Compare price-return and total-return outputs under the selected convention.
- [ ] Confirm no later corporate-action factor was used before its historical availability.
- [ ] Record reviewer, code version, data version, factor version, and sign-off date.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________