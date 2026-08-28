# Risk-Control Latency Workflow

## 1. Declare the required end state per control

Write it down before instrumenting, because it decides which window the budget applies to and
therefore what "compliant" means.

| Control | Required end state | Why |
|---|---|---|
| Pre-trade credit / size / price rejection | `DISPATCH` (the reject never leaves the process) | The control completes locally; there is nothing to acknowledge. |
| Position or leverage limit block | `DISPATCH` | Same — the order is suppressed before it exists at the venue. |
| Kill switch / cancel-all | `ACKNOWLEDGEMENT` | RTS 6 Article 12 obliges the firm to cancel unexecuted orders; only the venue's acknowledgement evidences that they are gone. |
| Drawdown breaker that flattens exposure | `ACKNOWLEDGEMENT`, plus a separate exposure check | Acknowledgement of the closing order is necessary but not sufficient; fills are the real end state. |
| Hedge trigger | `ACKNOWLEDGEMENT` | Exposure is unhedged until the venue accepts the hedge. |

A control whose protection depends on someone else acting cannot be budgeted on `DISPATCH`. If
you cannot obtain an acknowledgement timestamp, that is a gap to close, not a reason to fall
back to the send timestamp — the engine will report `UNCERTAIN`, which is the accurate verdict.

## 2. Instrument the boundaries under one timestamp policy

- Stamp `t_event_ms` where the *triggering observation* enters the process (feed callback,
  fill event, position update) — not where the risk thread happened to pick it up. The gap
  between the two is queue age, and it is frequently the largest stage.
- Stamp `t_start_ms` and `t_end_ms` around the evaluation only.
- Stamp `t_order_sent_ms` at the last point under your control before the socket write.
- Stamp `t_ack_ms` from the venue's acknowledgement as received, not from the venue's own
  timestamp field unless you have verified both clocks against the same reference.
- Use a monotonic source (`time.perf_counter_ns()`, `CLOCK_MONOTONIC_RAW`). Convert once to
  milliseconds at the boundary; do not mix units.
- If any two boundaries are stamped on different hosts, either bring them onto one synchronized
  domain or pass `clock_synchronized=False` and treat the result as evidence about the clock,
  not about the control.

## 3. Budget each stage separately, from measured capacity

A single end-to-end number tells you that you failed, not where. Budget ingestion, evaluation,
transmission, acknowledgement, retries, and fail-safe actuation independently, then check that
the sum is still inside the end-to-end budget with headroom.

Derive each figure from measurement under load — RTS 6 Article 10's stress volume (2× the peak
message rate of the previous six months) is a reasonable floor for the test, not a ceiling.
A budget validated at rest is not validated.

Sanity check the budget against the clock: if the budget is within an order of magnitude of the
combined permitted clock divergence (RTS 25: 100 µs for HFT, 1 ms otherwise, doubled across two
clocks), you cannot certify it across hosts.

## 4. Record and read the verdict

```python
from risk_latency_budgeter import (
    LatencyEndState,
    MeasurementStatus,
    RiskControlLatencyBudgeter,
)

budgeter = RiskControlLatencyBudgeter(
    default_sla_budget_ms=250.0,               # calibrated, not inherited
    default_end_state=LatencyEndState.ACKNOWLEDGEMENT,
)

trace = budgeter.record_trace(
    "kill_switch",
    t_event_ms=t_event,
    t_start_ms=t_start,
    t_end_ms=t_end,
    t_order_sent_ms=t_sent,
    t_ack_ms=t_ack,                            # None => UNCERTAIN, never PASS
    clock_synchronized=clock_health.is_synchronized,
)
if trace.status is not MeasurementStatus.PASS:
    escalate(trace)                            # includes UNCERTAIN
```

Decision points:

- `PASS` — measured, trusted, within budget. Equality passes.
- `BREACH` — invoke the approved fail-safe action, then verify broker/exchange state. Do not
  treat the alert as the remediation.
- `UNCERTAIN` — triage in this order:
  1. Is `audited_latency_ms` `None`? The required end state was never observed. For a cancel
     path this means the exposure may still be live. Reconcile against the venue before
     anything else.
  2. Is `budget_exceeded` `True`? The measurement is untrusted but the overrun is far too large
     to be clock skew. Treat it as a probable breach and investigate the pipeline.
  3. Otherwise, investigate the clock: skew, a clock-source change, a delayed producer, replay.

## 5. Investigate a breach by stage

`primary_bottleneck` names the largest stage *inside the audited window*.

| Stage | Look at |
|---|---|
| `INGESTION` | Queue depth and age, consumer lag, thread starvation, GC pauses, feed handler backlog, whether the event thread is shared with something slower. |
| `EVALUATION` | Synchronous I/O inside the check (DB writes, HTTP position lookups, audit logging), lock contention, recomputation that could be incremental, cold caches. |
| `TRANSMISSION` | Serialization, gateway queueing, session-level rate limiting, TCP retransmits, NIC and network path. |
| `ACKNOWLEDGEMENT` | Venue-side throttling, message-rate limits, order-to-trade ratio penalties, retries, session recovery, matching-engine load. |

Preserve the raw traces for the incident window; a percentile is not evidence, the underlying
observations are.

## 6. Report

Segment by control, venue, account, strategy, session, and deployment version. Always publish
`measured_traces` alongside `total_traces` and `uncertain_count` — a clean P99 over 12 measured
traces out of 4,000 is a broken pipeline, not a fast one. Check `p99_resolvable` before quoting
the P99 at all.

An empty audit is a finding. `summarize_audit()` returns `is_risk_pipeline_healthy=False` when
there are no traces precisely so that a stopped instrumentation thread cannot render as green.

## 7. Fault-test outside production

Cover, at minimum: stalled and backed-up queues; clock skew and a clock-source change mid-run;
a slow or unavailable trace store; stale market data; network loss between the strategy host and
the gateway; broker throttling and rate-limit rejection; acknowledgement never arriving;
acknowledgement arriving after the fail-safe already fired; and the fail-safe actuator itself
failing. Each should produce the correct status (`BREACH` or `UNCERTAIN`) and the correct
escalation — never a `PASS`.
