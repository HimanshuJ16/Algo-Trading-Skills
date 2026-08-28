# Pre-Flight Checklist — Runbook Automation for Common Incident Types

Sign off before the engine is allowed to act on a live alert.

## Wiring

- [ ] Is a handler registered for **every** action reachable from a registered
      playbook — i.e. does `engine.unhandled_actions()` return `()`?
- [ ] Does the deployment pipeline **fail the build** when it does not?
- [ ] Does every handler enforce its own transport-level timeout, rather than
      relying on `step_timeout_seconds` to bound it?
- [ ] Does each handler signal failure by raising or returning `False`, and
      never by returning a truthy value on a partial failure?

## Playbooks

- [ ] Is a playbook registered for every `incident_type` your alert routing can
      emit? (An unmapped type escalates with zero steps — verify that is the
      intended outcome for any you deliberately left out.)
- [ ] Is `halt_on_failure` **False** on every step that precedes
      `TRIGGER_KILL_SWITCH`, so a failed cancel cannot cost you the shutdown?
- [ ] Is `terminal_on_success` set on each cheap reversible fix
      (`RECONNECT_SOCKET`, `THROTTLE_ORDER_RATE`) so a recovered session is not
      failed over unnecessarily?
- [ ] Has each playbook been reviewed and approved by whoever owns the risk
      controls it touches, and recorded as the RTS 6 Art. 14(2)(e) usage policy
      for the kill functionality?

## Alert contract

- [ ] Is `incident_id` stable **per incident**, not per delivery (Alertmanager
      `fingerprint`, PagerDuty `dedup_key`, or your own deterministic hash)?
- [ ] Do all alert timestamps carry an explicit UTC offset?
- [ ] Is `RunbookInputError` caught at the ingestion boundary and routed to a
      human page — never to a fallback playbook?
- [ ] Is `force_reexecute` reachable **only** from an authenticated operator
      action, never from the alert webhook?

## Dry run

- [ ] Has a dry run been executed for every incident type, with production
      wiring, returning `DRY_RUN_COMPLETE` and no escalation reasons?
- [ ] Is it understood that a dry run against production is **not** the
      separated test environment RTS 6 Art. 7 requires?
- [ ] Is this dry run part of the annual business-continuity test required by
      RTS 6 Art. 14(4)?

## Latency budget

- [ ] Is `step_timeout_seconds` set from your own measured handler latencies and
      your broker's documented API timeouts — and is the reasoning written down?
- [ ] Is it understood that this bounds the engine's **wait**, not the handler,
      and that a `TIMED_OUT` action may still have taken effect?
- [ ] Is a `TIMED_OUT` cancel reconciled against broker state before any retry,
      rather than blindly resubmitted?

## Audit

- [ ] Is every `IncidentRunbookReport` persisted durably as it is produced,
      rather than left in `get_audit_history()` (DORA Art. 17(2))?
- [ ] Is `max_audit_history` set high enough that no incident is trimmed before
      it has been persisted?
- [ ] Does downstream automation branch on `requires_human_escalation` rather
      than on the `status` string?
- [ ] Is it understood that `RESOLVED` means every step succeeded — not that the
      underlying fault is gone — and is resumption of trading gated on an
      independent check?
