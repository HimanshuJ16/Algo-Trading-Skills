# Standards for Automated Rollback on Anomaly Detection

## 0. How to read this document

Section 1 lists **regulatory touchpoints**: obligations and published supervisory
expectations, with the jurisdiction stated. Sections 2-7 are **engineering standards** —
this repository's recommended practice, not legal requirements, labelled as such so an
agent does not present them to an operator as compliance mandates.

Every number below (a threshold, a window, a poll interval) is an engineering default to
be calibrated against your own baseline, not a figure any regulator has set. Nothing here
substitutes for your own compliance function's determination of which regime applies to
you.

## 1. Regulatory touchpoints

### 1.1 EU / UK — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

**Applicability:** investment firms engaged in algorithmic trading authorised under
MiFID II (Directive 2014/65/EU). It does **not** bind a US-only broker-dealer, a non-EU
proprietary trader, or an individual trading their own capital. The UK supervises a
materially equivalent onshored version.

| RTS 6 Article | Subject | Bearing on this skill |
|---|---|---|
| Art. 11 | Management of material changes | A material change to the production environment must be "preceded by a review of that change by a person designated by senior management", with review depth proportionate to the change. An automated rollback is itself a change to the production environment; automating the *trigger* does not remove the review. What gets reviewed and approved before the deploy is the rollback **policy** — thresholds, `consecutive_failures_required`, cooldown, cap, and the exact target version — which is why this skill insists thresholds are agreed beforehand and never improvised mid-deploy. |
| Art. 14(2)(f), 14(2)(g), 14(3), 14(4) | Business continuity arrangements | Arrangements must include "arrangements for shutting down the relevant trading algorithm or trading system where appropriate" and "alternative arrangements for the investment firm to manage outstanding orders and positions"; the firm "shall ensure that its trading algorithm or trading system can be shut down in accordance with its business continuity arrangements without creating disorderly trading conditions"; and the arrangements must be reviewed and tested annually. This is the regulatory shape of the "rollback itself causes harm" pitfall: an automatic rollback that abandons in-flight orders or leaves positions unmanaged is a shutdown that can create disorderly conditions. Before enabling automatic rollback, confirm the standby version adopts outstanding orders and positions — and include the rollback path in the annual test. |
| Art. 12 | Kill functionality | Ability "to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues". A rollback is **not** a kill switch: rerouting traffic to the previous version cancels nothing already resting at a venue. When the requirement is "stop, now", use `kill-switch-and-drawdown-circuit-breakers` / `execution-algorithm-kill-switch-integration`. |
| Art. 16 | Real-time monitoring | Firms must "monitor in real time all algorithmic trading activity that takes place under its trading code, including that of its clients, for signs of disorderly trading", with staff who respond in a timely manner and initiate remedial action. The deployment-health gate in this skill does **not** discharge that duty and must not be presented as doing so: it watches one deployment, for one burn-in window, on health metrics — not trading conduct across the firm's activity. Run both. |
| Art. 5(7) | Records of material change | Records must allow the firm to determine when a change was made, who made it, who approved it, and its nature. For an automated rollback, that record is the decision trail: versions/SHAs reverted from and to, the confirmed anomalies with observed values and thresholds, `rollbacks_issued`, and the confirmation streak at decision time. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex ELI
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. UK onshored text:
<https://www.legislation.gov.uk/eur/2017/589>.

### 1.2 US — FINRA Regulatory Notice 15-09

**Status:** effective-practice **guidance** addressed to FINRA member firms. It is not a
rule and sets no thresholds; it does not bind a non-member.

Three passages bear directly on post-deployment rollback:

- "when deploying new code, maintaining heightened scrutiny of the impacted trading
  account, including real-time monitoring of the subject algorithmic strategy" — the
  burn-in window this skill runs is that heightened scrutiny, expressed in supervisory
  language.
- "providing mechanisms by which the firm may quickly disable the algorithm or supporting
  platform with a minimal number of steps" — an argument for keeping rollback a traffic
  reroute to a warm standby rather than a re-provision.
- "where feasible, deploying new algorithmic strategies in a pilot phase of limited size,
  increasing only as results are confirmed" — the rollback gate is the exit criterion of
  such a pilot; see `canary-releases-for-strategy-code-changes`.

<https://www.finra.org/rules-guidance/notices/15-09>

**Boundary:** SEC Rule 15c3-5 (17 CFR 240.15c3-5) governs automated **pre-trade** risk
controls for broker-dealers with market access. A post-deployment health gate is not such
a control and never substitutes for one — it acts after orders have already been sent.
See `sec-rule-15c3-5-risk-controls-us`.

## 2. Engineering standard — metric thresholds and severities

*Recommended practice, not a regulatory requirement.*

| Metric Category | Metric | Baseline Threshold | Severity | Rollback Reason |
|---|---|---|---|---|
| Technical | `latency_ms` | < 50ms (tune to p99 of prior version) | `WARNING` | System cannot respond to fast-market conditions. |
| Technical | `http_5xx_error_rate` | < 1% | `CRITICAL` | Internal server crashes or unhandled exceptions. |
| Trading | `order_reject_rate` | < 2% | `CRITICAL` | Logic bug causing the exchange to reject malformed orders; direct financial impact. |

Comparison is strict (`>`): a value exactly equal to its threshold is not a breach, so
state thresholds as the highest value you will tolerate, not the lowest you will reject.

Thresholds must be agreed **before** the deployment begins, calibrated against the
prior version's baseline and historical drawdown windows — never improvised mid-deploy.
For an in-scope EU firm that pre-agreement is also what the Art. 11 review in Section 1.1
is reviewing.

## 3. Engineering standard — rollback policy parameters

*Recommended practice, not a regulatory requirement.*

| Parameter | Default | Purpose |
|---|---|---|
| `consecutive_failures_required` | 2 | Number of consecutive breaching snapshots required before rollback. Prevents flapping from a single transient spike. |
| `rollback_cooldown_seconds` | 300 | Seconds that must elapse after a rollback before another is allowed. Prevents rollback loops. Breaches observed *during* the cooldown are logged but do not advance the confirmation streak, so the next rollback needs a fresh streak — a cooldown that only postpones the streak paces a rollback loop instead of preventing it. |
| `max_rollbacks_per_deployment` | 1 | Hard cap on automatic rollbacks per deployment. Once reached, escalate to a human. `0` = detection-only / manual-rollback mode. |

Guard order: the confirmation streak is evaluated **before** either loop guard, in every
mode, so a single transient spike is never rolled back or escalated — including in
detection-only mode, where the cap decides only that a *confirmed* anomaly becomes a page
rather than a rollback. The cooldown is then bypassed once the cap is reached, so a firm
out of rollback budget pages immediately instead of after the cooldown expires.

## 4. Engineering standard — telemetry validity

*Recommended practice, not a regulatory requirement.*

A rollback gate is only as good as the samples it is handed, and its dangerous failure is
silent: a gate that cannot see reports `HEALTHY`.

- **Non-finite values are rejected, not compared.** Every ordered comparison against `NaN`
  is false under IEEE 754, so a `NaN` observation reads as within threshold and a `NaN` or
  `Inf` *threshold* disables that metric for the whole deployment. Prometheus states that
  because "JSON does not support special float values such as `NaN`, `Inf`, and `-Inf`,
  ... sample values are transferred as quoted JSON strings rather than raw numbers", so a
  poller doing `float(value)` reproduces them exactly; a reject-rate ratio over a window
  with no orders is `0/0`. `RollbackThresholdConfig` and `DeploymentHealthMetrics` raise
  `ValueError` on any non-finite field.
  <https://prometheus.io/docs/prometheus/latest/querying/api/>
- **A missing sample is not a healthy sample.** A scrape that times out, returns no
  series, or returns a series older than the poll interval must abort the evaluation and
  escalate. Substituting the previous value, or defaulting a missing series to `0`, holds
  the gate at `HEALTHY` for the whole burn-in window while a defective version trades.
- **Label the sample to the version.** Metrics must be labelled by version/SHA, or the
  gate measures the fleet rather than the deployment and the previous version's healthy
  traffic dilutes the new version's breach.

## 5. Engineering standard — anomaly-comparison strategies

*Recommended practice, not a regulatory requirement.*

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

## 6. Engineering standard — burn-in / bake window

*Recommended practice, not a regulatory requirement.*

| Workload type | Suggested window | Rationale |
|---|---|---|
| Low-latency trading APIs | 5–10 minutes | Enough samples at 5–10s polling to confirm, short enough to act. |
| Batch / settlement | 15–30 minutes | Slower signal accrual; avoid rolling back on a single slow batch. |
| Daily releases | Shorter window | Frequent deploys need fast feedback. |
| Weekly releases | Longer window | Infrequent deploys warrant more observation. |

Run **only one canary/deployment evaluation at a time** to avoid signal contamination.
The Google SRE Workbook is explicit: "Running simultaneous canaries also increases the
risk of signal contamination if the canaries overlap. We strongly advise running only one
canary deployment at a time." <https://sre.google/workbook/canarying-releases/>

The burn-in window must be sized in **samples**, not calendar time alone: a 10-minute
window at a 60s poll interval yields ten observations, which cannot support the
confirmation policy the way a 5–10s interval can.

## 7. Escalation and observability

*Recommended practice; for an in-scope EU firm the record-keeping element also carries the
Art. 5(7) obligation in Section 1.1.*

- On reaching `max_rollbacks_per_deployment` with anomalies persisting, the
  engine returns `SUPPRESSED` and logs a CRITICAL escalation message. Past the cap the
  confirmation streak keeps advancing as an escalation signal — it says how many
  consecutive samples have breached — even though no automatic rollback can follow.
- The on-call page must include: deployment version/SHA, the confirmed
  anomalies with observed values and thresholds, `rollbacks_issued`, and the
  consecutive-failure count at decision time.
- Repeated escalation without human acknowledgement must not auto-resume
  rollbacks; the deployment is frozen until a human clears the state via
  `engine.reset()`.
- Dashboards must show version/SHA, environment, traffic share, error rate,
  latency, saturation, and dependency health. Logs must include the release
  version/commit SHA so anomalies can be correlated to a specific deployment.
  Structured log fields emitted by the engine: `decision`, `consecutive_failures`,
  `rollbacks_issued`, `remaining_cooldown_s`, `anomaly_count`.
- Both a cap-suppressed and a cooldown-suppressed evaluation return `SUPPRESSED`.
  Distinguish them in dashboards and alerts by `rollbacks_issued` and
  `remaining_cooldown_s`, not by the decision alone.

## Category

`deployment-ops`
