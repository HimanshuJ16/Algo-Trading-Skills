# Pre-Flight Checklist

- [ ] Are standalone initial margin requirements ($M_i$) ingested for all portfolio asset classes, one aggregated figure per asset class (no duplicate identifiers)?
- [ ] Is every offset ($\rho_{i,j}$) traceable to an active cross-margin arrangement and tagged with its program (`CME-OCC`, `CME-FICC/GSD`) rather than estimated from returns?
- [ ] Is the account structure actually eligible — same dually-registered FCM/BD at both clearing houses, signed cross-margin participant agreement?
- [ ] Is `default_correlation` left at 1.0 (fail closed) so unregistered pairs earn no offset, and is `unregistered_pairs` reviewed on every run?
- [ ] Is the model-risk floor (`minimum_floor_pct`, default $20\%$) deliberately calibrated, and documented internally as a model parameter rather than a clearing house rule?
- [ ] Does the run complete without `InconsistentCorrelationError` — i.e. are the pairwise offsets jointly consistent?
- [ ] Has the estimate been reconciled against the CCP / clearing-broker requirement before any collateral is released?
- [ ] Are capital efficiency savings, applied offsets, and freed collateral logged for treasury management and audit?
- [ ] Has offset behaviour been stress-reviewed for correlation convergence, so freed collateral is not committed to positions that fail when the offset evaporates?
