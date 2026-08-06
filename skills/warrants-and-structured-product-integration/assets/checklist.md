# Institutional Warrants & Structured Product Operations Checklist

## Contract Master & Term Sheet Setup
- [ ] **Entitlement Ratio Verification**: Verify exact Entitlement Ratio ($R_{\text{ent}}$) from exchange term sheet (e.g. 0.1 for 10:1 ratio).
- [ ] **Knock-Out Barrier Monitoring**: Configure real-time tick alerts for Turbo Warrant / CBBC barrier levels ($B_{\text{knockout}}$).
- [ ] **Settlement Mechanism Audit**: Confirm cash settlement vs physical delivery rules for covered warrants.

## Pricing & Gearing Calibration
- [ ] **Entitlement Ratio Scaling**: Confirm Black-Scholes fair value and greeks ($\Delta, \Gamma, \Theta, \text{Vega}$) are multiplied by $R_{\text{ent}}$.
- [ ] **Effective Gearing Computation**: Calculate $\text{Effective Gearing} = \text{Simple Gearing} \times \Delta_{\text{raw}}$ to measure true leverage.
- [ ] **Time Decay Tracking**: Track daily Theta decay as warrants approach maturity.

## Delta Hedging & Knock-Out Risk Management
- [ ] **Real-Time Delta Rebalancing**: Execute `calculate_delta_hedge_signal()` to maintain delta-neutral underlying share positions.
- [ ] **MCE Knock-Out Liquidations**: Automatically dump underlying equity hedges when a Turbo Warrant / CBBC is knocked out.