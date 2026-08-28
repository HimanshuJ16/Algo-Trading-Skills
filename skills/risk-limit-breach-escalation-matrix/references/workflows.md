# Workflows for Risk Limit Breach Escalation Matrix

The engine is a pure decision function with three pieces of state: the audit
trail, the latched-incident map, and the replay cache. It never calls out.

## 0. Configure the ladder once, at construction

```python
matrix = RiskEscalationMatrix(
    policies=None,                    # None -> DEFAULT_POLICIES; [] is an error
    sustained_breach_seconds=300.0,
    latch_escalations=True,
)
```

Construction validates the ladder and raises `InvalidPolicyError` if it is not
one: thresholds must be strictly ascending and unique, severity and action must
be non-decreasing along it, every tier must route to at least one channel, and
every `ack_timeout_seconds` must be positive. An empty `policies` list is an
error rather than a silent fallback to the defaults — an operator who configured
away every tier needs to be told, not handed the stock ladder back.

The legacy positional levels (`warn_lvl`, `reduce_lvl`, `halt_lvl`,
`flatten_lvl`) drive only the legacy `evaluate()` API and must be strictly
ascending. All-equal levels used to collapse into a single dictionary key and
silently delete three tiers.

## 1. Breach event ingestion and validation

Everything is validated at the engine boundary; `BreachEvent` itself validates
nothing, so a malformed payload fails where it is evaluated.

| Field | Rule on failure |
|---|---|
| `event_id`, `metric_name`, `strategy_id` | Non-blank strings, stripped. `InvalidBreachError`. |
| `current_value`, `limit_value`, `duration_seconds` | Coerced from JSON strings; `bool` refused; NaN/Inf refused. `InvalidBreachError`. |
| `limit_value` | Must be `> 0`. |
| `duration_seconds` | Must be `>= 0`. |
| `timestamp_iso` | ISO-8601 **with a UTC offset**; normalised to `...Z`. A naive timestamp is refused because it cannot be reconciled with venue records. |
| `current_value` under `UPPER` | Must be `>= 0`. A negative magnitude is refused, not reinterpreted. |

## 2. Replay check, before anything is computed

The engine fingerprints `(metric_name, strategy_id, current_value, limit_value,
duration_seconds, direction, timestamp_iso)` per `event_id`.

- **Same id, same fingerprint** → the original decision is returned with
  `is_replay=True`, nothing is re-logged, and no second audit row is written.
  `FLATTEN` is destructive and alert pipelines retry on timeout.
- **Same id, different fingerprint** → a warning is logged and the event is
  processed. This is how a monitor reports an ongoing breach with a growing
  `duration_seconds`; deduping on the id alone would freeze the incident at its
  first decision and defeat duration escalation entirely.

## 3. Ratio computation

| Direction | Formula | Use for |
|---|---|---|
| `UPPER` (default) | `current / limit` | drawdown, exposure, leverage, VaR, position count, OTR |
| `LOWER` | `max(0, 1 + (limit - current) / limit)` | free margin, cash buffer, collateral coverage |

Worked `LOWER` values against a 50,000 floor: 60,000 → 0.8 (no breach);
50,000 → 1.0; 40,000 → 1.2; 25,000 → 1.5; 0 → 2.0; 125,000 → floored at 0.0.

The ratio is compared **exactly**. It is rounded only for display, in the audit
note. Rounding before comparison made 1.99996x match a 2.0 threshold.

## 4. Tier matching

Walk the ascending ladder in reverse and take the first tier with
`ratio >= threshold`. Thresholds are inclusive: exactly 1.0x is a breach.

If no tier matches, a `NONE` decision is produced **and recorded**. A
`BreachEvent` that evaluates below the lowest tier says something about the
upstream detector, and discarding it left a mis-signed or mis-scaled input with
no trace at all.

## 5. Duration escalation

```
is_sustained = duration_seconds >= sustained_breach_seconds
if is_sustained and matched_index + 1 < len(policies):
    promote wholesale to policies[matched_index + 1]
```

Promotion carries **severity, action, channels and acknowledgement deadline
together**, one rung, positionally. Three consequences:

- A sustained AMBER breach becomes a full RED rung — HALT *and* PagerDuty *and*
  the 120 s deadline. Promoting the action alone left the alert on Slack and
  e-mail with the AMBER deadline.
- A sustained RED breach becomes CRITICAL/FLATTEN. The old hard-coded
  `WARN→REDUCE→HALT` chain stopped at HALT, so a 1.6x breach held for four hours
  never escalated.
- A ladder built from `THROTTLE` or `GLOBAL_KILL_SWITCH` escalates correctly,
  because promotion is by position, not by action name.

At the top rung there is nothing to promote to: the decision keeps
`is_sustained_breach=True`, sets `is_duration_escalated=False`, and the audit
note records that no higher rung exists.

## 6. Latching

Keyed on `(strategy_id, metric_name)`. If the incident has already reached a
stronger action than this observation warrants, the stronger one is retained
along with its severity, channels and deadline, and `is_latched=True`. A metric
oscillating around a threshold cannot cancel an in-flight FLATTEN.

De-escalation is explicit and logged at WARNING:

```python
matrix.reset_incident("STAT_ARB_01", "DAILY_DRAWDOWN")   # -> bool
matrix.get_active_incidents()                            # snapshot copy
```

Set `latch_escalations=False` for a purely stateless evaluator.

## 7. Notification routing and the audit trail

Hand `action` to the enforcement layer and `notification_channels` to the
notifier. Neither field is evidence that anything happened; the engine performs
no I/O beyond logging.

Logging severity follows the decision: CRITICAL → `logger.critical`,
RED → `logger.warning`, everything else → `logger.info`.

`get_audit_trail()` returns a tuple of frozen `EscalationDecision` records,
oldest first, carrying the verdict *and* the inputs behind it (`current_value`,
`limit_value`, `duration_seconds`, normalised `timestamp_iso`,
`matched_threshold`, `is_duration_escalated`, `is_latched`, `is_replay`). The
engine never truncates the trail — silently dropping rows from a risk-control
audit trail is worse than the memory — so a long-running process must drain and
persist it.

## 8. Legacy API

`evaluate(risk_metric, limit) -> EscalationResult` is retained for backward
compatibility and uses only the four positional levels. It now raises
`InvalidBreachError` on a non-positive limit, a negative metric or a non-finite
input; it previously returned `ResponseAction.NONE`, so `evaluate(1e9, 0)`
answered a nine-figure risk metric with "take no action".

## 9. Wiring checklist

1. Compute the metric and its persistence duration upstream — the engine holds
   no clock.
2. Call `process_breach_event()` inside a `try/except EscalationMatrixError`.
   Validation failures are loud by design and must not kill the monitor loop;
   route them to the same alerting path as a breach, because a rejected input
   means the control is blind to that metric.
3. Execute `action` idempotently at the enforcement layer, keyed on `event_id`.
   Replay protection here does not make your order gateway idempotent.
4. Deliver to every channel in `notification_channels`; treat a delivery failure
   on a CRITICAL tier as an incident in its own right.
5. Persist the audit row before acting where the sequencing allows it, so the
   record survives a crash mid-action.
