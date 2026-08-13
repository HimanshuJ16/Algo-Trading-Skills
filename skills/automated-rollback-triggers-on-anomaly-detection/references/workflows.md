# Workflows for Automated Rollback on Anomaly Detection

## End-to-End Procedure

1. **Pre-Deploy Configuration**
   - Agree objective rollback thresholds *before* the deploy (see
     `references/standards.md`).
   - Construct a `RollbackThresholdConfig` with the metric thresholds and the
     safety policy (`consecutive_failures_required`, `rollback_cooldown_seconds`,
     `max_rollbacks_per_deployment`).
   - Ensure the previous known-good version is retained in a warm standby
     (Blue/Green) so rollback is a traffic reroute, not a re-provision.
   - Verify rollback safety: message/cache formats and schema migrations must be
     forward/backward compatible across adjacent versions.

2. **Deployment Phase**
   - The new algorithmic trading container is spun up in production (e.g. via
     Kubernetes / ArgoCD).
   - Call `engine.reset()` to clear any state from a prior deployment.

3. **Telemetry Ingestion (Burn-in Window)**
   - For the configured burn-in period (5–15 min for low-latency trading), a
     poller or cronjob pulls metrics from the aggregator (Prometheus, Datadog)
     at a fixed interval (5–10s) and constructs a `DeploymentHealthMetrics`
     snapshot per poll.
   - Set `market_open_volatility = True` on snapshots that overlap a known
     market-open / fast-market window.

4. **Anomaly Evaluation**
   - Feed each snapshot into `AutomatedRollbackEngine.evaluate_metrics()`.
   - The engine detects threshold breaches, classifies severity, and updates the
     consecutive-failure streak. Market-open-volatile samples are detected and
     logged but do **not** advance the streak.

5. **Confirmation Gate (Flapping Prevention)**
   - A rollback is recommended only after `consecutive_failures_required`
     consecutive breaching snapshots. A single transient spike returns
     `CONFIRMING`, not `ROLLBACK`.
   - A healthy sample resets the streak.

6. **Trigger Hook**
   - When `result.decision == ROLLBACK` (`result.should_rollback == True`), the
     script triggers a webhook to the CI/CD controller (e.g. ArgoCD) to reroute
     traffic back to the previous version.
   - The engine records the rollback, starts the cooldown, and resets the
     confirmation streak.

7. **Rollback-Loop Guard**
   - While the cooldown is active, further `ROLLBACK` decisions are suppressed
     (`SUPPRESSED`, `remaining_cooldown_s > 0`).
   - Once `max_rollbacks_per_deployment` is reached, all further automatic
     rollbacks are suppressed and the engine escalates to a human on-call.

8. **Reversion**
   - The CI/CD controller scales down / drains the new container and scales up /
   reroutes to the previously successful container, restoring the "last known
   good" state.
   - Verify the rollback actually fixed the issue: sample the same metrics for a
   short verify window (4–5 min). If still failing, escalate — do not roll back
   again automatically.

9. **Post-Rollback**
   - Only after the burn-in window passes with all metrics healthy, decommission
   the standby version.
   - File a post-mortem; the rollback is a signal, not a resolution.

## Decision Flow

```
snapshot -> detect anomalies?
  no  -> HEALTHY (reset streak)
  yes -> market_open_volatility?
         yes -> SUPPRESSED (do not advance streak)
         no  -> advance streak
                -> rollback cap reached?  -> SUPPRESSED (escalate)
                -> cooldown active?       -> SUPPRESSED
                -> streak >= required?    -> ROLLBACK (record, cooldown, reset streak)
                -> else                   -> CONFIRMING
```

## Failure Modes and Recovery

| Failure mode | Detection | Recovery |
|---|---|---|
| Flapping (transient spike) | Single breach, no confirmation | Require `consecutive_failures_required`; streak resets on healthy sample. |
| Rollback loop | Breaches persist after rollback | Cooldown + `max_rollbacks_per_deployment`; escalate to human. |
| Real market event misread as defect | Rejects/latency spike across both versions | `market_open_volatility` guard; confirm deployment correlation. |
| Rollback causes harm (in-flight state) | Post-rollback verify window still failing | Do not re-roll back automatically; escalate; ensure rollback-safe schemas. |
| Stale engine state from prior deploy | State carried across deployments | Call `engine.reset()` at the start of each new deployment. |
