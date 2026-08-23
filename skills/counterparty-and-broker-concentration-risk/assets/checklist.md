# Pre-Flight Checklist

- [ ] Are per-broker NAV concentration limits (e.g. 35% NAV) configured for all active counterparties?
- [ ] Are Credit Default Swap (CDS) spread alerts and max thresholds (>250 bps) monitored — with missing/NaN quotes treated as blocking, not healthy?
- [ ] Does pre-trade order routing fail over to the compliant secondary broker with the lowest projected weight on limit breach?
- [ ] When ALL brokers are non-compliant, does the pipeline honor `blocked=True` (route nothing, escalate) instead of falling back to the primary?
- [ ] Is the fail-closed path active for unassessable NAV (≤ 0), with no substituted denominator?
- [ ] Are broker profiles re-registered with fresh balances before routing (no stale opening-of-day exposure)?
- [ ] Are unknown broker ids rejected loudly (no silent 0.0 exposure)?
- [ ] Are caps and HHI measured on exposure *magnitude*, so a net-debit or net-short broker cannot read as unused capacity and win the failover search?
- [ ] Does an undefined HHI (zero gross exposure) surface as a raised error rather than a 0.0 that reads as perfect diversification?
- [ ] Is broker HHI computed continuously to track counterparty diversification, with the >0.35 alert level calibrated to policy?
