# Workflows for Runbook Automation for Common Incident Types

## 0. Wiring, once, at startup

Nothing in this engine executes until you bind a handler. Do it at process
start, then fail the deployment if anything is left unbound.

```python
from runbook_incident_automator import (
    IncidentAlert, IncidentType, RemediationAction,
    RunbookIncidentAutomationEngine, RunbookInputError,
)

engine = RunbookIncidentAutomationEngine(
    is_dry_run=False,
    step_timeout_seconds=10.0,     # your budget, not a standard -- see standards.md
)

engine.register_handler(RemediationAction.CANCEL_OPEN_ORDERS,  risk.cancel_all_open_orders)
engine.register_handler(RemediationAction.TRIGGER_KILL_SWITCH, risk.latch_kill_switch)
engine.register_handler(RemediationAction.FAILOVER_VENUE,      sor.failover_to_backup)
engine.register_handler(RemediationAction.RECONNECT_SOCKET,    feed.reconnect)
engine.register_handler(RemediationAction.THROTTLE_ORDER_RATE, sor.halve_order_rate)

unbound = engine.unhandled_actions()
if unbound:
    raise SystemExit(f"Refusing to start: unwired remediation actions {unbound}")
```

**Handler contract.** A handler receives the `IncidentAlert` and performs the
action. It signals failure by **raising**, or by returning exactly `False`. Any
other return value — including `None` — counts as success, so a handler that
forgets a `return` is not mistaken for a failed kill switch. Handlers must set
their own transport-level timeouts; `step_timeout_seconds` bounds only how long
the engine waits.

## 1. Alert ingestion

Alerts arrive as JSON from Alertmanager, a broker webhook, or your own risk
engine. Construct the `IncidentAlert` inside a `try` and treat a rejection as a
page, never as a reason to guess:

```python
try:
    alert = IncidentAlert(
        incident_id=payload["fingerprint"],       # stable per incident, not per delivery
        incident_type=payload["labels"]["incident_type"],   # str is accepted
        severity=payload["labels"]["severity"],
        source_service=payload["labels"]["service"],
        metric_value=payload["annotations"]["value"],
        threshold_value=payload["annotations"]["threshold"],
        timestamp_iso=payload["startsAt"],        # must carry a UTC offset
    )
except RunbookInputError as exc:
    pager.page(tier="PRIMARY", detail=f"Unclassifiable incident alert: {exc}")
    return
```

Validation performed on construction:

| Field | Rule | Why |
|---|---|---|
| `incident_id` | non-empty string, stripped | it is the deduplication key |
| `incident_type` | `IncidentType` or its exact name (case-insensitive) | an unrecognised label must not select a remediation sequence |
| `severity` | `CRITICAL`/`HIGH`/`MEDIUM`; anything else recorded as `CRITICAL` with `severity_was_coerced=True` | guessing severity downward hides the worst incidents |
| `source_service` | non-empty string | RTS 6 Art. 12(3) attribution |
| `metric_value`, `threshold_value` | finite floats; numeric strings coerced | `NaN` compares False against every threshold and prints as `nan` in the record |
| `timestamp_iso` | ISO-8601 **with** a UTC offset (`Z` accepted); normalised to UTC | a local-time incident record cannot be ordered against venue logs |

**Stable `incident_id`.** If your transport mints a new id per delivery,
deduplication cannot work. Alertmanager's `fingerprint`, PagerDuty's
`dedup_key`, or your own hash of `(incident_type, source_service, window)` are
all appropriate; a UUID generated at send time is not.

## 2. Playbook lookup

| Incident type | Sequence | Branch rules |
|---|---|---|
| `FEED_DISCONNECT` | `RECONNECT_SOCKET` → `FAILOVER_VENUE` | reconnect is `terminal_on_success`: a recovered socket does not then move venues |
| `LATENCY_SPIKE` | `THROTTLE_ORDER_RATE` → `FAILOVER_VENUE` | throttling is reversible and local; try it before a failover that is neither |
| `BROKER_API_OUTAGE` | `CANCEL_OPEN_ORDERS` → `FAILOVER_VENUE` | the cancel runs through the failing broker and is *expected* to fail sometimes; the failover must not be abandoned with it |
| `DRAWDOWN_BREACH` | `CANCEL_OPEN_ORDERS` → `TRIGGER_KILL_SWITCH` | RTS 6 Art. 12(1) cancel, then Art. 14(2)(f) shutdown; the kill switch is attempted even if the cancel fails |
| `ORDER_THROTTLE` | `THROTTLE_ORDER_RATE` | single step |

There is **no default playbook**. An incident type with no registered sequence
produces zero steps, `ESCALATED`, and `requires_human_escalation=True`. The
1.0.0 engine defaulted to `CANCEL_OPEN_ORDERS`, which meant an incident class
nobody had thought about triggered a mass cancel.

To override:

```python
from runbook_incident_automator import PlaybookStep

engine.register_playbook(IncidentType.LATENCY_SPIKE, [
    PlaybookStep(RemediationAction.THROTTLE_ORDER_RATE, terminal_on_success=True),
    PlaybookStep(RemediationAction.CANCEL_OPEN_ORDERS),
    PlaybookStep(RemediationAction.FAILOVER_VENUE),
])
```

`PlaybookStep` fields:

- `terminal_on_success` — if this step succeeds, the incident is remediated and
  the remaining steps are skipped as `SKIPPED_ALREADY_REMEDIATED`.
- `halt_on_failure` — if this step fails, abandon the rest
  (`SKIPPED_AFTER_HALT`). **Defaults to `False`.** Set it to `True` only where
  continuing would itself be unsafe; never on the step before a kill switch.

An empty playbook is rejected: it would report `RESOLVED` having remediated
nothing.

## 3. Step execution

Per step, in order:

1. **No handler bound** → `NO_HANDLER_REGISTERED`, logged at ERROR, contributes
   an escalation reason. This is checked in dry-run mode too.
2. **Dry run** → `SKIPPED_DRY_RUN`, handler not called.
3. **Live** → the handler is invoked on a daemon thread and joined with
   `step_timeout_seconds`:
   - returns anything but `False` → `SUCCESS`
   - returns `False` → `FAILED`
   - raises → `FAILED`, with the exception type and message in `detail`
   - still running at the deadline → `TIMED_OUT`

`step_timeout_seconds=None` calls handlers inline with no bound at all. Only
appropriate when every handler enforces its own deadline.

**A `TIMED_OUT` step is an unknown, not a failure.** Python cannot cancel a
running thread, so the handler is still executing. A cancel request that timed
out may already have reached the broker. Reconcile broker state before any
retry — see `order-placement-idempotency`.

## 4. Idempotency and redelivery

```python
report = engine.execute_runbook(alert)          # runs the playbook
again  = engine.execute_runbook(alert)          # returns the stored report, runs nothing
assert again.duplicate_delivery_count == 1
```

Alertmanager `repeat_interval`, webhook retries and at-least-once queues all
redeliver. Without this, a flapping feed alert trips the kill switch once per
delivery.

`force_reexecute=True` re-runs the playbook and appends a second audit record.
It is for an operator-authorised retry after a partial failure. Record who
authorised it, and never wire it to the alert webhook.

Concurrency: `execute_runbook` holds the engine lock for the whole execution, so
two simultaneous deliveries of the same alert cannot both run the playbook.
Handlers therefore run one at a time per engine instance — if you need
parallelism across unrelated incidents, use one engine per incident stream.

## 5. Reading the report

```python
if report.requires_human_escalation:
    pager.page(tier="PRIMARY", detail="\n".join(report.escalation_reasons))
audit_store.persist(report)
```

| Field | Meaning |
|---|---|
| `status` | `RESOLVED` \| `ESCALATED` \| `DRY_RUN_COMPLETE` |
| `requires_human_escalation` | exactly `bool(escalation_reasons)` — branch on this |
| `escalation_reasons` | one entry per failed, timed-out or unwired step, plus a missing-playbook reason |
| `steps_executed` | frozen `RemediationStep` records with `duration_ms` each |
| `total_time_taken_ms` | wall time for the whole runbook |
| `is_dry_run` | whether any handler was actually invoked |
| `duplicate_delivery_count` | redeliveries seen after the first execution |
| `severity_was_coerced` | the alert source used a severity label the engine does not know |
| `executed_at_utc_iso` | when the engine ran, distinct from the alert timestamp |
| `audit_notes` | one-line human-readable summary for the log |

`RESOLVED` means every attempted step succeeded. It does **not** mean the
underlying fault is gone — verify independently before resuming trading.

## 6. Persistence and retention

`get_audit_history()` returns deep copies, oldest first, capped by
`max_audit_history` (default 10 000) and dropped oldest-first past that bound.
It is in-process memory and dies with the process.

DORA Art. 17(2) requires financial entities to "record all ICT-related
incidents". Persist each report as it is produced — the in-memory list is a
debugging convenience. Note that once a report is trimmed, its `incident_id`
also loses deduplication, so a very old alert redelivered after the ring has
wrapped will execute again.

## 7. Pre-flight testing

```python
dry = RunbookIncidentAutomationEngine(is_dry_run=True, step_timeout_seconds=10.0)
# register the same handlers as production
for incident_type in IncidentType:
    report = dry.execute_runbook(build_synthetic_alert(incident_type))
    assert report.status is IncidentStatus.DRY_RUN_COMPLETE
    assert not report.requires_human_escalation, report.escalation_reasons
```

A dry run verifies sequencing **and** wiring: an unbound action still reports
`NO_HANDLER_REGISTERED`. Run this in CI and on the annual business-continuity
test required by RTS 6 Art. 14(4).

A dry run against production is **not** the separated test environment RTS 6
Art. 7 requires for pre-deployment testing. It is a readiness check on top of it.

## 8. Post-incident

Feed `steps_executed`, `escalation_reasons` and `duration_ms` into the
post-mortem. A `TIMED_OUT` or `FAILED` step is a finding about the handler or
the dependency behind it; a `NO_HANDLER_REGISTERED` step is a finding about the
deployment gate that let it through. See
`post-mortem-culture-and-blameless-review-process`.
