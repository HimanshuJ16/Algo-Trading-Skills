# Pre-Flight Checklist

- [ ] Are schema contracts defined with required fields, expected types, and inclusive value bounds?
- [ ] Does the contract itself validate at construction (no duplicate fields, no inverted bounds, null ceiling within 0-100)?
- [ ] Are missing fields and type mutations intercepted at the ingestion edge?
- [ ] Are `NaN` and `±Inf` rejected for numeric fields before bounds are evaluated?
- [ ] Are boolean values blocked from satisfying `int`/`float` field contracts?
- [ ] Does a single malformed (non-mapping) payload get quarantined without aborting the batch?
- [ ] Are corrupt payloads routed to a Dead Letter Queue (DLQ) for alerting, with the payload snapshotted rather than aliased?
- [ ] Is batch nullability monitored per field against `max_allowed_null_pct`, and does a breach invalidate the batch?
- [ ] Are undeclared fields surfaced as schema drift signals, and is `forbid_unknown_fields` set deliberately for this feed?
