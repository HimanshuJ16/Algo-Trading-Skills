---
name: runbook-automation-for-common-incident-types
description: >-
  Use when monitoring already classifies a trading incident and the first minute of
  response should be a pre-approved sequence rather than improvisation. It executes a
  runbook; it does not decide whether the alert is real.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: runbook-automation, incident-response, trading-sre, kill-switch, feed-disconnect, venue-failover, mifid-ii-rts-6, dora-art-17
  brokers_frameworks: "Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Arts. 7, 12, 14, 16; Regulation (EU) 2022/2554 (DORA), Art. 17; SEC Rule 17 CFR 240.15c3-5 (Market Access Rule); SEC Admin. Proc. 34-70694 (Knight Capital Americas LLC, 2013); Beyer et al., Site Reliability Engineering (O'Reilly, 2016); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when your monitoring stack can already classify a trading
incident, and you want the first minute of the response to be a pre-approved
sequence instead of whatever the person holding the pager remembers. The case
for it is measurable: Google's SRE Book reports that "thinking through and
recording the best practices ahead of time in a 'playbook' produces roughly a 3x
improvement in MTTR as compared to the strategy of 'winging it'".

For a trading system, MTTR is denominated in money and in regulatory exposure.
The SEC's Knight Capital order records both halves of the failure this skill
addresses: Knight "did not have supervisory procedures to guide its relevant
personnel when significant issues developed", and then, improvising, "uninstalled
the new RLP code from the seven servers where it had been deployed correctly.
This action worsened the problem." Forty-five minutes and roughly $460 million.
A runbook is the pre-approved answer; the engine is what makes running it
deterministic and auditable.

Use it in three places: as the automated first responder wired to your alerting
webhook, as a dry-run pre-flight that proves every remediation action is actually
wired before you need it, and as the record you hand to the post-mortem.

## When NOT to Use

- **As a detector.** The engine does not decide whether an incident is real. It
  consumes an alert your monitoring stack has already classified. Feed it a
  false positive and it will faithfully cancel your orders.
- **As the remediation itself.** Every `RemediationAction` is inert until you
  bind a handler with `register_handler`. This engine sequences, times, and
  records; it does not know how to cancel an order or trip a kill switch. The
  kill switch is `execution-algorithm-kill-switch-integration`; venue selection
  is `smart-order-router-failover-on-venue-outage`; paging a human is
  `on-call-rotation-and-escalation-for-trading-systems`.
- **As your pre-trade risk control.** SEC Rule 15c3-5(c)(1) controls are
  *pre-trade* and must be "under the direct and exclusive control of the broker
  or dealer" (§240.15c3-5(d)). This is post-hoc incident response and satisfies
  none of that. Do not substitute a remediation runbook for a pre-trade check.
- **As your retained incident record.** `get_audit_history()` is in-process
  memory, capped and lost on restart. DORA Art. 17(2) requires financial
  entities to "record all ICT-related incidents". Persist every report before
  you rely on it.
- **As a substitute for a separated test environment.** RTS 6 Art. 7 requires
  pre-deployment testing "in an environment that is separated from its
  production environment". Dry-run mode is a wiring check on top of that, not
  in place of it.
- **For an incident class you have not written a playbook for.** The engine
  escalates instead of guessing. That is deliberate — see Pitfalls.

## Prerequisites

- **A classified alert**: `incident_id`, `incident_type` (one of
  `FEED_DISCONNECT`, `LATENCY_SPIKE`, `BROKER_API_OUTAGE`, `DRAWDOWN_BREACH`,
  `ORDER_THROTTLE`), `severity`, `source_service`, `metric_value`,
  `threshold_value`, `timestamp_iso`. The timestamp must carry an explicit UTC
  offset; strings and a trailing `Z` are accepted and normalised.
- **A stable `incident_id` per incident, not per delivery.** Deduplication is
  keyed on it. If your alert transport mints a fresh id on every retry, the
  engine cannot protect you from a repeat mass cancel.
- **A handler for every action your playbooks reach.** Verify with
  `unhandled_actions()` in your deployment gate, not at 03:00.
- **A documented usage policy for the kill functionality.** RTS 6 Art. 14(2)(e)
  requires business continuity arrangements to include a "usage policy
  regarding the functionality referred to in Article 12" — that is, when the
  kill switch may fire. Automating it does not remove the requirement to have
  written down when it should.
- Python 3.7+. Standard library only — no dependencies.

## Workflow

1. **Wire every action before you trust the engine, and prove it with a dry run.**
   - `register_handler(action, callable)` binds the code that actually performs
     each action. An unbound action reports `NO_HANDLER_REGISTERED` and
     escalates; it is **never** reported as `SUCCESS`.
   - **Decision point — a dry run checks wiring, not just sequencing.** In dry
     run, a registered action reports `SKIPPED_DRY_RUN` and an unregistered one
     still reports `NO_HANDLER_REGISTERED`. A dry run that reported every step
     as fine regardless would rehearse a happy path and prove nothing.
   - Dry runs return `IncidentStatus.DRY_RUN_COMPLETE`, never `RESOLVED`. A
     simulation resolved nothing, and downstream automation that closes an
     incident on `RESOLVED` must not be handed a rehearsal.

2. **Accept the alert at the boundary, or reject it — never coerce it into a
   playbook.**
   - `IncidentAlert` validates on construction: empty ids, non-finite
     metrics (a `NaN` threshold compares False against everything), naive
     timestamps, and unrecognised `incident_type` labels all raise
     `RunbookInputError`.
   - **Decision point — an unrecognised incident type escalates, it does not
     default.** The previous version defaulted any unmapped type to
     `CANCEL_OPEN_ORDERS`. Executing a market-affecting action on a diagnosis
     you do not have is exactly what turned Knight's deployment error into a
     firm-ending one. Catch `RunbookInputError`, page a human, execute nothing.
   - An unrecognised **severity** label is recorded as `CRITICAL` and flagged
     with `severity_was_coerced`. Severity is audit metadata here — the playbook
     is chosen by `incident_type` — but guessing severity downward is how a
     `P1` label ends up filed as informational.

3. **Look up the pre-approved playbook. There is no fallback.**
   - The default table maps each incident type to an ordered sequence.
     DORA Art. 17(3)(c) requires a financial entity to "assign roles and
     responsibilities that need to be activated for different ICT-related
     incident types and scenarios"; this table is that assignment for the
     automated portion.
   - No playbook registered $\implies$ zero steps, `ESCALATED`,
     `requires_human_escalation=True`. Override or extend with
     `register_playbook`; an empty playbook is rejected outright, because a
     playbook that does nothing would report `RESOLVED` while the incident runs.

4. **Execute in order, with the two branch rules that make a playbook a playbook.**
   - **`terminal_on_success` — try the cheap fix first.** `FEED_DISCONNECT` is
     `RECONNECT_SOCKET` *then* `FAILOVER_VENUE`, and a successful reconnect
     stops there. Failing over after the socket already came back is a
     gratuitous mid-session venue change with its own queue-position and
     entitlement cost.
   - **`halt_on_failure` defaults to False — a failed step must not cost you the
     kill switch.** On `DRAWDOWN_BREACH` the sequence is cancel-then-kill. If
     the cancel fails and the runbook halts, the algorithm is still live and
     the limit is still breached. RTS 6 Art. 12(1) requires the ability "to
     cancel immediately, as an emergency measure, any or all of its unexecuted
     orders", and Art. 14(3) requires that the system "can be shut down …
     without creating disorderly trading conditions". Both say: attempt the
     protective step anyway. Set `halt_on_failure=True` only where continuing is
     itself unsafe.
   - **Decision point — the cancel in a `BROKER_API_OUTAGE` playbook is expected
     to fail sometimes.** It is routed through the broker that is already down.
     That is the argument for the default, not against the step. Exchange-side
     cancel-on-disconnect is the real backstop for orders resting at a venue you
     can no longer reach.

5. **Bound every step, and treat a timeout as an unknown, not a failure.**
   - `step_timeout_seconds` (default 30 s) caps how long the engine waits for
     one handler. It bounds the **wait**, not the handler: Python cannot cancel
     a running thread, so a handler blocked in a socket read is still running
     after `TIMED_OUT` is recorded. Handlers must set their own transport
     timeouts.
   - **Decision point — a `TIMED_OUT` cancel may still have reached the broker.**
     Treat the action's effect as unknown and reconcile against broker state
     before any retry. See `order-placement-idempotency`.

6. **Deduplicate the redelivery, because your alert transport will redeliver.**
   - `execute_runbook` is idempotent on `incident_id`: a second delivery returns
     the stored report with `duplicate_delivery_count` incremented and executes
     nothing. Alertmanager repeat intervals and webhook retries are normal
     operation; re-running a `DRAWDOWN_BREACH` playbook means a second mass
     cancel and a second kill-switch trip.
   - `force_reexecute=True` exists for an operator-authorised retry after a
     partial failure. Record who authorised it. Never wire it to the webhook.

7. **Branch on `requires_human_escalation`, then persist the report.**
   - `requires_human_escalation` is exactly `bool(escalation_reasons)` and
     covers every failure, timeout, unwired action and missing playbook. Branch
     on it, not on the status string.
   - `RESOLVED` means every attempted step succeeded. It does not mean the
     underlying fault is gone — verify independently before resuming trading.
   - Write the report to durable storage. The in-memory history is capped by
     `max_audit_history` and drops oldest-first; past that bound an old
     `incident_id` also loses its deduplication.

> Full procedure and wiring examples: see `references/workflows.md`.
> Obligation-by-obligation sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A runbook engine that simulates its own success.** The 1.0.0 engine
  hard-coded `step_status = "SUCCESS"` for every step. Wired to a real kill
  switch it returned `RESOLVED` while the position ran on untouched. An
  unbound action must escalate, never succeed — a remediation report that
  claims an action which never happened is worse than no report at all.
- **Reading a dry run as a resolution.** A dry run resolved nothing. If it
  returns `RESOLVED`, some downstream automation will eventually close a live
  incident on the strength of a rehearsal.
- **Defaulting an unmapped incident type to "cancel everything".** The safe
  default for a diagnosis you do not have is to do nothing and page someone.
  Knight's remediation attempt, executed on a wrong diagnosis, spread the
  defect from one server to eight.
- **Halting the playbook when the cancel fails.** This is the pitfall that costs
  the most money, and it reads as prudence. On a drawdown breach the kill switch
  is the step that matters; abandoning it because the cancel before it failed
  leaves the algorithm trading through its limit.
- **Failing over after a successful reconnect.** A playbook that always runs
  every step is a list, not a playbook. Mark the cheap fix `terminal_on_success`.
- **Re-running the playbook on every alert redelivery.** Monitoring transports
  redeliver by design. Without idempotency keyed on a stable `incident_id`, a
  flapping feed alert fires the kill switch once per delivery.
- **Retrying a timed-out cancel as though it definitely failed.** The request may
  have reached the broker before the client stopped waiting. Reconcile broker
  state first; do not blindly resubmit a non-idempotent action.
- **Quoting a millisecond remediation SLA you cannot source.** No regulator
  prescribes a remediation-execution deadline. RTS 6 Art. 16 prescribes five
  seconds for *alert generation*, which is a different clock on a different
  event. Budget your own targets and be able to defend them — see
  `references/standards.md`.
- **Treating the in-memory audit history as the record.** It is a debugging
  convenience. DORA Art. 17(2) wants the incident recorded; a list that dies
  with the process is not that.
- **Assuming a `NaN` metric is harmless because nothing divides by it.** It
  compares False against every threshold, silently disabling any downstream
  check, and prints as `nan` in the incident record a regulator may read.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/runbook-automation-for-common-incident-types/scripts`
  — 62 tests, all must pass.
- Construct an engine with **no** handlers and execute a `DRAWDOWN_BREACH`
  alert. Every step must be `NO_HANDLER_REGISTERED` and the status
  `ESCALATED`. If you see `RESOLVED`, you are on the old engine.
- Wire all five actions, set the `CANCEL_OPEN_ORDERS` handler to raise, and
  re-run: step 1 is `FAILED`, step 2 `TRIGGER_KILL_SWITCH` is `SUCCESS`, and the
  kill-switch handler was called exactly once. The kill switch firing after a
  failed cancel is the single most important behaviour here.
- Execute a `FEED_DISCONNECT` with a succeeding reconnect handler and confirm
  `FAILOVER_VENUE` is `SKIPPED_ALREADY_REMEDIATED` and its handler was never
  called. Make the reconnect fail and confirm the failover runs.
- Deliver the same `incident_id` three times and confirm the kill-switch handler
  was called once and `duplicate_delivery_count` reached 2.
- Run in dry-run mode with one action deliberately unbound: the bound steps are
  `SKIPPED_DRY_RUN`, the unbound one is `NO_HANDLER_REGISTERED`, the status is
  `DRY_RUN_COMPLETE`, and no handler was invoked.
- Set `step_timeout_seconds=0.05` against a handler that blocks: the step is
  `TIMED_OUT`, not `FAILED`, and the following step still runs.
- Feed a naive timestamp, a `NaN` metric, and `incident_type="DISK_FULL"`. Each
  must raise `RunbookInputError` — no report, no steps, no cancel.
- Against your real estate: run `unhandled_actions()` in your deploy pipeline
  and fail the build if it is non-empty. Then run a dry run for all five
  incident types on the schedule you use to satisfy RTS 6 Art. 14(4) annual
  business-continuity testing.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `smart-order-router-failover-on-venue-outage`
- `on-call-rotation-and-escalation-for-trading-systems`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `structured-logging-for-post-incident-forensics`
- `post-mortem-culture-and-blameless-review-process`
- `disaster-recovery-runbook-for-full-region-outage`
