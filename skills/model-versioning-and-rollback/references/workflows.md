# Workflows for Model Versioning & Rollback

## 1. Fingerprint and register the artifact

```python
digest = ModelVersionManagerEngine.compute_sha256(artifact_bytes)
engine.register_version(ModelVersion(
    model_id="ML_ALPHA_101",
    version="v1.1.0",                 # semantic; 'latest_model.pkl' is rejected
    sha256_hash=digest,
    training_dataset_id="DS_2026_Q1",
    sharpe_ratio=2.4,                 # validated, pre-deployment
    max_drawdown_pct=11.0,            # validated, positive magnitude
    status="PRODUCTION",
    is_active=False,                  # staged, NOT deployed — see step 3
    registered_at_epoch=1750000000.0,
    approved_by="risk.committee",
))
```

Registration validates the version against Semantic Versioning 2.0.0, validates
the digest as 64 hexadecimal characters (normalising case), requires a
`training_dataset_id`, requires finite metrics with a non-negative drawdown, and
stores a **defensive copy** so later mutation of the caller's object cannot
rewrite registry history.

**Re-registration is either identical or an error.** Byte-identical identity
fields — digest, dataset, Sharpe, drawdown — make the call an idempotent no-op,
so a deployer that crash-loops and replays its registrations is safe. Anything
else raises `ModelRegistryError`, because a version string that can mean two
artifacts makes every past prediction irreproducible (semver rule 3).

A replay carrying `is_active=True` is not swallowed by the no-op: it is routed
through `promote_version`, so the pointer moves, the promotion is recorded, and
a version quarantined by an earlier rollback is refused instead of resurrected.

## 2. Verify on every load, not just at registration

```python
if not engine.verify_artifact(model_id, version, loaded_bytes):
    raise SystemExit("artifact/digest mismatch — refusing to serve")
```

Comparison is constant-time. The guarantee stops at integrity: it detects a
corrupted or substituted file, and it detects tampering only if the registry
record holding the digest is itself on append-only or signed storage. See
`references/standards.md`.

## 3. Promote deliberately

```python
engine.promote_version("ML_ALPHA_101", "v1.1.0", approved_by="head.of.quant")
```

Registering with `is_active=False` **stages**; the incumbent keeps serving.
Only `promote_version` — or an explicit `is_active=True` registration, which
requires `status="PRODUCTION"` — moves the pointer, and exactly one version
holds it at a time.

`promote_version` refuses a version the engine has quarantined by rollback.
Re-promoting the artifact that just breached its limits is a rollback loop;
register a fixed version instead.

## 4. Audit confirmed telemetry

```python
report = engine.audit_telemetry_and_rollback(
    RollbackTriggerConfig(max_allowed_drawdown_pct=15.0,
                          max_allowed_error_rate_pct=5.0),
    LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0",
                             live_drawdown_pct=18.5,
                             live_error_rate_pct=1.0,
                             recent_sharpe=0.4),
)
```

The breach test is strict — `live > limit` — so a reading exactly at the limit
is healthy. Set the limit to the last value you are willing to tolerate.

**Wire the exception as a failed check.** `ModelRegistryError` is raised for
non-finite or negative telemetry, non-finite limits, and unknown identifiers.
It is never a healthy sample:

```python
try:
    report = engine.audit_telemetry_and_rollback(cfg, telem)
except ModelRegistryError:
    halt_trading_and_page_on_call()      # never `continue`
```

`NaN > 15.0` is `False` under IEEE 754. A monitoring loop that swallows the
exception, or a naive comparison that never raises, reports a healthy model
while the metrics pipeline is broken.

**Feed it a confirmed trigger.** One sample acts. There is no confirmation
streak, cooldown or per-deployment cap here — see
`automated-rollback-triggers-on-anomaly-detection` for that layer.

## 5. Rollback target selection

Plan-then-mutate: the fallback is chosen *before* the failing version is
touched, so a failed search cannot leave the registry with nothing serving by
accident.

| Filter | Reason |
|---|---|
| Not the failing version | It is the thing being rolled back. |
| `status == "PRODUCTION"` | `ARCHIVED` is a deliberate retirement; `STAGING` is unvalidated. |
| `STAGING` only with `allow_staging_fallback=True` | Promoting an unvalidated candidate mid-incident swaps a known-bad model for an unknown one. Opt in explicitly. |
| Not `DEACTIVATED_ROLLBACK` | A version quarantined by an earlier rollback is not a healthy target. |
| Validated `max_drawdown_pct <= live limit` | Rolling onto a model already known to breach the limit only re-trips the breaker. |
| Never-served versions ranking **above** the failing one are excluded | That is a roll-forward onto an unproven artifact, not a rollback. |

Ranking, all descending:

```
(last_activated_seq, semver_precedence_key(version), registered_at_epoch)
```

`last_activated_seq` is a monotonic counter the engine increments on every
activation — an ordering token, not a wall clock, so the engine has no hidden
time dependency and identical call sequences produce identical results. A
version that has actually served outranks one that was only registered.
Semver precedence is numeric per rule 11: ranking the version *strings* would
place `v1.10.0` below `v1.9.0`.

## 6. No healthy fallback — halt by default

`halt_on_missing_rollback_target=True` (default):

- the breaching version is quarantined as `DEACTIVATED_ROLLBACK`,
- `report.active_version` is `None` and `report.is_serving_halted` is `True`,
- a `HALT` event is recorded and a `CRITICAL` log line is emitted.

**Nothing is serving.** Wire this to the trading kill switch and an on-call
page — `kill-switch-and-drawdown-circuit-breakers`. Capital protection beats
continuity when the only alternative is a model known to be breaching its
limits.

Setting the flag to `False` keeps the breaching version active, logs `CRITICAL`,
and returns `is_serving_halted=False`. It is a recorded decision, not a default
to inherit.

## 7. Read the audit log at change-control review

```python
for event in engine.audit_log:            # immutable, ordered tuple
    print(event.sequence, event.event, event.version, event.approved_by, event.detail)
```

`REGISTER` / `PROMOTE` / `ROLLBACK` / `ROLLBACK_FAILED` / `HALT`, each with the
version, the approver where one was supplied, the caller's epoch, and a
monotonic sequence number. A breach that found no target is recorded as
`ROLLBACK_FAILED` or `HALT` — never as a `ROLLBACK`. This is what reconstructs
an incident after the fact; the report returned by a single call is not a
history.

For EU/EEA investment firms this maps onto ESMA's statement that firms "are
required to timestamp, approve, and record all material changes"
(ESMA74-1505669079-10311, ¶31) — with the caveats on status, applicability and
sourcing in `references/standards.md`. The engine records the approver; it does
not authenticate them.

## 8. What this workflow does not do

Moving the pointer is not the whole rollback. Still required, elsewhere:

- draining in-flight inference and reloading the serving process
  (`model-serving-infrastructure-ab-testing`, `blue-green-deployment-for-live-strategy-updates`),
- cancelling resting orders and unwinding positions the failing model opened
  (`strategy-decommissioning-and-position-unwind-procedure`),
- deciding whether the anomaly was deployment-correlated at all — a venue
  outage spikes drawdown on every version, and rollback cannot fix a market
  event.
