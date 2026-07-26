# Workflows for Convertible Bond Arbitrage Data & Pricing

1. **Market Data Ingestion**:
   - Ingest Stock Spot Price $S$, Stock Borrow Fee Rate $r_{borrow}$, CB Clean Price $P_{cb}$, Accrued Interest $AI$, Credit Spread $CS_{bps}$.
2. **Parity & Premium Calculation**:
   - $\text{Parity} = \text{Conversion Ratio} \times S$.
   - $\text{Conversion Premium} = \frac{P_{cb} + AI - \text{Parity}}{\text{Parity}}$.
3. **Delta Hedge Calculation**:
   - Estimate equity delta $\Delta$ (via Black-Scholes or binomial tree model for convertible debt).
   - $\text{Short Shares} = \text{CB Quantity} \times \text{Conversion Ratio} \times \Delta$.
4. **Net Carry Yield Audit**:
   - $\text{Net Carry} = \text{Coupon Rate} - \text{Financing Cost} - r_{borrow}$.
5. **Trade Decision**:
   - If Implied Volatility $< \text{Historical Volatility}$ AND $\text{Net Carry} > 0 \implies$ Generate Buy CB + Short Stock Signal.