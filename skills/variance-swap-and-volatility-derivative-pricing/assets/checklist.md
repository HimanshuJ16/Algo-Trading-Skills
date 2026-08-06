# Institutional Variance Swap & Volatility Derivative Operations Checklist

## Market Data & Option Strip Hygiene
- [ ] **Option Chain Filtering**: Filter out ITM options; strictly ingest OTM Puts ($K < F_0$) and OTM Calls ($K \ge F_0$).
- [ ] **Strike Grid Coverage**: Verify option strikes cover at least $\pm 30\%$ from spot price to prevent tail variance truncation.
- [ ] **Forward Price Calibration**: Compute forward price $F_0 = S_0 e^{r T}$ using exact risk-free yield curve rates.

## Static Replication & Convexity Checks
- [ ] **Log-Contract Integration**: Execute `calculate_fair_strikes()` using non-uniform strike spacing $\Delta K_i = \frac{K_{i+1} - K_{i-1}}{2}$.
- [ ] **Vol vs Var Notional Verification**: Verify Variance Notional $N_{\text{var}} = \frac{N_{\text{vega}}}{2 K_{\text{vol}}}$ to prevent position sizing errors.
- [ ] **Convexity Adjustment Calculation**: Compute convexity adjustment $K_{\text{var}} - K_{\text{vol}}^2$ for Volatility Swap pricing.

## Daily Realized Variance & MTM Risk Audit
- [ ] **Log-Return Sampling**: Calculate daily log-returns $r_i = \ln(S_i / S_{i-1})$ using official exchange closing prices.
- [ ] **Seasoned Contract MTM**: Execute `price_variance_swap_mtm()` blending elapsed realized variance and remaining fair variance strikes.
- [ ] **ISDA CSA Collateral Margining**: Update present value MTM in ISDA variation margin engines.