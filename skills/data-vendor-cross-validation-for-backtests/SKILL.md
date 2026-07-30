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
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill before running production backtests on historical price data from a single vendor. Single-vendor data frequently contains silent errors: missing bars during exchange outages, incorrect OHLC values due to errata, phantom volume spikes from duplicate reporting, or stale prices from non-trading hours leaking in. Cross-validating against a second independent vendor catches these errors before they corrupt strategy performance metrics.

## Prerequisites

- OHLCV bar data from primary vendor (Vendor A).
- OHLCV bar data from secondary vendor (Vendor B) for the same symbol and date range.

## Workflow

1. **Align Bar Timestamps**:
   - Join Vendor A and Vendor B bars on timestamp keys, identifying bars present in only one vendor.

2. **Compute Per-Bar Price Discrepancy**:
   - Close price delta: $\Delta_{\text{close}} = |C_A - C_B| / C_A \times 10^4$ (bps).
   - Flag bars exceeding discrepancy threshold (e.g., $>50$ bps).

3. **Audit Missing Bar Coverage**:
   - Count bars present in Vendor A but missing from Vendor B (and vice versa).
   - Flag if missing bar ratio exceeds tolerance (e.g., $>1\%$).

4. **Generate Cross-Validation Report**:
   - Emit pass/fail verdict with per-bar discrepancy statistics and missing bar counts.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Timezone Mismatch Between Vendors**: Vendor A reports in UTC, Vendor B in exchange local time, causing systematic 1-bar offset.
- **Adjusted vs Unadjusted Prices**: One vendor provides split-adjusted closes while the other provides raw closes, creating false discrepancies.
- **Ignoring Volume Discrepancies**: Focusing only on price while ignoring $3\times$ volume spikes that indicate duplicate trade reporting.

## Verification

- Submit two aligned datasets with a $100$ bps price discrepancy on specific bars, verify flagging.
- Submit datasets with $>1\%$ missing bar ratio, verify fail verdict.
- Run `python scripts/test_vendor_cross_validator.py` and confirm 100% pass rate.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `multi-year-regime-coverage-requirement`
---
