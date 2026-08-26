# Pre-Flight Checklist — Model Versioning & Rollback

## Registration

- [ ] Is the version a real semantic version (`vX.Y.Z`, no leading zeroes, no `+build`) rather than `latest_model.pkl` or `v1.0`?
- [ ] Is the SHA-256 digest validated by **character class**, not just length? (`"z" * 64` is not a digest.)
- [ ] Is the digest normalised to lowercase before it is stored or compared?
- [ ] Is `training_dataset_id` populated, so the version can actually be reproduced?
- [ ] Is `approved_by` recorded? (A deployment record that cannot say who approved the change is not an audit trail.)
- [ ] Does re-registering the same version with a different artifact **raise**, and re-registering identical metadata no-op?

## Deployment

- [ ] Is the digest verified against the loaded bytes on **every** load, and does a mismatch refuse to serve?
- [ ] Is registration separated from promotion — does staging the next `PRODUCTION` release leave the incumbent serving?
- [ ] Does exactly one version hold the active pointer after promotion?
- [ ] Is the registry persisted to append-only or signed storage? (An unsigned hash beside a rewritable artifact proves nothing about authenticity.)

## Circuit breaker

- [ ] Are `max_allowed_drawdown_pct` and `max_allowed_error_rate_pct` your firm's numbers, agreed before deployment? (The 15.0 / 5.0 defaults are placeholders, not standards.)
- [ ] Is telemetry supplied as **positive-magnitude percentages** (`18.5`, not `0.185` and not `-18.5`)?
- [ ] Does the trigger reaching this engine come from a **confirmed** detection layer with a confirmation streak, cooldown and rollback cap — not a raw poll?
- [ ] Is `ModelRegistryError` wired as a *failed check* that halts and pages, never caught-and-continued? (`NaN > limit` is `False`; a swallowed exception silently disables the breaker.)
- [ ] Is a reading exactly at the limit understood to be healthy? (The test is strict `>`.)

## Rollback

- [ ] Is at least one previously served, non-archived `PRODUCTION` version retained as a fallback?
- [ ] Does the fallback's **validated** `max_drawdown_pct` sit inside the live limit, so the rollback will not immediately re-trip?
- [ ] Is `allow_staging_fallback` left off unless promoting an unvalidated candidate mid-incident is the trade you intend?
- [ ] Is the failing version selected-then-deactivated — never deactivated before a target exists?
- [ ] Is the quarantined version blocked from re-promotion? (Rollback → re-promote is a loop.)
- [ ] Is stale telemetry naming the rolled-back version discarded rather than re-triggering?

## Halt path

- [ ] Has someone decided, in advance, whether "no healthy fallback" halts serving (default) or keeps a breaching model live?
- [ ] Is `report.is_serving_halted` / `active_version is None` wired to the trading kill switch and an on-call page?
- [ ] Do downstream consumers handle `active_version is None` rather than assuming a string?

## Audit

- [ ] Are `REGISTER` / `PROMOTE` / `ROLLBACK` / `ROLLBACK_FAILED` / `HALT` events retained beyond the process lifetime?
- [ ] Does a breach that found no target appear as `ROLLBACK_FAILED` / `HALT` rather than as a successful `ROLLBACK`?
- [ ] Can the log answer "which artifact was live at time T, who approved it, and why did it change?"
- [ ] For EU/EEA investment firms: is the record sufficient to timestamp, approve and evidence a material change, including changes to the **thresholds themselves**?
