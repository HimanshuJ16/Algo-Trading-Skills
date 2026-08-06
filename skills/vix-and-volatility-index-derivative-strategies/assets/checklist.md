# Institutional VIX Strategy Operations Checklist

## Market Data & Term Structure Monitoring
- [ ] **Real-Time Feed Calibration**: Verify continuous ingestion of Spot VIX ($S_{\text{VIX}}$) and front-two futures ($F_1, F_2$).
- [ ] **Term Structure Slope Calculation**: Execute `analyze_term_structure()` calculating slope % $\frac{F_2 - F_1}{F_1} \times 100$.
- [ ] **Contango / Backwardation Thresholds**: Confirm state thresholds ($\ge +2\%$ Contango, $\le -2\%$ Backwardation).

## Position Sizing & Risk Controls
- [ ] **Contract Multiplier Check**: Apply the mandatory **$1,000 contract multiplier** per index point for all futures and option position sizing.
- [ ] **Roll Yield Harvest Caps**: Limit short VIX futures allocations to $\le 5\%$ of total portfolio equity.
- [ ] **Catastrophic Volatility Stop-Loss**: Enforce automatic buy-stop orders on short VIX futures if spot VIX spikes $+30\%$ in a single session.

## Options Pricing & Expiry Roll Management
- [ ] **Forward Futures Pricing Verification**: Verify VIX option strikes are priced off $F_1$ forward futures, NOT spot VIX.
- [ ] **Monthly Expiry Calendar Roll**: Initiate futures roll protocol when $D_{\text{expiry}} \le 3\ \text{days}$ to avoid settlement auction gaps.