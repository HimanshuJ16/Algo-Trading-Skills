---
name: automated-rollback-triggers-on-anomaly-detection
description: >-
  Use immediately after deploying a trading algorithm, to watch post-deployment health
  metrics and roll back automatically on a deployment defect, with flapping and
  rollback-loop protection. Not for strategy underperformance.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment, ci-cd, rollback, anomaly-detection, self-healing
  brokers_frameworks: generic
  version: "2.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill within your CI/CD pipeline immediately following a deployment of a trading algorithm (e.g., during a Canary Release or Blue/Green deployment). In algorithmic trading, milliseconds of malfunction can cause catastrophic financial damage.

This self-healing engine continuously monitors post-deployment telemetry (technical metrics like latency/errors and trading metrics like order reject rates) and, if a metric breaches its safety threshold for a confirmed number of consecutive samples, fires an automated rollback trigger that minimizes Mean Time to Recovery (MTTR).

## When NOT to Use

Do not wire automated rollback as the *primary* control loop for market-data alerts or strategy underperformance — those are human/postmortem signals, not deployment-defect signals. Automated rollback is appropriate only when a deployment temporally correlates with the anomaly onset (a deployment-correlated rollback gate). For non-deployment anomalies (e.g. a venue-wide outage), rollback cannot help and may itself cause harm by reverting to a version that lacks the current degradation handling.

## Prerequisites

- Python 3.10+
- A telemetry aggregator (e.g., Prometheus, Datadog) capable of supplying real-time `DeploymentHealthMetrics` per deployment version.
- Integration with a CI/CD controller (e.g., Kubernetes, ArgoCD, GitHub Actions, Jenkins) to execute the rollback script when `should_rollback == True`.
- A "last known good" version retained in a standby/draining state (Blue/Green) so rollback is a traffic reroute, not a re-provision.
- Pre-deployed, objective rollback thresholds agreed *before* the deployment begins — never improvised mid-deploy.
- A poller that fails **loudly**. A metric the backend did not return, a scrape that timed out, or a non-finite value must abort the health gate and escalate — never be substituted with the last good sample or a zero.

## Workflow

1. **Configure (pre-deploy)**: Define the `RollbackThresholdConfig` — per-metric thresholds plus the safety policy: `consecutive_failures_required` (default 2), `rollback_cooldown_seconds` (default 300), and `max_rollbacks_per_deployment` (default 1). Call `engine.reset()` at the start of each new deployment.
2. **Deploy**: The new algorithm version `v2.1` is deployed; the previous version `v2.0` is kept warm in standby.
3. **Monitor**: The `AutomatedRollbackEngine` receives a `DeploymentHealthMetrics` snapshot each poll interval (e.g. every 5–10s) during a defined burn-in window (typically the first 5–15 minutes post-deploy). Build every snapshot from a fresh, complete scrape: `DeploymentHealthMetrics` rejects `NaN`/`Inf` at construction, and a scrape that returns no series must be handled by the poller as an evaluation failure, not passed on as a healthy sample.
4. **Detect**: For each snapshot, the engine compares metrics against the thresholds and records any breach as a structured `Anomaly` with a severity (`order_reject_rate` and `http_5xx` are `CRITICAL`; `latency_ms` is `WARNING`).
5. **Confirm (flapping guard)**: A breach only advances a consecutive-failure streak. A rollback is recommended only once the streak reaches `consecutive_failures_required` — a single transient spike does NOT roll back a healthy deployment.
6. **Decide**: The engine returns a `RollbackDecision`:
   - `HEALTHY` — no breaches; streak resets.
   - `CONFIRMING` — breach detected but not yet confirmed.
   - `ROLLBACK` — confirmed; the CI/CD controller reverts to `v2.0`.
   - `SUPPRESSED` — breach detected but action withheld (see Decision Points).
7. **Act**: On `ROLLBACK`, the CI/CD controller reroutes traffic to the previous version. The engine records the rollback, starts the cooldown, and clears the streak. Breaches seen *during* the cooldown are detected and logged but do not advance the streak — samples taken while traffic is still rerouting say nothing about the version under test, so the next automatic rollback must earn a fresh confirmation streak once the cooldown expires.
8. **Escalate**: If the per-deployment rollback cap is reached and anomalies persist, the engine suppresses further automatic action and escalates to a human on-call (the rollback may be masking a deeper defect, or the anomaly is a real signal).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Decision Points

- **Market-open / fast-market window**: set `DeploymentHealthMetrics.market_open_volatility = True` for samples that overlap a known volatile window. The engine detects and logs the anomaly but suppresses rollback and does *not* advance the confirmation streak — the anomaly may be a genuine market event rather than a deployment defect.
- **Confirmation first, then the cap, then the cooldown**: the confirmation streak is evaluated before either loop guard, in every mode, so a single transient spike is never rolled back *or* escalated. Once an anomaly is confirmed, the cap decides whether it becomes a rollback or a page — at the default `max_rollbacks_per_deployment = 1` the confirmed breach after the one allowed rollback escalates immediately (CRITICAL log) instead of waiting out the cooldown, because an early page beats a quiet cooldown. The cooldown binds only while rollback budget remains. Both guards return `SUPPRESSED`; read `rollbacks_issued` and `remaining_cooldown_s` on the result to tell them apart.
- **Detection-only mode**: set `max_rollbacks_per_deployment = 0` to run the engine as a detector that never auto-rolls back (manual-approval mode for high-risk namespaces). Confirmation still applies — the first breach returns `CONFIRMING` and only a confirmed anomaly escalates, so a transient spike does not page the on-call engineer.
- **Threshold vs. baseline comparison**: the reference engine uses absolute threshold comparison (the THRESHOLD strategy). For tighter false-positive control on noisy strategies, adopt the PREVIOUS or CANARY_BASELINE strategies described in `references/standards.md`.

## Common Pitfalls

- **Ignoring Trading Metrics**: Monitoring only CPU and Memory while failing to monitor financial metrics like runaway order rates or exchange reject rates.
- **Rolling Back on a Single Transient Spike (Flapping)**: Triggering an irreversible rollback from one bad sample. A single latency spike during market-open volatility is not a deployment defect. Always require consecutive confirmation (`consecutive_failures_required >= 2`).
- **Rollback Loops**: rollback → redeploy → still flagged → rollback, cascading until the system thrashes itself. Enforce a cooldown and a hard per-deployment rollback cap; escalate to a human once the cap is hit.
- **The Anomaly Is a Real Signal**: a genuine market move (fast market, venue outage) can spike rejects and latency across *both* versions. Rolling back cannot fix a market event and may revert to a version that handles the current regime worse. Suppress during market-open volatility and confirm temporal correlation with the deployment.
- **Rollback Itself Causes Harm**: reverting mid-flight can drop in-flight order state, desync caches, or break forward/backward-compatible schema assumptions. Ensure message/cache formats and schema migrations are rollback-safe *before* relying on automated rollback.
- **Flaky Thresholds**: thresholds set too tight relative to normal market-open volatility, causing false-positive rollbacks. Calibrate thresholds against historical drawdowns and the prior version's baseline.
- **Trusting a Frozen Feed**: the gate can only judge the samples it is handed. A poller that repeats the last good sample when a scrape fails, or that defaults a missing series to `0`, holds the engine at `HEALTHY` for the entire burn-in window while a defective version keeps trading. Absence of a signal is not a healthy signal — treat a missing or stale sample as an evaluation failure and escalate.
- **NaN Defeats Every Comparison**: under IEEE 754 every ordered comparison against `NaN` is false, so a `NaN` latency reads as within threshold and a `NaN` or `Inf` *threshold* silently disables that metric for the whole deployment. This is not hypothetical: Prometheus transfers `NaN`/`Inf` as quoted JSON strings, which `float()` parses happily, and a reject-rate ratio over a window with no orders is `0/0`. The dataclasses reject non-finite values at construction; do not strip that guard to "keep the pipeline running".

## Verification

- Confirm the engine tracks both technical (latency, 5xx) AND trading (order reject rate) metrics, with severity classification.
- Confirm that a *single* breach returns `RollbackDecision.CONFIRMING`, not `ROLLBACK` (flapping guard).
- Confirm that `consecutive_failures_required` consecutive breaches return `ROLLBACK`, increment `rollbacks_issued`, and start the cooldown.
- Confirm a second rollback within the cooldown returns `SUPPRESSED`.
- Confirm that once `max_rollbacks_per_deployment` is reached, further *confirmed* breaches return `SUPPRESSED` (escalation).
- Confirm `market_open_volatility=True` suppresses rollback and does not advance the streak.
- Confirm breaches suppressed by an active cooldown do not advance the streak, so the first breach after the cooldown expires returns `CONFIRMING`, not `ROLLBACK`.
- Confirm detection-only mode (`max_rollbacks_per_deployment = 0`) still confirms first: a single breach returns `CONFIRMING`, and only a confirmed anomaly escalates.
- Confirm invalid config/metrics raise `ValueError` — negative latency, rates outside `[0, 1]`, and any `NaN`/`Inf` threshold or observation.
- Run `python -m unittest discover -s skills/automated-rollback-triggers-on-anomaly-detection/scripts`.

## Related Skills

- `canary-releases-for-strategy-code-changes`
- `blue-green-deployment-for-live-strategy-updates`
- `model-versioning-and-rollback`
- `data-quality-monitoring-dashboard`
