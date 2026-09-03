---
name: risk-limit-breach-escalation-matrix
description: >-
  Use when a breach needs a proportionate response rather than a binary halt; maps
  breach ratio and duration onto a severity ladder returning the action your enforcement
  layer should take.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-escalation, limit-breach, escalation-matrix, pagerduty, risk-governance, automated-flatten, duration-escalation, audit-trail
  brokers_frameworks: "MiFID II RTS 6 (Art. 12, 15, 16, 17); SEC Rule 15c3-5; PagerDuty / Slack routing semantics (reference only, no integration); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a risk limit has been breached and the response has to be proportionate to *how far past* the limit the metric is and *how long* it has stayed there. A control with only two settings — ignore, or liquidate — either tolerates a 105% drawdown breach that runs for two hours, or force-flattens a book over a 101% blip. This engine turns the breach into a tier and returns the action, the notification channels, and the acknowledgement deadline that tier warrants, plus an audit row explaining why.

That graduation is what MiFID II RTS 6 Art. 17 contemplates for a triggered post-trade control: "appropriate action, which may include adjusting or shutting down the relevant trading algorithm or trading system or an orderly withdrawal from the market" — a ladder, not a switch.

Typical inputs: daily drawdown, gross/net exposure, leverage, VaR, position count, order-to-trade ratio (ceilings), and free margin, cash buffer or collateral coverage (floors).

## When NOT to Use

- **As the enforcement layer.** This engine decides; it does not act. It cancels no orders, flattens no positions and trips no kill switch. `action` is an instruction to *your* enforcement layer — and under SEC Rule 15c3-5(d) that layer must be "under the direct and exclusive control of the broker or dealer". For the enforcement side see `kill-switch-and-drawdown-circuit-breakers` and `strategy-level-kill-switch-vs-portfolio-level-kill-switch`.
- **As the notifier.** There is no PagerDuty, Slack, e-mail or ticketing integration. `notification_channels` is routing intent, not proof of delivery, and `ack_deadline_seconds` is an SLA for your notifier to enforce — the engine runs no timer and tracks no acknowledgement. Roster resolution and ack-SLA auditing belong to `on-call-rotation-and-escalation-for-trading-systems`.
- **As the breach detector.** It evaluates one observation you hand it. It does not poll metrics, does not hold a clock, and cannot measure how long a breach has persisted — `duration_seconds` is the caller's to supply. A caller that always passes `0.0` gets no duration escalation, however long the breach actually runs.
- **As the source of your limits.** The ladder is a response policy, not a calibration. Derive the limits themselves from `risk-limit-calibration-against-historical-drawdowns`; the 1.0/1.2/1.5/2.0 multipliers here are house defaults with no empirical or regulatory basis.
- **As a pre-trade control.** RTS 6 Art. 15 and Rule 15c3-5(c)(1)(i) require orders that would breach a threshold to be *blocked or rejected before entry*. This is a post-breach response: by the time it runs, the exposure exists. It supplements a pre-trade gateway and never replaces one.
- **Inside the alert-latency budget without measuring it.** For an EU firm, RTS 6 Art. 16 requires real-time alerts "within five seconds after the relevant event". This engine's decision is microseconds, but your metric computation, transport and notifier are not — budget the whole path, per `risk-control-latency-budget`.

## Prerequisites

- A breach observation: `event_id`, `metric_name`, `strategy_id`, `current_value`, `limit_value`, `timestamp_iso` (ISO-8601 **with a UTC offset** — a naive timestamp is rejected), and optionally `duration_seconds` and `direction`.
- A decision on `direction` **per metric**, because it selects the ratio formula. `UPPER` (default) for ceilings; `LOWER` for floors.
- For `UPPER`, `current_value` supplied as a **non-negative magnitude**. A drawdown passed as `-25000` against a limit of `10000` is rejected, not reinterpreted.
- An escalation ladder that is genuinely a ladder: thresholds strictly ascending, severity and action non-decreasing along it, every tier routed to at least one channel. The engine validates this at construction.
- An enforcement layer and a notifier to consume `action` and `notification_channels`, plus somewhere durable to persist the audit trail.

## Workflow

1. **Compute the breach ratio in the right direction**:
   - `UPPER`: $\text{ratio} = \text{current} / \text{limit}$.
   - `LOWER`: $\text{ratio} = 1 + (\text{limit} - \text{current}) / \text{limit}$, floored at 0 — sitting exactly on the floor is 1.0, and an exhausted buffer is 2.0. That mapping is a **house calibration so one ladder serves both directions**, not a standard.
   - **Decision point — a negative `UPPER` metric is an error, not a datum.** The engine cannot distinguish "drawdown expressed as a negative" from a genuine two-sided exposure by inspection. Guessing produced a ratio of $-2.5$ for a 2.5x drawdown, which matched no tier and returned `NONE`. Pass the magnitude, or set `LOWER`.
   - **Decision point — compare on the exact ratio, never a rounded one.** Rounding to 4dp before comparison turns 1.99996x into 2.0000 and force-liquidates a book at a threshold it never reached. Round for display only.

2. **Match the ratio to the highest tier it satisfies**:
   - Walk the ladder downward; the first tier with $\text{ratio} \ge \text{threshold}$ wins. Thresholds are inclusive: exactly 1.0x is a breach.
   - **Decision point — a sub-threshold event is still recorded.** A `BreachEvent` that evaluates below the lowest tier is evidence about the *upstream detector*, so it produces a `NONE` decision that is written to the audit trail rather than discarded.

3. **Apply duration escalation as a whole-rung promotion**:
   - At or beyond `sustained_breach_seconds` (default 300 s), promote to the next rung — severity, action, channels **and** acknowledgement deadline together.
   - **Decision point — promoting the action alone is the bug this replaces.** Raising a sustained AMBER breach to a RED/HALT while leaving its alerts on Slack and e-mail with the 300 s AMBER deadline produces exactly the unrouted-critical-notification failure this skill exists to prevent. Promote the rung, not the verb.
   - **Decision point — promotion is positional, so custom ladders work.** Escalation moves to `policies[i+1]`, whatever actions that ladder is built from. A hard-coded `WARN→REDUCE→HALT` chain silently escalates nothing for a ladder using `THROTTLE` or `GLOBAL_KILL_SWITCH`, and never escalates a RED breach at all.
   - **Decision point — the top rung does not promote.** A sustained CRITICAL breach stays CRITICAL and the audit note says so. There is nothing above it to promote to.

4. **Latch the incident, then route and record**:
   - **Decision point — escalation ratchets.** A metric oscillating around a threshold must not cancel an in-flight FLATTEN on the next tick, so a later observation of the same `(strategy_id, metric_name)` cannot return a weaker action than the incident has already reached. De-escalation is deliberate and logged: `reset_incident()`.
   - **Decision point — do not re-fire a destructive action on a retried alert.** An identical `event_id` with an identical payload returns the original decision marked `is_replay=True` and adds no second audit row. The same `event_id` with a *changed* payload is a re-evaluation of an ongoing breach — the normal way a monitor reports a growing duration — and is processed.
   - Hand `action` to the enforcement layer and `notification_channels` to the notifier, then persist the audit row.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A NaN risk metric reads as "no breach"**: `nan >= threshold` is `False` for every tier, so a corrupt feed silently disables the entire escalation matrix — no alert, no action, and the absence of an alarm looks exactly like safety. The engine rejects non-finite input instead.
- **Sign-convention mismatch between the detector and the matrix**: the single most likely way this control fails in production is a drawdown reported as a negative number. It produces a negative ratio, matches no tier, and returns `NONE` for a catastrophic breach.
- **Rounding the ratio before comparing it**: a display concern that becomes a trading action. 1.99996x rounded to 2.0000 triggers a CRITICAL force-flatten below the threshold.
- **Escalating the action without escalating the routing**: a HALT announced only on Slack at 03:00 is a HALT nobody knows about until the desk opens.
- **Retrying an alert that already flattened**: alert pipelines retry on timeout, and `FLATTEN` is not idempotent at the enforcement layer. Dedupe on `event_id` **and** payload, not on `event_id` alone — deduping on the id alone would freeze an ongoing breach at its first decision and prevent duration escalation entirely.
- **Letting a metric oscillate its way out of an escalation**: without latching, one tick back under the threshold silently downgrades an active CRITICAL incident to a WARN.
- **Treating the defaults as standards**: the 1.0/1.2/1.5/2.0 multipliers, the 300 s sustained window and the 900/300/120/60 s acknowledgement deadlines are house defaults. No regulator prescribes them; do not cite them as compliance evidence.
- **Sharing a mutable ladder between engines**: when `DEFAULT_POLICIES` is a module-level list of mutable policies, one component reassigning a tier's action changes the response of every other engine in the process. The ladder and its rungs are frozen for this reason.
- **Trusting `ack_deadline_seconds` to be enforced**: it is a number in a record. Nothing in this engine watches the clock or notices that no one acknowledged.
- **Configuring a ladder that is not monotone**: a higher tier with a milder action or a lower severity means a worse breach gets a weaker response. Rejected at construction rather than discovered during an incident.
- **Relying on this instead of a pre-trade control**: by the time an escalation decision exists, the position exists. RTS 6 Art. 15 and Rule 15c3-5(c)(1)(i) are about preventing entry.

## Verification

- Instantiate `RiskEscalationMatrix()` and process a 2.5x `DAILY_DRAWDOWN` breach ($25000/10000$): expect `CRITICAL`, `FLATTEN`, channels exactly `(PAGERDUTY, COMPLIANCE_TICKET)`, `ack_deadline_seconds == 60`.
- Tier boundaries are inclusive and exact: ratios 1.0, 1.2, 1.5 and 2.0 must map to WARN/INFO/900s, REDUCE/AMBER/300s, HALT/RED/120s and FLATTEN/CRITICAL/60s respectively; 1.4999 must stay on the 1.2 rung; **1.99996 must match the 1.5 rung, not 2.0** (the rounding regression).
- Duration escalation promotes a whole rung: 1.3x held 600 s must return `HALT` **with** `PAGERDUTY` routing and `ack_deadline_seconds == 120` — not HALT on the AMBER channels. 1.6x held 4 h must reach `FLATTEN`. 2.5x held 24 h must stay `FLATTEN` with `is_duration_escalated is False`. The boundary is inclusive: 300.0 s is sustained, 299.999 s is not.
- `LOWER` direction, computed by hand against a 50,000 floor: 60,000 → ratio 0.8, `NONE`; 50,000 → 1.0, `WARN`; 40,000 → 1.2, `REDUCE`; 0 → 2.0, `FLATTEN`; 125,000 → floored at 0.0.
- Fail-closed checks — each must raise: `NaN`/`Inf` metric, negative `UPPER` metric, `limit_value <= 0`, negative duration, blank `event_id`/`metric_name`/`strategy_id`, a boolean metric, a naive or unparseable `timestamp_iso`, and `evaluate(1e9, 0.0)`. A numeric *string* (`"2.5"`) from a JSON payload must be accepted.
- Ladder validation must reject: an empty `policies` list (it must not silently restore the defaults), duplicate thresholds, a weakening action, a decreasing severity, an unrouted tier, a non-positive ack timeout, and non-ascending legacy levels including all-equal ones.
- Latching and replay: after a FLATTEN, a 1.05x observation of the same strategy/metric must still return `FLATTEN` with `is_latched is True`; `reset_incident()` must restore `WARN`; a byte-identical resubmission must yield exactly one FLATTEN row with `is_replay is True` on the second call, while the same id with a longer duration must be re-evaluated.
- Audit rows must be frozen (assignment raises), must carry `current_value`, `limit_value`, `duration_seconds` and the normalised UTC `timestamp_iso`, and must include sub-threshold `NONE` decisions.
- Run `python -m unittest discover -s skills/risk-limit-breach-escalation-matrix/scripts` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `risk-control-bypass-audit-logging`
- `risk-limit-calibration-against-historical-drawdowns`
- `on-call-rotation-and-escalation-for-trading-systems`
- `position-limit-breach-simulation-fire-drills`
- `risk-control-latency-budget`
- `margin-utilization-circuit-breaker`
