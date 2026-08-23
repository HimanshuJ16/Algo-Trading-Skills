# Pre-Flight Checklist

- [ ] Are cloud cost metrics ingested by service category (`Compute`, `NetworkEgress`, `Storage`)?
- [ ] Is every baseline scoped to a single (service, environment) pair — no PROD/DEV mixing?
- [ ] Is rolling 14-day baseline ($Z$-score) configured for cost spike detection?
- [ ] Are non-finite telemetry values rejected (loud failure) rather than silently classified NORMAL?
- [ ] Does CRITICAL retain the dual gate ($Z \ge 3.0$ AND > 30% mean increase)?
- [ ] Does WARNING retain the `flat_baseline_min_pct_change` floor, so a \$3 blip on a \$100k/day flat baseline does not page on-call?
- [ ] Is the history you pass already truncated to the intended rolling window (the detector does not window, sort, or dedupe it)?
- [ ] Is an `inf` unit cost treated as an alert (spend with no trades), not as missing data?
- [ ] Are cost allocation tags enforced across all cloud infrastructure assets?
- [ ] Is unit cost per trade calculated with real trade volume to prevent false alarms during high-volume trading days?
- [ ] Do services without baseline history surface as baseline-UNKNOWN rather than healthy?
