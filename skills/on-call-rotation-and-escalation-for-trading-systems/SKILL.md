---
name: on-call-rotation-and-escalation-for-trading-systems
description: >-
  Shift-aware on-call rotation resolver and incident escalation manager for live trading systems: severity-driven escalation ladders (Primary, Secondary, Executive), acknowledgement-SLA auditing that does not erase late responses, acknowledgement-timeout re-triggers, and explicit undeliverable-page detection.
domain: SRE & System Reliability
subdomain: On-Call Rotations & Automated Escalation Policies
tags: ["on-call", "sre", "escalation-policy", "incident-management", "sev1-sla", "pagerduty", "trading-reliability"]
brokers_frameworks: ["PagerDuty / Opsgenie escalation semantics (reference only, no integration)", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing on-call rotations and incident escalation for live
algorithmic trading systems. An unacknowledged kill-switch trigger, broker
disconnect or feed stall is a position running unsupervised, so the escalation
ladder is a capital-protection control as much as an operational one — and its
output is an audit artifact: under MiFID II RTS 6 Art. 16(4) an EU algorithmic
trading firm must ensure staff in charge of real-time monitoring are reachable
"at all times", with out-of-hours contact procedures that are "periodically
tested".

The engine answers two questions at 03:00: **who is on call for this tier right
now**, and **was this incident acknowledged inside its response SLA**. It
resolves responders from a shift schedule, escalates
`PRIMARY` $\rightarrow$ `SECONDARY` $\rightarrow$ `EXECUTIVE` on cumulative
elapsed time, and refuses to report a page as delivered when no one is on the
other end.

## When NOT to Use

- **As a notifier.** This engine sends nothing. There is no PagerDuty, Opsgenie,
  SMS or telephony integration; `notification_channel` is an instruction to your
  notifier, not evidence of delivery. Wire the actual sending yourself, and
  treat `is_notification_deliverable=False` as a hard stop.
- **As a risk control.** It does not halt trading, cancel orders or trip a kill
  switch. For automated mitigation see
  `execution-algorithm-kill-switch-integration` and
  `kill-switch-and-drawdown-circuit-breakers`; for risk-metric-driven escalation
  see `risk-limit-breach-escalation-matrix`.
- **As the regulatory reporting clock.** DORA's 4-hour/24-hour major-incident
  deadlines and Reg SCI's immediate notification run from awareness and
  classification, not from acknowledgement, and an acknowledgement neither
  starts nor stops them. See `references/standards.md`.
- **With a PagerDuty escalation policy transposed verbatim.** PagerDuty's
  "escalates after N min" is per-rule, measured from when *that level* was
  notified. This engine's thresholds are cumulative from incident creation.
  3 min then 5 min means an executive page at t=5 here, at t=8 there.
- **With local-time timestamps.** Every timestamp is a UTC epoch second.

## Prerequisites

- A roster of engineers mapped to tiers (`PRIMARY`, `SECONDARY`, `EXECUTIVE`)
  with unique `engineer_id` values and a contact address for each severity's
  channel (`phone` for `PHONE_CALL`/`SMS`).
- Optional `shift_start_utc` / `shift_end_utc` per engineer. An entry with
  neither is always on call for its tier and serves as the rota fallback.
- An incident event (`incident_id`, `severity`, `title`, `description`,
  `created_at_utc`) with `severity` already mapped to `SEV_1` / `SEV_2` /
  `SEV_3` by the alert source.
- Response SLAs and escalation thresholds you are willing to defend. The
  defaults are engineering conventions from Google SRE practice, **not**
  regulatory deadlines — see `references/standards.md`.

## Workflow

1. **Roster & Shift Registration**:
   - Register engineers into tiers, optionally with shift windows as the
     half-open interval `[shift_start_utc, shift_end_utc)`.
   - **Decision point — half-open shifts, and a fallback for the gaps.** At the
     handover instant exactly one engineer is on call; a closed interval either
     double-pages or leaves each assuming the other owns it. If no scheduled
     shift covers the moment of a page and there is no always-on entry, that is
     a hole in the rota and the engine reports the page as undeliverable rather
     than picking someone.

2. **Incident Creation & SLA Assignment**:
   - `create_incident` is idempotent on `incident_id`: a redelivered alert
     webhook returns the incident already held and changes nothing.
   - **Decision point — never guess a severity downward.** An unrecognised label
     is treated as `SEV_1`, logged at ERROR, and flagged with
     `severity_was_coerced` (or rejected outright under
     `unknown_severity_policy="REJECT"`). Guessing upward costs one unnecessary
     page; guessing downward routes a broker disconnect to a chat channel.
   - Response SLAs: `SEV_1` 5 min, `SEV_2` 15 min, `SEV_3` 60 min — configured
     separately from the escalation thresholds that enforce them.

3. **Escalation Evaluation**:
   - Elapsed $\Delta t = T_{\text{now}} - T_{\text{created}}$ selects the highest
     ladder rung whose cumulative threshold is $\le \Delta t$ (thresholds are
     inclusive): `SEV_1` PRIMARY $\rightarrow$ SECONDARY at 3 min
     $\rightarrow$ EXECUTIVE at 5 min; `SEV_2` SECONDARY at 10 $\rightarrow$
     EXECUTIVE at 30; `SEV_3` SECONDARY at 30, no executive rung.
   - **Decision point — an escalation is not an SLA breach.** A SEV-2 escalates
     at 10 minutes against a 15-minute SLA. Flagging a breach at the escalation
     threshold records a breach that did not happen.
   - **Decision point — page on the transition, not on the tick.**
     `is_new_escalation` is True only the first time a tier is reached; a
     5-second poll that pages on every report dials the primary 36 times before
     the first escalation is due. Use `record_notification=False` for a what-if
     evaluation.
   - **Decision point — verify the page can land.**
     `is_notification_deliverable=False` means no engineer resolved for the tier,
     or no contact address for the channel. Do not report the incident as paged.

4. **Acknowledgement & Re-trigger**:
   - The **first** acknowledgement is the one measured against the SLA; a later
     re-acknowledgement updates only the re-trigger clock.
   - **Decision point — acknowledging late does not un-breach an SLA.** A
     response 60 minutes into a 5-minute SEV-1 SLA is `ACKNOWLEDGED_LATE` with
     `is_sla_breached=True`, permanently.
   - **Decision point — an acknowledgement is not a resolution.** An
     acknowledged, unresolved incident re-triggers after `ack_timeout_mins`
     (default 30) at whichever tier total elapsed time now warrants, and
     `retrigger_count` keeps the abandonment visible after a re-acknowledgement.

5. **Resolution & Audit**: `resolve_incident` stops escalation; an SLA breached
   before resolution stays breached on the record. `purge_resolved_incidents`
   bounds memory in a long-lived process — only once a durable copy exists.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Paging a tier nobody is registered for**: an escalation to an empty tier, or
  into a gap between shifts, must fail loudly. Fabricating a placeholder "duty
  engineer" produces a report that looks entirely normal while the SEV-1 page
  reaches nobody — the worst failure this skill has, because it is invisible.
- **Letting an acknowledgement erase the SLA record**: clearing the breach flag
  whenever someone acknowledges means a response 55 minutes late is filed as an
  SLA met. That is a fabricated compliance record, not a lenient one.
- **Treating an unmapped severity as the least severe**: a label the mapping
  does not recognise (`CRITICAL`, `P1`, `URGENT`) falling through to the SEV-3
  branch routes the most severe incident class to a chat message.
- **Acknowledge and go back to sleep**: without an acknowledgement timeout, one
  tap at 02:00 silences the pager permanently while the fault runs on.
- **Trusting a backwards clock**: clamping negative elapsed time to zero means an
  incident stamped in local time never escalates at all. Reject it instead.
- **Re-paging on every poll**: escalation state must be deduplicated by tier, or
  the ladder becomes its own alert-fatigue source.
- **Duplicate alert webhooks resetting incident state**: re-registering an
  incident that is already acknowledged must not un-acknowledge it.
- **Phone-paging SEV-3 out of hours**: informational warnings belong on a chat
  channel. A rotation trained to ignore its pager will ignore the SEV-1 too.
- **Assuming Reg SCI applies**: its immediate-notification requirement binds SCI
  entities — exchanges, registered clearing agencies, SCI ATSs, plan processors,
  competing consolidators — not ordinary broker-dealers or prop trading firms.

## Verification

- Instantiate `OnCallEscalationManagerEngine` with a Primary, Secondary and
  Executive engineer. Submit a SEV-1 incident. At $t=0 \implies$ `PRIMARY`,
  status `ACTIVE_PRIMARY`. At $t=3.5$ min $\implies$ `SECONDARY`, status
  `ESCALATED_WARNING`, `is_sla_breached` False. At $t=5.5$ min $\implies$
  `EXECUTIVE`, status `SLA_BREACH`. Acknowledge at $t=60$ min $\implies$ status
  `ACKNOWLEDGED_LATE` with `is_sla_breached` **True** and
  `ack_latency_minutes` $= 60.0$.
- Drop the executive engineer from the roster and re-run: at $t=5.5$ min the
  report must carry `is_notification_deliverable=False` and a
  `delivery_warnings` entry, not a placeholder responder.
- Create an incident with severity `"CRITICAL"`: it must be handled as `SEV_1`
  on `PHONE_CALL` with `severity_was_coerced=True`, never as `SEV_3` on Slack.
- Run `python -m unittest discover -s skills/on-call-rotation-and-escalation-for-trading-systems/scripts`
  (51 tests).

## Related Skills

- `runbook-automation-for-common-incident-types`
- `risk-limit-breach-escalation-matrix`
- `execution-algorithm-kill-switch-integration`
- `systemd-supervision-for-trading-bots`
- `structured-logging-for-post-incident-forensics`
- `post-mortem-culture-and-blameless-review-process`
