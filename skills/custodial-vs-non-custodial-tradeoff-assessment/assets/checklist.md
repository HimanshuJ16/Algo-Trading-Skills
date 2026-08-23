# Pre-Flight Checklist

- [ ] Are the strategy's latency budget, monthly volume, gas sensitivity, and counterparty tolerance documented and validated?
- [ ] Is `max_counterparty_risk_pct` understood as a tolerance BUDGET, so that a lower value excludes more architectures rather than scoring exposed ones better?
- [ ] Are key sovereignty, counterparty tolerance, latency floor and qualified-custodian requirements applied as pass/fail disqualifications, not as score penalties?
- [ ] Do on-chain architectures carry a latency floor equal to block inclusion time for the chain actually used, rather than network round-trip?
- [ ] Have the illustrative default residual-exposure and gas-burden figures been replaced with firm-specific due diligence, and the flag cleared?
- [ ] Is off-exchange settlement (Fireblocks Off-Exchange / Copper ClearLoop) evaluated once monthly volume passes the firm's review threshold?
- [ ] Is the residual exposure of off-exchange settlement — unsettled P&L on open positions, plus substituted custodian and trust-structure risk — quantified rather than assumed to be zero?
- [ ] Is the legal structure making collateral bankruptcy-remote confirmed in writing, including who holds it?
- [ ] Are the composite weights declared, validated to sum to 1.0, and recorded with the decision?
- [ ] Does the process fail closed when no architecture satisfies the mandate, rather than falling back to the least-bad option?
- [ ] Is the applicable jurisdiction's custody position confirmed (e.g. MiCA Art 75 for EU CASPs, Advisers Act custody obligations in the US) before the architecture is committed?
- [ ] For non-custodial paths: are MPC or multi-signature signing, tested key recovery, private-mempool routing, and contract/bridge audits all in place?
- [ ] Is it understood that private-mempool routing mitigates rather than eliminates MEV, and that inclusion is not guaranteed?
