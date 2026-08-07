---
name: vendor-specific-adjustment-methodology-reconciliation
description: "Institutional market data skill for modeling vendor corporate action adjustment methodologies (CRSP Total Return, Bloomberg Proportional, Refinitiv Split-Only, Raw Unadjusted), computing price/volume cumulative adjustment factors, detecting cross-vendor divergence anomalies, and generating audit reconciliation reports."
domain: Market Data Architecture & Quantitative Research
subdomain: Corporate Action Adjustments & Data Hygiene
tags:
- corporate-actions
- vendor-reconciliation
- crsp
- bloomberg
- refinitiv
- adjustment-factors
- stock-splits
- cash-dividends
brokers_frameworks:
- crsp
- bloomberg-bpipe
- refinitiv-elektron
- factset
- polygon-io
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when ingesting historical price series from multiple market data vendors (e.g. CRSP, Bloomberg, Refinitiv, FactSet), building multi-vendor backtesting databases, or auditing corporate action adjustment discrepancies.

This skill provides institutional mechanisms to:
- Model vendor-specific corporate action adjustment rules (`CRSP_TOTAL_RETURN`, `BLOOMBERG_PROPORTIONAL`, `SPLIT_ONLY_PRICE_RETURN`, `RAW_UNADJUSTED`).
- Calculate continuous **Cumulative Price & Volume Adjustment Factors** ($\text{Factor}_{\text{div}} = 1 - \frac{D}{P_{\text{cum}}}$, $\text{Factor}_{\text{split}} = \frac{1}{\text{Split Ratio}}$).
- Detect **Cross-Vendor Price Divergences** exceeding tolerance thresholds ($\text{Diff \%} > \text{tolerance\_pct}$).
- Generate **Reconciliation Audit Reports** detailing divergence counts, maximum percentage variances, and anomaly flags.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `typing`).
- Raw or vendor-adjusted price bar histories and corporate action events databases.

## Workflow

1. **Ingest Corporate Action Events**: Construct `CorporateAction` instances specifying ex-date, action type (`CASH_DIVIDEND`, `STOCK_SPLIT`, `SPECIAL_DIVIDEND`, `SPIN_OFF`), cash amount, and split ratio.
2. **Calculate Cumulative Adjustment Factors**: Call `calculate_adjustment_factors(bars, actions, methodology)` to compute date-specific price and volume adjustment multipliers.
3. **Adjust Raw Price Series**: Execute `adjust_price_series(bars, actions, methodology)` to generate vendor-conforming adjusted price histories.
4. **Reconcile Cross-Vendor Price Series**: Invoke `reconcile_vendor_series(symbol, series_a, vendor_a_name, series_b, vendor_b_name, tolerance_pct=0.5)` to detect methodology divergence anomalies.
5. **Audit Discrepancies**: Review the resulting `ReconciliationReport` to identify missing corporate actions or vendor factor calculation variances.

## Common Pitfalls

- **Mixing Total Return & Price Return Feeds**: Mixing a CRSP Total Return series (dividend-adjusted) with a Refinitiv Price Return series (split-adjusted only) introduces false price jumps ($\Delta P$), corrupting quant factor signals.
- **Volume Un-Adjustment**: Adjusting historical prices downward for a stock split without scaling historical volumes UPWARD ($V_{\text{adj}} = V_{\text{raw}} \times \text{Split Ratio}$) skews liquidity metrics and turnover backtests.
- **Spin-Off Market Capitalization Distortions**: Vendors treat spin-offs inconsistently (some subtract cash value $P - V_{\text{spin}}$, others issue synthetic child contracts). Unreconciled spin-off adjustments create severe PnL jumps.
- **Cum-Date Price Mis-Alignment**: Calculating dividend adjustment factors using post-dividend ex-date prices instead of pre-dividend cum-date prices ($P_{\text{cum}}$) produces mathematical factor errors.

## Verification

Run the unit test suite to validate stock split factor math, cash dividend proportional adjustments, cross-vendor reconciliation matching, and divergence anomaly detection:

```bash
python -m unittest discover -s skills/vendor-specific-adjustment-methodology-reconciliation/scripts
```

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `vendor-lock-in-risk-for-proprietary-custody-formats`
- `unicode-and-encoding-issues-in-global-instrument-names`
- `tick-size-pilot-program-impact-assessment`

