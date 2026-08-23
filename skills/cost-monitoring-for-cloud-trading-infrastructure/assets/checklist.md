# Pre-Flight Checklist

- [ ] Are cloud cost metrics ingested by service category (`Compute`, `NetworkEgress`, `Storage`)?
- [ ] Is every baseline scoped to a single (service, environment) pair — no PROD/DEV mixing?
- [ ] Is rolling 14-day baseline ($Z$-score) configured for cost spike detection?
- [ ] Are non-finite telemetry values rejected (loud failure) rather than silently classified NORMAL?
- [ ] Does CRITICAL retain the dual gate ($Z \ge 3.0$ AND > 30% mean increase) so flat baselines don't page on small absolute deviations?
- [ ] Are cost allocation tags enforced across all cloud infrastructure assets?
- [ ] Is unit cost per trade calculated with real trade volume to prevent false alarms during high-volume trading days?
- [ ] Do services without baseline history surface as baseline-UNKNOWN rather than healthy?
