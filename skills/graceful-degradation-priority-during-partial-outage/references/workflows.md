# Workflows — graceful-degradation-priority-during-partial-outage

## 1. Tag the work before the outage, not during it

Every task entering the chokepoint carries exactly one of `P1_CRITICAL`, `P2_HIGH`,
`P3_MEDIUM`, `P4_LOW`. Assign the tier at the point the work is created, where its
purpose is known — a router cannot infer that a database write is a risk-limit
persist rather than a tick log.

- P1 — risk-limit checks, emergency stop / mass-cancel, venue heartbeats.
- P2 — position exits, stop-loss executions, fill reconciliation.
- P3 — new signal entries, child order slices.
- P4 — analytics, historical tick logging, GUI streaming.

A tier that cannot be parsed raises `UnknownTaskPriorityError` and rejects the whole
batch. This is deliberate: the failure mode being prevented is a mis-tagged mass-cancel
being shed as if it were analytics.

## 2. Sample health honestly

Produce `cpu_utilization_pct`, `network_packet_loss_pct`, optionally
`db_connection_latency_ms`, and stamp each sample with `sample_age_seconds`.

- A metric that could not be read is `None`, **never** `0.0`.
- A non-finite value raises. `NaN` compares `False` against every threshold, so an
  unguarded one classifies as `NORMAL_HEALTHY` and disables shedding entirely.
- If the sampler itself is down, feed `SystemHealthMetrics(None, None, None, None)`
  rather than the last known good sample. The engine escalates; a frozen snapshot does
  not.

## 3. Classify, then damp

`determine_system_mode()` classifies one sample and touches no state — safe to call for
dashboards. `process_and_filter_tasks()` applies the latched mode:

- Escalation is immediate and unconditional.
- Recovery steps down **one** level after `recovery_confirmation_samples` consecutive
  healthier samples. Any sample at or above the current severity resets the streak.
- `reset_mode_state()` exists for an operator-confirmed recovery; use it deliberately,
  not to skip the confirmation window.

## 4. Route and dispatch in order

The policy matrix returns `PROCESS` / `DEFER` / `DROP` per task.
`processed_task_ids` is already sorted P1-first with input order preserved inside each
tier, so dispatching in list order dispatches risk work first. If P1 and P4 share a
thread pool, a connection pool, or an event loop, the ordering buys nothing — separate
the resources.

## 5. Handle the shed work

| Disposition | Meaning | Required follow-up |
|---|---|---|
| `PROCESS` | Dispatch now | — |
| `DEFER` | Still has to happen | Re-queue with a freshness check; escalate if P1/P2 |
| `DROP` | Gone, deliberately | Record the count; never retry |

When `manual_intervention_required` is set, P1/P2 work was shed or telemetry was
unreadable. Page a human and invoke the alternative arrangement for outstanding orders
and positions (RTS 6 Art. 14(2)(g)) — a broker phone line, a manual desk, the DR
runbook. The report is the trigger, not the remedy.

## 6. Replay the backlog without re-breaking the system

- Re-validate every deferred task against current price, position and signal state
  before executing. A deferred entry from before the outage is stale alpha.
- Stagger the replay with randomised backoff and a retry budget. Flushing a deferred
  backlog the moment the mode clears is how a recovering system is pushed straight back
  into the overload it just left.
- Deferred P2 exits go first, and only after confirming the position still exists — the
  manual desk may already have flattened it.

## 7. Audit and rehearse

- Log `audit_notes`, `classification_reasons`, and every mode transition (they are
  emitted at `WARNING` with before/after states).
- Reconcile per session: tasks received = processed + deferred + dropped, and every
  deferred P1/P2 task closed out or explicitly written off.
- Exercise the degradation paths on a schedule. Graceful-degradation code runs rarely,
  which is exactly why it is the code most likely to be broken when it is finally
  needed — see `chaos-engineering-for-trading-infrastructure`.
