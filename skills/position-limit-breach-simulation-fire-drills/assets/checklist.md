# Pre-Flight / Sign-off Checklist — position-limit-breach-simulation-fire-drills

## Scope
- [ ] Binding regime identified (CME Rules 559/562, CFTC Part 150, SEC Rule 15c3-5, RTS 6) and recorded on the drill report.
- [ ] Drill cadence satisfies the applicable review obligation (SEC Rule 15c3-5(e)(1) annual review; RTS 6 Art. 9/10 annual self-assessment and stress testing).
- [ ] Target environment's limit configuration reconciled against production — no drift.

## Environment isolation
- [ ] Every scenario targets `SANDBOX`, `STAGING` or `PAPER`; no `PRODUCTION` scenario exists (RTS 6 Art. 10).
- [ ] No path routes an injected breach order to a live matching engine.
- [ ] Alert monitors were told the drill window, so drill alerts do not become a real incident.

## Scenario design
- [ ] `expected_outcome` derived from the rule, independently of what the system is believed to do.
- [ ] Suite covers `PRE_TRADE` / `BLOCK_AND_HALT` breaches (order-driven).
- [ ] Suite covers `POST_TRADE` / `ALERT_ONLY` breaches (option assignment, close-of-day delta re-evaluation, scheduled spot-month limit step-down, cross-venue aggregation) — RTS 6 Art. 17(4).
- [ ] Suite covers at least one `ALLOW` negative control, so over-blocking is detectable.
- [ ] Every over-limit `ALLOW` records an `exemption_basis` (CME Rule 562 assignment grace, Rule 559 hedge filing, RTS 6 Art. 15(6) authorised override).
- [ ] Boundary case included: a position exactly at the limit is compliant ("in excess of", Rule 562).
- [ ] Quantities converted to futures-equivalents upstream; net-long and net-short limits scripted separately where they differ.

## Observation capture
- [ ] Rejection flag, halt state, alert id, remediation action and latencies captured from the system under test's logs, not derived from the scenario.
- [ ] `manual_reenable_required` verified — a halt that resumes on its own is a finding (RTS 6 Art. 15(3)).
- [ ] Latency measured for every drill; an unmeasured latency is recorded as a failure, not a pass.
- [ ] Measurement taken intraday, not only at the close (Rule 562).

## Grading
- [ ] `max_pre_trade_latency_ms` calibrated for this firm and documented as an internal SLA, not a regulatory threshold.
- [ ] `max_alert_latency_ms` left at the RTS 6 Art. 16(5) five-second requirement, or a tighter figure justified.
- [ ] Suite-level `all_passed` reviewed together with `coverage_findings`; any disabled coverage gate is a recorded decision.
- [ ] No duplicate `scenario_id` in the suite — records must be individually attributable.

## Follow-through
- [ ] Report filed with scenario definitions, raw observations and findings, not just the verdict.
- [ ] Every `CONTROL_FAILED` has an owner, a date and a scheduled re-drill (RTS 6 Art. 9(4)).
- [ ] Automated Testing: run `python -m unittest discover -s skills/position-limit-breach-simulation-fire-drills/scripts` — 100% pass rate.

## Sign-off

- Drill suite ID: ___________________________
- Environment: ___________________________
- Reviewed by: ___________________________
- Date: ___________________________
