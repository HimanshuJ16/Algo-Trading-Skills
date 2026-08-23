# Pre-Flight Checklist

## Archive health
- [ ] Is WAL streamed continuously to durable off-site storage?
- [ ] Does an alert fire on the **age of the newest archived segment**, not only on archive command errors?
- [ ] Is the archive's retention window at least as long as the oldest recovery point you claim to support?

## Drill execution
- [ ] Is PITR exercised against a specific target timestamp $T_{\text{target}}$, not just "latest"?
- [ ] Does the WAL sequence replay contiguously, with no missing LSN?
- [ ] Did recovery actually **reach** the target (`recovery_target_reached`), rather than stopping short?
- [ ] Is the measured RPO the shortfall $T_{\text{target}} - T_{\text{horizon}}$ — and is a reported 0.0s corroborated by WAL existing at or past the target?
- [ ] Is RTO taken from wall-clock restore duration rather than estimated?
- [ ] Was the full restore drill run on an **isolated** instance no live process can reach?

## Verification
- [ ] Are restored row counts and checksum compared against an expectation derived **independently** of the replayed WAL?
- [ ] Do restored trade ledgers reconcile against broker records and custodian positions?
- [ ] Is `integrity_verified = None` treated as unverified rather than as a pass?

## Governance
- [ ] Is each drill report archived so degradation is visible drill over drill?
- [ ] Are the RPO/RTO objectives your own, justified by business impact — not the module defaults copied unchanged?
- [ ] Does the drill cadence satisfy the review/testing obligations that actually apply to your entity (see `references/standards.md`)?
