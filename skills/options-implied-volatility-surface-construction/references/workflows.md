# Workflows for Options Implied Volatility Surface Construction

1. **Market Quote Ingestion & Black-Scholes Inversion**:
   - Invert Black-Scholes option price to derive implied volatility.
2. **Parametric Volatility Smile Fitting**:
   - Fit strike moneyness (m = K/S) to quadratic smile: IV(m) = ATM + alpha*(m - 1.0) + beta*(m - 1.0)^2.
3. **No-Arbitrage Audit**:
   - Verify calendar variance monotonicity (w(t2) >= w(t1)) and butterfly convexity.
4. **Audit Report Generation**:
   - Output structured IV surface construction report.
