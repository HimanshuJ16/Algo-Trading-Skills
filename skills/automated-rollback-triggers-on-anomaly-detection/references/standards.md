# Standards for Automated Rollback on Anomaly Detection

## Metric Thresholds and Severities

| Metric Category | Metric | Baseline Threshold | Severity | Rollback Reason |
|---|---|---|---|---|
| Technical | `latency_ms` | < 50ms (tune to p99 of prior version) | `WARNING` | System cannot respond to fast-market conditions. |
| Technical | `http_5xx_error_rate` | < 1% | `CRITICAL` | Internal server crashes or unhandled exceptions. |
| Trading | `order_reject_rate` | < 2% | `CRITICAL` | Logic bug causing the exchange to reject malformed orders; direct financial impact. |

Thresholds must be agreed **before** the deployment begins, calibrated against the
prior version's baseline and historical drawdown windows — never improvised mid-deploy.

## Rollback Policy Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `consecutive_failures_required` | 2 | Number of consecutive breaching snapshots required before rollback. Prevents flapping from a single transient spike. |
| `rollback_cooldown_seconds` | 300 | Seconds that must elapse after a rollback before another is allowed. Prevents rollback loops. |
| `max_rollbacks_per_deployment` | 1 | Hard cap on automatic rollbacks per deployment. Once reached, escalate to a human. `0` = detection-only / manual-rollback mode. |

## Anomaly-Comparison Strategies

The reference engine implements the **THRESHOLD** strategy (absolute threshold
comparison). For strategies with higher false-positive pressure, an operator may
adopt one of the following, ordered by increasing fidelity:

1. **THRESHOLD** — flag a metric if it exceeds its absolute threshold. Simplest,
   explainable, and the reference implementation. Best when thresholds are
   well-calibrated against a stable baseline.
2. **PREVIOUS** — additionally require the metric to be worse than the *last
   successful deployment's* value by a configurable margin (e.g. 1.5x). Avoids
   rolling back a deployment that is merely "different" but still within an
   acceptable band. Useful when the absolute threshold is loose.
3. **CANARY_BASELINE** — statistically compare the canary variant against the
   baseline/control variant over the burn-in window (e.g. Mann-Whitney U test on
   per-request latency). Highest fidelity; requires per-variant metric labels
   (`app`, `env`, `version`/`sha`) and a sufficient sample size. Recommended for
   high-frequency strategies where small distributional shifts matter.

Regardless of strategy, **detection is separate from action**: a detected
anomaly is gated by the consecutive-confirmation, cooldown, cap, and
market-open guards before it becomes a rollback.

## Burn-in / Bake Window Guidance

| Workload type | Suggested window | Rationale |
|---|---|---|
| Low-latency trading APIs | 5–10 minutes | Enough samples at 5–10s polling to confirm, short enough to act. |
| Batch / settlement | 15–30 minutes | Slower signal accrual; avoid rolling back on a single slow batch. |
| Daily releases | Shorter window | Frequent deploys need fast feedback. |
| Weekly releases | Longer window | Infrequent deploys warrant more observation. |

Run **only one canary/deployment evaluation at a time** to avoid signal
contamination (Google SRE guidance).

## Escalation Policy

- On reaching `max_rollbacks_per_deployment` with anomalies persisting, the
  engine returns `SUPPRESSED` and logs a CRITICAL escalation message.
- The on-call page must include: deployment version/SHA, the confirmed
  anomalies with observed values and thresholds, `rollbacks_issued`, and the
  consecutive-failure count at decision time.
- Repeated escalation without human acknowledgement must not auto-resume
  rollbacks; the deployment is frozen until a human clears the state via
  `engine.reset()`.

## Observability Requirements

Dashboards must show version/SHA, environment, traffic share, error rate,
latency, saturation, and dependency health. Logs must include the release
version/commit SHA so anomalies can be correlated to a specific deployment.
Structured log fields emitted by the engine: `decision`, `consecutive_failures`,
`rollbacks_issued`, `remaining_cooldown_s`, `anomaly_count`.

## Category

`deployment-ops`
