# Pre-Flight Checklist

- [ ] Are all strategy data dependencies mapped with assigned criticality tiers and SLAs?
- [ ] Is data freshness audited against SLA cutoffs ($\le 60\text{s}$ for critical feeds)?
- [ ] Are secondary vendor fallbacks configured for all critical orderbook/price feeds?
- [ ] Is strategy execution hard-blocked if any critical dependency fails both primary and secondary vendors?