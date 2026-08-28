# Pre-Flight Checklist — On-Call Rotation & Escalation

## Roster and rotation coverage
- [ ] Every tier in use (`PRIMARY`, `SECONDARY`, `EXECUTIVE`) has at least one
      registered engineer, or the tier's absence is a deliberate, recorded
      decision.
- [ ] Every `engineer_id` is unique across the roster.
- [ ] Shift windows tile the clock with **no gaps** — or an always-on fallback
      entry exists for every tier. A rota hole at 03:00 is an undeliverable
      SEV-1 page.
- [ ] Shift boundaries are half-open `[start, end)`; the handover instant pages
      exactly one engineer.
- [ ] Every engineer has a `phone` value if their tier can receive `PHONE_CALL`
      or `SMS` pages.
- [ ] Contact channels have been **tested out of hours**, not just configured
      (MiFID II RTS 6 Art. 16(4) requires periodic testing).

## Timestamps and clocks
- [ ] All timestamps supplied are UTC epoch seconds, never naive local time.
- [ ] Hosts producing incident timestamps are NTP-synchronised; the configured
      `max_clock_skew_seconds` matches observed inter-host jitter.

## Severity mapping
- [ ] The alert source maps every label it can emit to `SEV_1` / `SEV_2` /
      `SEV_3`.
- [ ] `unknown_severity_policy` is set deliberately (`ESCALATE` to fail safe,
      `REJECT` to fail fast) and ERROR logs for coerced severities are monitored
      — each one is a mapping bug.
- [ ] SEV-3 is not routed to a phone channel out of hours.

## Policy configuration
- [ ] Escalation thresholds are strictly increasing per severity (the engine
      raises otherwise; a non-increasing ladder makes a tier unreachable).
- [ ] Thresholds were entered as **cumulative minutes from incident creation**,
      not transposed verbatim from PagerDuty per-rule delays.
- [ ] Response SLAs are configured separately from escalation thresholds, and
      each threshold has a recorded justification.
- [ ] `ack_timeout_mins` is enabled for SEV-1, or its absence has a documented
      compensating control.

## Integration correctness
- [ ] The caller pages on `is_new_escalation`, not on every polling tick.
- [ ] The caller checks `is_notification_deliverable` and treats `False` as a
      failed page requiring out-of-band action.
- [ ] `create_incident` idempotency is relied on for redelivered webhooks — no
      external de-duplication resets incident state.
- [ ] `resolve_incident` is actually called when incidents close; otherwise
      every incident re-triggers forever.
- [ ] `purge_resolved_incidents` runs only after the incident record is durably
      persisted elsewhere.

## Audit and regulatory
- [ ] `ACKNOWLEDGED_LATE` and `retrigger_count` are surfaced in incident review;
      a late acknowledgement is never filed as an SLA met.
- [ ] Regulatory reporting (DORA major-incident classification, or Reg SCI if
      and only if the firm is an SCI entity) is wired as a **separate path** from
      the pager ladder — those clocks run from awareness and classification.
- [ ] The firm's actual regulatory scope has been confirmed rather than assumed;
      Reg SCI does not apply to ordinary broker-dealers or prop trading firms.
- [ ] On-call workload is within a defensible ceiling (Google SRE's reference
      figure is 2 incidents per 12-hour shift).
