# Institutional Vendor Corporate Action Adjustment Operations Checklist

## Data Ingestion & Corporate Action Parsing
- [ ] **Raw Exchange Price Ingestion**: Verify unadjusted Open, High, Low, Close, and Volume series are stored independently from adjusted feeds.
- [ ] **Corporate Action Master Sync**: Parse ex-dates, action types (`CASH_DIVIDEND`, `STOCK_SPLIT`, `SPECIAL_DIVIDEND`, `SPIN_OFF`), cash amounts, and split ratios.
- [ ] **Cum-Date Price Alignment**: Confirm dividend adjustment factors use exact closing prices prior to ex-dates ($P_{\text{cum}}$).

## Factor Calculation & Price Adjustment
- [ ] **Cumulative Factor Computation**: Execute `calculate_adjustment_factors()` building backward product multipliers ($F_t$).
- [ ] **Volume Scaling Verification**: Confirm historical volume is scaled inversely ($V_{\text{adj}} = V_{\text{raw}} / F_t$) to preserve total dollar volume traded.
- [ ] **Methodology Standard Matching**: Apply exact vendor methodology (`CRSP_TOTAL_RETURN`, `BLOOMBERG_PROPORTIONAL`, `SPLIT_ONLY`).

## Cross-Vendor Reconciliation & Audit Alerting
- [ ] **Cross-Vendor Reconciliation Audit**: Execute `reconcile_vendor_series()` comparing Vendor A vs Vendor B adjusted price series.
- [ ] **Divergence Anomaly Quarantine**: Quarantine any symbol with percentage differences exceeding tolerance thresholds ($> 0.5\%$).
- [ ] **Audit Report Distribution**: Deliver `ReconciliationReport` to quantitative research and risk management teams.

