# Pre-Flight Checklist

- [ ] Are single-name NAV concentration limits (e.g. 5% NAV) configured and tested?
- [ ] Is the NAV cap applied to **absolute** exposure, so that a short is capped symmetrically with a long? (Verify a `SELL` from a flat position is downsized, not waved through.)
- [ ] Are limits expressed as fractions (0.05), not whole percents (5), and does the constructor reject the wrong unit?
- [ ] Do pre-trade limit calculations include orders already sent to a venue but not yet filled or cancelled?
- [ ] Are de-risking trades on an already-over-limit position allowed through rather than blocked?
- [ ] Does an unrecognised order side raise, rather than falling through to a branch with no NAV limit?
- [ ] Are Average Daily Volume (ADV) liquidity limits (e.g. 10% ADV) integrated into pre-trade order sizing, on both buys and sells?
- [ ] Does a missing, zero, or negative ADV reject the order rather than being read as "no liquidity constraint"?
- [ ] Does the ADV lookback window match the one the % ADV threshold was calibrated against?
- [ ] Does the limiter automatically downsize oversized orders to the maximum compliant share count, flooring rather than rounding up?
- [ ] Is lot-size / minimum-fill rounding applied downstream of the downsized quantity before routing?
- [ ] Are single-stock-future and option delta notionals folded into the position value when they share the single-name limit?
- [ ] Is Herfindahl-Hirschman Index (HHI) and Effective Assets ($N_{eff}$) computed for portfolio risk reporting, with the empty-portfolio NaN case handled by downstream alerting?
- [ ] Have the configured thresholds been checked against the fund's actual mandate rather than assumed from `references/standards.md` defaults?
