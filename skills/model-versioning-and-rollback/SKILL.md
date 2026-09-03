---
name: model-versioning-and-rollback
description: >-
  Use when a model or execution algorithm is registered, promoted or rolled back in
  production; an append-only SHA-256 registry where one semantic version permanently
  identifies one artifact, plus a guarded rollback path.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: deployment-ops, model-registry, model-versioning, rollback, semantic-versioning, sha256, circuit-breaker
  brokers_frameworks: "MLflow Model Registry (conceptual reference only); Python standard library (hashlib, hmac, threading)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when an ML alpha model or execution algorithm is **registered, promoted, or taken out of service** in a live trading environment, and the deployment must survive an audit and an incident:

- **Registration.** A version string must permanently identify exactly one artifact. Semantic Versioning 2.0.0 rule 3 is the governing convention — "Once a versioned package has been released, the contents of that version MUST NOT be modified." A registry that lets `v1.1.0` be re-registered with a different artifact cannot reproduce any past prediction, and no amount of downstream lineage tooling recovers that.
- **Promotion.** Exactly one version holds the serving pointer at a time, and the swap is recorded with who approved it. For EU investment firms this is not optional bookkeeping: ESMA's supervisory briefing on algorithmic trading states that "investment firms are required to timestamp, approve, and record all material changes" (ESMA74-1505669079-10311, 26 February 2026, ¶31), and lists changing risk-control thresholds among the change types.
- **Rollback.** When a *confirmed* degradation trigger fires — a drawdown or inference-error breach — the active pointer moves to the last known healthy production version, atomically, with the failing version quarantined so it cannot be silently re-promoted.

## When NOT to Use

- **As the trigger layer.** This engine acts on a **single** telemetry sample with no confirmation streak, cooldown, or per-deployment rollback cap. Wiring raw poll output straight into `audit_telemetry_and_rollback` will flap on one transient spike. Debounce first — see `automated-rollback-triggers-on-anomaly-detection`.
- **As a traffic router.** The engine moves a pointer in a registry. It does not drain in-flight inference requests, reload a serving process, reconcile positions taken by the failing model, or cancel its resting orders. Rolling the model back does not flatten what it already did — see `strategy-decommissioning-and-position-unwind-procedure` and `kill-switch-and-drawdown-circuit-breakers`.
- **As proof of artifact authenticity.** A SHA-256 digest detects corruption and accidental substitution. It establishes authenticity only if the registry holding the digest is itself protected: an attacker who can rewrite the artifact in object storage can rewrite an unsigned hash sitting next to it. Persist the registry to append-only or signed storage.
- **As a distributed source of truth.** The reference engine is a single-process, in-memory registry with a re-entrant lock. It is safe across threads in one process; it is not a consensus store. Two serving hosts running their own copies will disagree after a rollback.
- **When the anomaly is not deployment-correlated.** A venue outage or a market-wide dislocation spikes drawdown across *every* version. Rolling back cannot fix a market event and may revert to a version that handles the current regime worse.

## Prerequisites

- Model version metadata: `model_id`, `version` (semantic, e.g. `v1.1.0`), `sha256_hash`, `training_dataset_id`, `sharpe_ratio`, `max_drawdown_pct` (validated pre-deployment figures, positive magnitude), `status` (`PRODUCTION` / `STAGING` / `ARCHIVED`), and `approved_by`.
- Rollback thresholds agreed **before** deployment: `max_allowed_drawdown_pct`, `max_allowed_error_rate_pct`. These are your firm's risk numbers — no regulation supplies them, and the defaults here (15.0 / 5.0) are illustrative placeholders, not standards.
- Live performance telemetry as **positive-magnitude percentages**: `live_drawdown_pct=18.5` means 18.5%, not `0.185` and not `-18.5`.
- At least one retained, previously-served `PRODUCTION` version to roll back to. Without one the default policy halts serving.

## Workflow

1. **Register the artifact against its digest.**
   - `compute_sha256(artifact_bytes)` → register `ModelVersion`. The engine validates the semantic version (rejecting `latest_model.pkl`, `v1.0`, `v01.0.0` and `+build` metadata), validates the digest is 64 hexadecimal characters, and stores a defensive copy.
   - **Decision point — a re-registration is either identical or an error.** Byte-identical metadata is an idempotent no-op, so a crash-looping deployer replaying registrations is safe. Anything else raises. Do not "fix" a bad artifact by re-registering the same version; publish a new one.
   - A replayed registration that asks for the pointer (`is_active=True`) still gets it: the identity is unchanged, but the *intent to deploy* must not be dropped. It is routed through the same promotion path, so a quarantined version is refused rather than quietly resurrected.

2. **Verify before you serve.** Call `verify_artifact(model_id, version, artifact_bytes)` on every load from disk or object storage, and refuse to serve on `False`. Registering a hash and never checking it against the loaded bytes buys nothing.

3. **Promote deliberately, and separately from registration.**
   - **Decision point — registering is not deploying.** A `PRODUCTION`-status artifact registered with `is_active=False` is *staged*; the incumbent keeps serving. Only `promote_version` (or an explicit `is_active=True` registration) moves the pointer. Conflating the two is how staging the next release silently leaves a model with no active version at all.
   - Pass `approved_by`. A deployment record that cannot say who approved the change is not an audit trail.

4. **Feed the breaker confirmed telemetry.** Compare live drawdown and error rate against the limits. The breach test is strict (`live > limit`), so a reading exactly at the limit is not a breach — set the limit to the last value you are willing to tolerate.
   - **Decision point — an unevaluable sample is a failed check, never a healthy one.** `NaN > 15.0` is `False` under IEEE 754, so a missing-data NaN passed to a naive comparison reports the model healthy and silently disables the breaker. The engine raises `ModelRegistryError` instead. A monitoring loop that catches it and `continue`s has re-created the bug.

5. **Execute the rollback — target chosen before anything mutates.** The engine selects the fallback first, then deactivates the failing version and quarantines it as `DEACTIVATED_ROLLBACK`. Eligible targets exclude:
   - `ARCHIVED` versions (archival is a deliberate retirement decision) and, by default, `STAGING` versions — promoting an unvalidated candidate mid-incident swaps a known-bad model for an unknown one. Opt in with `allow_staging_fallback=True` if that trade is the one you want.
   - Any version whose *validated* `max_drawdown_pct` already exceeds the live limit; rolling onto it only re-trips the breaker.
   - Any never-served version ranking **above** the failing one — that is a roll-forward onto an unproven artifact, not a rollback.
   - **Decision point — ranking is by activation history, then semver precedence, then registration epoch.** A version that has actually served outranks one that was only ever registered. Precedence is computed numerically per semver rule 11; sorting the version *strings* places `v1.10.0` below `v1.9.0`.

6. **Handle "no healthy fallback" as a halt, not a shrug.** The default `halt_on_missing_rollback_target=True` quarantines the breaching version and leaves **no active version**: `active_version` is `None` and `is_serving_halted` is `True`. That is the fail-safe answer — capital protection over continuity — and it must be wired to the trading kill switch and an on-call page. Setting it to `False` keeps a breaching model serving and is a decision to record, not a default to inherit.

7. **Read the audit log.** `engine.audit_log` returns an ordered, immutable tuple of `REGISTER` / `PROMOTE` / `ROLLBACK` / `ROLLBACK_FAILED` / `HALT` events with the approver and the caller-supplied epoch. A failed rollback is never recorded as a `ROLLBACK`. This is the artefact a change-control review reads; the returned report is a single call's outcome.

> Full procedure: see `references/workflows.md`.
> Standards, citations, and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mutable version names.** Deploying under `latest_model.pkl` or re-writing `v1.1.0` in place. Once a version string can mean two artifacts, no past prediction is reproducible and the digest column becomes decoration. The engine rejects both.
- **Registering a `PRODUCTION` artifact and expecting the incumbent to keep serving.** In the pre-2.0 implementation this cleared the active pointer entirely: staging the next release left the model with **zero** active versions, and nothing in the report said so.
- **Treating a 64-character string as a digest.** `"z" * 64` is not a SHA-256 hash. Validate the character class, not just the length, and normalise case before comparing — an uppercase digest and its lowercase twin are not equal under `==`.
- **Storing a hash and never checking it.** The pitfall the previous version of this skill warned about while providing no verification function at all.
- **NaN telemetry reading as healthy.** Every comparison against NaN is `False`, so a gap in the metrics pipeline looks exactly like a well-behaved model. A negative drawdown under a signed convention does the same thing.
- **Deactivating the failing version before finding a target.** The failed path then leaves nothing serving while the report names the failing version as active — the registry and the report disagree about what is live, during an incident.
- **Rolling back onto whatever sorts first.** Ranking on unset registration timestamps falls through to dict insertion order, and ranking on version strings picks `v1.9.0` over `v1.10.0`.
- **Re-promoting the version that just breached.** A rollback followed by a re-promotion is a rollback loop. The engine refuses to re-promote a quarantined version; register a fixed one.
- **Acting on stale telemetry.** A poll still naming the version that was just rolled back must be discarded. Otherwise every subsequent sample re-reports a successful rollback and re-fires whatever the caller attaches to one.
- **Rolling back the pointer and calling the incident closed.** Positions the failing model opened, and its resting orders, are untouched by a registry write.

## Verification

- **The documented scenario.** Register `v1.0.0` (Sharpe 2.1, validated max drawdown 10.0%) and `v1.1.0` (Sharpe 2.4, active). Telemetry reports `v1.1.0` at 18.5% drawdown against a 15.0% limit ⇒ the report is `ROLLBACK_SUCCESSFUL`, `active_version == "v1.0.0"`, `previous_version == "v1.1.0"`, `sha256_hash` is v1.0.0's digest, and `v1.1.0` is left `DEACTIVATED_ROLLBACK`.
- **Threshold edge.** A reading exactly at the limit is `MODEL_VERSION_HEALTHY`; `15.000001` against a 15.0 limit rolls back.
- **Unevaluable telemetry (regression).** `NaN`, `Inf` and `-18.5` drawdowns each raise `ModelRegistryError` and leave the pointer untouched. Against the pre-2.0 implementation, `NaN` returned `MODEL_VERSION_HEALTHY`.
- **Immutability (regression).** Re-registering `v1.0.0` with a different digest raises and the stored digest is unchanged; re-registering identical metadata is a no-op that adds no second audit event.
- **Input validation (regression).** `"z" * 64`, a 63-character digest, `latest_model.pkl`, `v1.0`, `v01.0.0` and `1.2.3+build.5` are all rejected, as are a version with leading or trailing whitespace and a non-finite `registered_at_epoch`. An uppercase digest is normalised and still verifies against the artifact bytes.
- **Pointer semantics (regression).** Registering a second `PRODUCTION` version with `is_active=False` leaves the incumbent active; registering with `is_active=True` leaves exactly one active version. A replayed registration with `is_active=True` promotes, and the same replay against a quarantined version raises.
- **No-fallback halt (regression).** With no eligible target, `active_version` is `None`, `is_serving_halted` is `True`, the breaching version is quarantined and a `HALT` event is recorded. With `halt_on_missing_rollback_target=False`, it keeps serving and logs `CRITICAL`.
- **Target selection.** `STAGING` is ineligible by default and eligible under `allow_staging_fallback=True`; `ARCHIVED` is never eligible; a candidate with a validated 22% max drawdown against a 15% limit is skipped with a warning; a never-served higher version is not selected; a version that has served outranks one that has not; ties resolve to `v1.10.0` over `v1.9.0`.
- **Stale telemetry (regression).** Replaying the same breaching sample returns `TELEMETRY_STALE_NO_ACTION` with exactly one `ROLLBACK` event in the audit log.
- **Digest.** `compute_sha256(b"")` equals the published `e3b0c442...7852b855` test vector.
- **Determinism and concurrency.** Two identical call sequences produce equal reports; 24 threads registering concurrently leave 24 versions, exactly one active, and unique audit sequence numbers.
- Run `python -m unittest discover -s skills/model-versioning-and-rollback/scripts` and confirm a 100% pass rate.

## Related Skills

- `automated-rollback-triggers-on-anomaly-detection`
- `model-card-documentation-for-trading-models`
- `model-serving-infrastructure-ab-testing`
- `model-staleness-detection`
- `blue-green-deployment-for-live-strategy-updates`
- `canary-releases-for-strategy-code-changes`
- `kill-switch-and-drawdown-circuit-breakers`
- `reproducible-ml-training-pipelines`
- `audit-logging-for-configuration-changes`
