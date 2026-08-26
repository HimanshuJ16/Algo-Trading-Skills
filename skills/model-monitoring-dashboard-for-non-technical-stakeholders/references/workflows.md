# Deep Workflow Reference — model-monitoring-dashboard-for-non-technical-stakeholders

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 0. Configure the bands, once, deliberately

`DashboardThresholds` is frozen and validated on construction. Incoherent
configuration raises `DashboardConfigError` at configuration time rather than
producing a silently wrong grade at evaluation time: inverted bands, an accuracy
band above 100, a zero or negative PSI band, a half-configured latency budget (one
bound set and the other `None`), or a non-finite edge.

Latency bounds have no default. There is no defensible universal inference-latency
budget — a sub-millisecond tick-to-trade path and a five-second end-of-day rebalance
are both normal — so `latency_green_max_ms` and `latency_amber_max_ms` default to
`None` and must be set together. Leaving them unset does **not** disable the check:
the latency component then grades AMBER as unconfigured, which is a visible gap
rather than a silent pass. To declare latency genuinely out of scope, construct with
`monitor_latency=False`; the component is then omitted entirely and every report
records `latency_monitored=False`.

### 1. Ingest raw telemetry

Four optional inputs: `accuracy_pct` (percentage points, $[0, 100]$),
`staleness_days` (non-negative whole days), `feature_drift_psi` (non-negative,
the per-feature **maximum**), `latency_ms` (non-negative, the p99).

Any of them may be `None`, meaning "not reported this cycle". `None` is a legitimate
state and is graded, not rejected. What is rejected is an *impossible* value.

### 2. Validate before grading

`DashboardInputError` is raised for:

- a non-`str` or blank `model_name`;
- a non-finite metric (`NaN`, $\pm\infty$) — every `>=` comparison against `NaN` is
  `False`, so an unguarded ladder walks past every band and lands on the last branch;
- a `bool`, `str`, `list` or other non-real metric. The guards test against the
  stdlib `numbers.Real` / `numbers.Integral` ABCs rather than `float` / `int`, so a
  `numpy.int64` or pandas scalar pulled straight from a telemetry frame is accepted
  without this module depending on numpy. `bool` registers as `Integral` and needs
  its own guard (`isinstance(True, int)` is `True` in Python);
- `accuracy_pct` outside $[0, 100]$;
- a non-integer or negative `staleness_days` — a negative age means a clock skew or
  a broken `last_retrained_at`, not a fresh model;
- a negative `feature_drift_psi` — PSI is non-negative by construction, so a
  negative value means a broken drift computation;
- a negative `latency_ms`.

An `accuracy_pct` strictly between 0 and 1 logs a WARNING rather than raising: `0.58`
is a legal accuracy and almost always a mis-scaled 58%, but 0.58% is not impossible.

**These exceptions are monitoring failures, not passing grades.** Escalate them as
you would a RED. Wrapping `evaluate_health` in `try/except` and rendering a blank
tile silently deletes the control.

### 3. Grade each component

| Component | GREEN | AMBER | RED |
|---|---|---|---|
| Prediction Accuracy | $a \ge$ `accuracy_green_min_pct` | `accuracy_amber_min_pct` $\le a <$ green | $a <$ `accuracy_amber_min_pct` |
| Model Age | $d \le$ `staleness_green_max_days` | green $< d \le$ `staleness_amber_max_days` | $d >$ `staleness_amber_max_days` |
| Feature Drift PSI | $\text{PSI} <$ `drift_psi_green_max` | green $\le \text{PSI} <$ `drift_psi_amber_max` | $\text{PSI} \ge$ `drift_psi_amber_max` |
| Inference Latency | $\ell \le$ `latency_green_max_ms` | green $< \ell \le$ `latency_amber_max_ms` | $\ell >$ `latency_amber_max_ms` |

The PSI row's edges belong to the worse band; the other three are GREEN-inclusive.
The reason for the asymmetry is in `references/standards.md`: the PSI row reproduces
a cited convention exactly, the other three have no source to reproduce.

An unmeasured metric — `None`, or a latency with no configured budget — yields
`ModelHealthComponent(status=AMBER, measured=False, value=None)` with a summary
naming the reason. `value` is never `0.0` for a metric that was not computed.

### 4. Aggregate

Overall status is the **worst** component status under the ordering
GREEN $<$ AMBER $<$ RED. Never an average, and never a weighted score: those exist
precisely to let a severe breach in one component be absorbed by health in the
others.

`driving_components` lists the names of every component sitting at the overall
status, and is empty when the overall status is GREEN.

### 5. Recommend

| Condition | Action |
|---|---|
| Overall GREEN | `NO_ACTION_REQUIRED` |
| Overall AMBER, and every AMBER component is unmeasured | `RESTORE_MODEL_TELEMETRY` |
| Overall AMBER, with at least one measured AMBER component | `SCHEDULE_RETRAIN_AND_REVIEW` |
| Overall RED | `HALT_TRADING_IMMEDIATELY` |

The distinction in the middle two rows matters operationally: recommending a retrain
because the drift exporter went down sends the operator to rebuild the model when
the fault is in the pipeline, and a retrain fitted on a degraded feed is worse than
no retrain at all.

The headline names the driving components and, for RED, states explicitly that the
action is advisory. Log severity follows the status: `logger.error` for RED,
`logger.warning` for AMBER, `logger.info` for GREEN.

### 6. Retain the report

`DashboardReport.to_dict()` returns a JSON-serialisable snapshot including every
component's status, value, unit, summary and `measured` flag, the driving
components, and `latency_monitored`. Retain the snapshots: monitoring evidence is an
input to the RTS 6 Article 9 annual self-assessment and validation report, and a
rendered tile is not evidence.

## What happens after a colour

The module produces a recommendation and nothing else. The surrounding process is
what makes it a control:

- **RED** → escalate through `risk-limit-breach-escalation-matrix`; execute any halt
  through `kill-switch-and-drawdown-circuit-breakers`. Before acting on the model,
  run `concept-drift-vs-staleness-differentiation` — a frozen feature pipeline
  replays the reference distribution and looks like a healthy model, while a genuine
  $P(Y \mid X)$ shift and a covariate shift call for different remediations.
- **AMBER, measured** → open a change request. ESMA lists retraining an ML component
  among the change types warranting retesting, so the sequence is
  alert → recorded, timestamped, approved change → testing → controlled deployment
  (`model-versioning-and-rollback`, `canary-releases-for-strategy-code-changes`).
  Never alert → retrain → deploy.
- **AMBER, unmeasured** → this is an observability incident, not a model incident.
  Fix the exporter. Until it is fixed, the model's quality is unverified — which is
  not the same as bad, and not the same as fine.

## Migration from v1.x

`evaluate_health`'s signature is unchanged and the four metrics may now also be
`None`. Behaviour changed in ways that will move existing dashboards:

| Change | v1.x | v2.0.0 |
|---|---|---|
| `latency_ms` | accepted and never evaluated | graded against the configured budget |
| No latency budget configured | GREEN | AMBER, `RESTORE_MODEL_TELEMETRY` |
| PSI exactly $0.10$ | GREEN | AMBER |
| PSI exactly $0.25$ | AMBER | RED |
| Negative model age, negative PSI, accuracy outside $[0, 100]$ | GREEN | `DashboardInputError` |
| `NaN` metric | RED, with `nan` rendered as the measured value | `DashboardInputError` |
| RED headline | "Immediate intervention required" | names the breaching components, states the action is advisory |
| Recommended actions | 3 | 4 (`RESTORE_MODEL_TELEMETRY` added) |

A caller that previously constructed `NonTechnicalMonitoringDashboard()` and passed a
latency value will now see AMBER until it either configures a latency budget or
passes `monitor_latency=False`. That is the intended forcing function: the v1
behaviour was a control that did not exist.

## Production Implementation Reference

- Reference code: `scripts/monitoring_dashboard.py`
  (`NonTechnicalMonitoringDashboard`, `DashboardThresholds`, `DashboardReport`,
  `ModelHealthComponent`, `HealthStatus`, `DashboardConfigError`,
  `DashboardInputError`).
- Automated unit tests: `scripts/test_monitoring_dashboard.py` (45 tests).
