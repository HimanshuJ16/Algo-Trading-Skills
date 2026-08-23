# Pre-Flight Checklist

## Data
- [ ] PnL return streams ingested for all active sub-strategy pods, on one shared timestamp index?
- [ ] Market beta / common factor exposure stripped before correlating pod PnL?
- [ ] Column order confirmed to match the `strategy_names` list?
- [ ] Non-finite values repaired or dropped upstream (never imputed)?
- [ ] Flat / stale / idle pods excluded explicitly rather than zero-filled?

## Configuration
- [ ] `lookback_window` set deliberately (e.g. 30 days) instead of defaulting to full history?
- [ ] `min_observations` calibrated to your noise tolerance, not left at the hard floor of 3?
- [ ] Correlation thresholds (0.70 / 0.85) and `min_diversification_ratio` (1.20) calibrated
      against your own pods, and documented as policy defaults rather than standards?
- [ ] Capital weights non-negative and matching the pod count?

## Monitoring
- [ ] Rolling PnL correlation matrix recomputed continuously?
- [ ] Correlation breaches flagged automatically, with persistence across consecutive
      windows required before capital is cut?
- [ ] Portfolio Diversification Ratio calculated and alerted independently of pair breaches?
- [ ] $DR = \infty$ / validation exceptions routed to an operator, not swallowed?
- [ ] Tail-dependence monitored separately (Pearson $DR$ will not see a crisis convergence)?
