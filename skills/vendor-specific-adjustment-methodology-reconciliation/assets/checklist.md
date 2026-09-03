# Institutional Vendor Corporate Action Adjustment Operations Checklist

## Data Ingestion & Corporate Action Parsing
- [ ] **Raw Exchange Price Ingestion**: Unadjusted OHLCV is stored independently from any vendor-adjusted feed, so adjustment is never applied twice.
- [ ] **Single-Symbol Batches**: Bars and actions are grouped per symbol; the engine rejects mixed-symbol input rather than cross-contaminating a series.
- [ ] **Duplicate Date Sweep**: The raw series has no duplicate bar dates and no non-finite or non-positive prices.
- [ ] **Corporate Action Master Sync**: Ex-dates, action types (`CASH_DIVIDEND`, `SPECIAL_DIVIDEND`, `STOCK_SPLIT`, `REVERSE_SPLIT`, `SPIN_OFF`), cash amounts and split ratios are parsed from the vendor record.
- [ ] **Split Ratio Convention**: `split_ratio` is confirmed to be **new shares per old share** in both directions — `2.0` for 2-for-1, `0.1` for 1-for-10 reverse. An inverted ratio produces a plausible-looking but wrong series.
- [ ] **Cum-Date Price Alignment**: `cum_price` is the close on the last session *before* the ex-date, not the ex-date close.
- [ ] **Spin-Off Valuation**: `cash_amount` for a `SPIN_OFF` carries the per-share value of the distributed entity, and its source is recorded.
- [ ] **Not-Yet-Effective Actions**: If the action feed includes announced events, `as_of=` is set so nothing adjusts history before its ex-date.

## Factor Calculation & Price Adjustment
- [ ] **Methodology Declared First**: `CRSP_TOTAL_RETURN`, `BLOOMBERG_PROPORTIONAL`, `SPLIT_ONLY_PRICE_RETURN` or `RAW_UNADJUSTED` is chosen explicitly and recorded with the output series.
- [ ] **Cumulative Factor Computation**: `calculate_adjustment_factors()` accumulates over `ex_date > bar_date`, so ex-dates on holidays or in data gaps still adjust earlier history.
- [ ] **Separate Volume Factor**: Volume is scaled by share-count changing actions only. Confirm that a cash dividend or spin-off leaves historical volume **unchanged**.
- [ ] **Split Dollar-Volume Invariance**: Across a split, $P_{\text{adj}} \times V_{\text{adj}} = P_{\text{raw}} \times V_{\text{raw}}$.
- [ ] **Same-Ex-Date Aggregation**: Distinct actions sharing an ex-date multiply; same-date ordinary cash dividends sum before the factor is computed.
- [ ] **Rounding Policy**: Stored series are unrounded; `price_decimals` is used for display only.
- [ ] **Validation Errors Are Data Bugs**: Every `AdjustmentValidationError` is traced back to the corporate-action record and fixed there, never suppressed at the call site.

## Cross-Vendor Reconciliation & Audit Alerting
- [ ] **Cross-Vendor Reconciliation Audit**: `reconcile_vendor_series()` is run against the second vendor before either series is merged into research data.
- [ ] **Tolerance Calibrated**: `tolerance_pct` is set for the instrument's price level and liquidity, not left at 0.5% for a sub-dollar name.
- [ ] **Coverage Reviewed**: `coverage_pct`, `dates_only_in_a` and `dates_only_in_b` are read alongside `status`; `min_coverage_pct` is set where overlap is part of the audit.
- [ ] **Uncomparable Dates Triaged**: Divergences with `reason` of `NON_FINITE_PRICE` or `NON_POSITIVE_MID_PRICE` are raised as data-corruption incidents, not methodology findings.
- [ ] **Divergence Anomaly Quarantine**: Symbols failing reconciliation are quarantined from alpha models until the driving corporate action is identified.
- [ ] **Audit Report Distribution**: The `ReconciliationReport`, the declared methodology, and the action records used are delivered together to quantitative research and risk management.
