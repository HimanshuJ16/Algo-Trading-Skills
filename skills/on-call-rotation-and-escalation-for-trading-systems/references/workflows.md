# Workflows for On-Call Rotation and Escalation for Trading Systems

All timestamps below are **UTC epoch seconds**. The engine rejects an evaluation
or acknowledgement time that precedes the incident's creation time by more than
`max_clock_skew_seconds` (default 2.0), because that is the visible symptom of a
local-time timestamp — and a backwards clock silently pins elapsed time at zero,
leaving the incident at PRIMARY forever.

## 1. Roster and shift registration

Register every engineer with `engineer_id`, `name`, `tier`, `phone`, `email`,
and optionally `shift_start_utc` / `shift_end_utc`.

- `engineer_id` must be unique across the whole roster; the engine raises on a
  duplicate, because acknowledgements are attributed by id.
- A roster entry with **no** shift bounds is *always on call* for its tier and
  acts as the fallback when no scheduled shift covers the moment of a page.
- Shifts are the half-open interval `[start, end)`. At the handover instant the
  incoming engineer owns the page and the outgoing one does not — a closed
  interval either double-pages or leaves both assuming the other has it.
- Construction logs a warning for every unstaffed tier. That is not fatal — a
  firm may legitimately run without an executive rung — but every escalation to
  that tier will be reported as undeliverable.

**Resolution order for a tier at time `t`:** a scheduled engineer whose shift
covers `t` (most recent `shift_start_utc` wins among overlaps, `engineer_id` as
tie-break) → an always-on engineer → nobody, reported as a rota hole.

## 2. Incident creation

`create_incident` returns the registered incident and is **idempotent on
`incident_id`**. A duplicate registration returns the incident already held and
changes nothing. This matters because alert webhooks are redelivered: the
previous behaviour overwrote the stored incident, resetting `created_at_utc` and
discarding an acknowledgement a responder had already given.

Severity is normalised (case and whitespace) and validated on registration:

- Recognised (`SEV_1`, `SEV_2`, `SEV_3`) → used as given.
- Unrecognised, `unknown_severity_policy="ESCALATE"` (default) → treated as
  `SEV_1`, logged at ERROR, with `reported_severity` and `severity_was_coerced`
  preserved on the report for the audit trail.
- Unrecognised, `unknown_severity_policy="REJECT"` → `ValueError`.

**Decision point — never guess a severity downward.** An unmapped label is a
mapping bug in the alert source. Guessing upward costs one unnecessary page;
guessing downward routes a broker disconnect to a chat channel.

## 3. Escalation evaluation

Call `evaluate_escalation(incident_id, now_utc)` on each polling tick. It:

1. Computes elapsed minutes since creation.
2. Selects the highest ladder rung whose cumulative threshold `<=` elapsed.
   Thresholds are **inclusive** — at exactly `t=3.0` a SEV-1 is already at
   SECONDARY.
3. Resolves the responder for that tier at `now_utc` (see §1).
4. Sets `is_sla_breached` from the **response SLA**, which is configured
   separately from the escalation thresholds.

**Decision point — an escalation is not an SLA breach.** A SEV-2 escalates to
SECONDARY at 10 minutes but its SLA is 15. Flagging a breach at the escalation
threshold puts a breach in the audit trail that did not occur.

**Decision point — page on the transition, not on the tick.**
`is_new_escalation` is True only the first time a tier is reached. A 5-second
polling loop that pages on every report dials the primary engineer 36 times
before the first escalation is even due. Pass `record_notification=False` for a
what-if evaluation that leaves the deduplication record untouched.

**Decision point — check `is_notification_deliverable` before claiming the page
went out.** False means no engineer was resolved for the tier (unstaffed tier or
rota hole) or the resolved engineer has no contact address for the required
channel. `delivery_warnings` says which. A report with this False describes a
page that reaches nobody, and it is logged at ERROR.

## 4. Acknowledgement

`acknowledge_incident(incident_id, engineer_id, ack_time_utc)` returns False for
an unknown or already-resolved incident.

- The **first** acknowledgement is the one measured against the SLA. A later
  re-acknowledgement updates only `last_ack_at_utc`; otherwise a re-ack at t=41
  would retroactively overwrite a genuine response at t=2.
- Acknowledging after the SLA has passed yields status `ACKNOWLEDGED_LATE` with
  `is_sla_breached=True`. **Acknowledging late does not un-breach an SLA.** The
  previous version cleared the breach flag on any acknowledgement, erasing real
  breaches from the record.
- An `engineer_id` not on the roster is recorded with a warning rather than
  refused — a manager or contractor may legitimately acknowledge — but it cannot
  be attributed to a tier.

## 5. Acknowledgement timeout and re-trigger

An acknowledged but unresolved incident re-triggers after `ack_timeout_mins`
(default 30, enabled). Status becomes `RE_TRIGGERED_ACK_TIMEOUT` and paging
resumes at whichever tier the *total* elapsed time now warrants — for a SEV-1
that is the executive rung.

`retrigger_count` increments once per lapsed acknowledgement, not once per poll,
and stays on the report after a re-acknowledgement. Without it, an incident
acknowledged in 60 seconds and then abandoned for half an hour is
indistinguishable in the audit record from one that was handled.

Setting `ack_timeout_mins=None` restores the "an acknowledgement silences the
pager forever" behaviour. Do not do that for SEV-1 without a compensating
control.

## 6. Resolution and retention

`resolve_incident(incident_id, resolved_at_utc)` stops all escalation.
Resolution does not require a prior acknowledgement — an incident can self-clear
— but resolving an unacknowledged incident that already breached its SLA leaves
the breach on the record.

`purge_resolved_incidents(before_utc)` drops old resolved incidents so a
long-lived pager process does not accumulate every incident it has ever seen.
These objects are the SLA audit record: purge only once a durable copy exists
elsewhere.

## 7. Regulatory clocks run in parallel

The pager ladder is not the reporting clock. Under the DORA RTS a major ICT
incident requires initial notification within 4 hours of classification and no
later than 24 hours from awareness; under Reg SCI an SCI entity notifies the
Commission immediately. Neither clock is started or stopped by an
acknowledgement. Wire classification and regulatory notification as a separate
path — see `references/standards.md` for scope and applicability, including why
Reg SCI most likely does *not* apply to your firm.
