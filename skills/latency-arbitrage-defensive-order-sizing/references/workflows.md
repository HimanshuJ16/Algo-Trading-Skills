# Workflows for Latency Arbitrage Defense

1. **Latency Gap & Volatility Measurement**:
   - Measure cross-venue cancellation latency gap $\Delta \tau$ and annualized volatility $\sigma$.
2. **Sniping Probability Modeling**:
   - Compute adverse selection sniping probability $P_{\text{snipe}}$.
3. **Defensive Quote Sizing & Cancellation**:
   - Scale quote size $Q_{\text{defensive}} = Q_0 \times (1 - P_{\text{snipe}})$, cancel quote if below minimum lot size, and widen bid-ask spread.
4. **Audit Report Generation**:
   - Output structured defensive sizing report.
