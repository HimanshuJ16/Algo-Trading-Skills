---
name: data-vendor-cross-validation-for-backtests
description: Use when validating historical price data integrity by cross-referencing
  OHLCV bars from two independent data vendors, detecting discrepancies in price,
  volume, and missing-bar coverage before they corrupt backtest results.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- data-validation
- cross-vendor
- data-quality
- price-discrepancy
- missing-bars
brokers_frameworks:
- Data Vendor Cross Validator
- Python Statistics
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill before running production backtests on historical price data from a single vendor. Single-vendor data frequently contains silent errors: missing bars during exchange outages, incorrect OHLC values due to errata, phantom volume spikes from duplicate reporting, or stale prices from non-trading hours leaking in. Cross-validating against a second independent vendor catches these errors before they corrupt strategy performance metrics.

## When NOT to Use

- **You only have one vendor.** This skill compares two datasets; it cannot detect an error that both copies of the same underlying feed share. Two resellers of the same primary source are *not* two independent vendors.
- **The two feeds are not the same product.** A consolidated tape (all US venues) versus a primary-exchange-only feed will legitimately disagree on volume and on the closing print. Reconcile the product definitions first, or the report is all false positives.
- **You need a single canonical price rather than a pass/fail gate.** This skill answers "do my two sources agree enough to backtest on?", not "which price is right?". For arbitration between disagreeing sources see `multi-source-price-reconciliation-tie-breaking`.
- **Tick or quote data.** The comparison is bar-keyed on an exact timestamp string; trade-by-trade or L2 data needs sequence-aware reconciliation, not a bar join.

## Prerequisites

- OHLCV bar data from primary vendor (Vendor A). Vendor A is the *reference* vendor: the bps delta is expressed as a fraction of the Vendor A close, so the metric is asymmetric — pass the production vendor as A.
- OHLCV bar data from secondary vendor (Vendor B) for the same symbol and date range.
- **Both vendors normalised to one canonical timestamp string** (UTC, identical format). Timestamps are joined as exact strings; the validator cannot infer a timezone offset, only report the total loss of overlap one causes.
- Both vendors on the **same adjustment basis** (both split/dividend adjusted, or both raw) and the same bar convention (open-time vs. close-time labelling).

## Workflow

1. **Index Bars and Detect Duplicates**:
   - Build a timestamp → bar index per vendor. A repeated timestamp within one vendor is an integrity failure, not something to silently de-duplicate — it is a common symptom of duplicate trade reporting or a re-run backfill.

2. **Align Bar Timestamps**:
   - Join Vendor A and Vendor B bars on timestamp keys, identifying bars present in only one vendor.
   - If the overlap is *zero* while both datasets are non-empty, stop and check timestamp normalisation before interpreting the missing-bar ratio — this is almost always a format/timezone mismatch, not genuinely disjoint data.

3. **Screen Bar Integrity Before Comparing**:
   - Reject bars with a non-finite (NaN/Inf) close or volume, a negative volume, or a zero reference close. These are unusable, not in agreement — scoring them as a 0 bps match is how a corrupt feed passes a green gate.

4. **Compute Per-Bar Price Discrepancy**:
   - Close price delta: $\Delta_{\text{close}} = |C_A - C_B| / |C_A| \times 10^4$ (bps). The absolute denominator keeps the metric correct for legitimately negative prices (e.g. WTI crude settlements, April 2020).
   - Flag bars exceeding discrepancy threshold (default: $>50$ bps, exclusive).

5. **Audit Volume Ratios**:
   - Symmetric ratio $\max(V_A, V_B) / \min(V_A, V_B)$ per matched bar; flag above the spike threshold (default $3\times$).
   - Volume flags are for **audit, not verdict**: consolidated-versus-primary feed differences produce legitimate volume gaps. Price and integrity failures fail the run; volume flags do not.

6. **Audit Missing Bar Coverage**:
   - Count bars present in Vendor A but missing from Vendor B (and vice versa), over the union of timestamps.
   - Fail if missing bar ratio exceeds tolerance (default: $>1\%$).

7. **Generate Cross-Validation Report**:
   - Emit pass/fail verdict with per-bar discrepancy statistics, integrity issues, volume flags, and missing bar counts.

> Full procedure: see `references/workflows.md`.
> Default tolerances: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Timezone Mismatch Between Vendors**: Vendor A reports in UTC, Vendor B in exchange local time, causing a systematic 1-bar offset — or, with differing string formats, zero matches and a misleading "100% missing bars" verdict.
- **Adjusted vs Unadjusted Prices**: One vendor provides split-adjusted closes while the other provides raw closes, creating false discrepancies (see `adjusted-vs-unadjusted-price-series-pitfalls`).
- **Ignoring Volume Discrepancies**: Focusing only on price while ignoring $3\times$ volume spikes that indicate duplicate trade reporting.
- **Treating Sentinel Values as Agreement**: Vendors emit `0.0` or `NaN` closes for no-trade bars. A relative-difference check guarded by `if close > 0` scores those as a perfect match, so the worst bars in the dataset become the ones most likely to pass.
- **Silently De-duplicating Repeated Timestamps**: A dict-keyed join makes duplicate bars vanish (last wins), destroying the very evidence of duplicate reporting the audit is meant to find. Only the union bar count reveals it, and only if someone reads it.
- **Reading a PASS on an Empty Dataset**: If the loader silently returns nothing, an unguarded validator reports zero discrepancies and zero missing bars. Assert non-empty input before trusting the verdict.

## Verification

- Submit two aligned datasets with a $100$ bps price discrepancy on specific bars, verify flagging.
- Submit datasets with $>1\%$ missing bar ratio, verify fail verdict.
- Submit a bar whose Vendor A close is `NaN` or `0.0` against a real Vendor B price, verify it is reported as an integrity failure and not as agreement.
- Submit duplicate timestamps within one vendor, verify they are reported rather than collapsed.
- Submit two empty datasets, verify `ValueError` rather than a PASS verdict.
- Run `python scripts/test_vendor_cross_validator.py` and confirm 100% pass rate.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `multi-year-regime-coverage-requirement`
- `multi-source-price-reconciliation-tie-breaking`
- `cross-vendor-timestamp-precision-reconciliation`
