---
name: position-limit-breach-simulation-fire-drills
description: >-
  Use when running scheduled fire drills that inject simulated position-limit breaches into a non-production risk stack and grade what the pre-trade gateway, post-trade exposure control and kill switch actually did against the outcome the rule requires.
domain: Regulatory Compliance & Risk Controls
subdomain: Operational Risk Simulation & Compliance Fire Drills
tags: ["fire-drill", "position-limits", "cftc-compliance", "risk-gateway", "kill-switch", "operational-risk", "simulation"]
brokers_frameworks: ["CME Rule 559/562", "CFTC Part 150 Speculative Limits", "MiFID II RTS 6", "SEC Rule 15c3-5"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when scheduling or auditing position-limit breach fire drills — periodic, non-production exercises that inject a synthetic over-limit position and check that the pre-trade risk gateway, the post-trade exposure control, the kill switch and the compliance alerting pipeline all behave the way the applicable rule requires. It is the exercise behind the annual review obligations: SEC Rule 15c3-5(e)(1) requires broker-dealers with market access to review the effectiveness of these controls "no less frequently than annually", and MiFID II RTS 6 Art. 10 requires an investment firm's annual self-assessment to test that the controls in Arts. 12–18 withstand market stress.

The harness grades *evidence*, not intent. You supply the scenario (what was injected and what the rule says should happen) and the observed response (what the gateway, kill switch and alerting pipeline actually did); it reports whether they match. A drill that cannot fail is not a drill.

## When NOT to Use

- **As a risk control.** Nothing here rejects an order or trips a kill switch. It is a grader for a control that already exists. If the pre-trade gateway is the thing you are building, see `sec-rule-15c3-5-risk-controls-us` and `kill-switch-and-drawdown-circuit-breakers`.
- **Against production.** A `PRODUCTION` scenario raises `ProductionEnvironmentError` and never runs. RTS 6 Art. 10 requires stress tests be carried out "in such a way that they do not affect the production environment", and an injected breach order that reaches a live matching engine is a real position-limit violation under CME Rule 562, not a test.
- **Wired to a mock that mirrors the scenario.** If the observation is derived from the scenario ("it was over the limit, therefore it was blocked"), every drill passes and the exercise is theatre. The observation must come from the system under test's own logs.
- **To determine whether a limit applies.** The harness compares a quantity to a threshold you supply. Futures-equivalent conversion, option delta weighting, month aggregation and referenced-contract equivalence (17 CFR 150.2) happen upstream — see `position-limit-reporting-cftc-large-trader`.
- **As the escalation process itself.** Grading a drill is not running an incident. Escalation ownership and timing belong in `risk-limit-breach-escalation-matrix`.

## Prerequisites

- A non-production risk stack (`SANDBOX`, `STAGING` or `PAPER`) that is a faithful copy of the production control configuration — a drill against stale limits proves nothing about the live gateway.
- Per scenario: `scenario_id`, `breach_type`, `target_symbol`, `injected_position_qty`, `limit_threshold`, `control_phase` and the `expected_outcome` derived from the applicable rule.
- Per scenario: an `ObservedControlResponse` captured from the system under test — rejection flag, kill-switch state, whether re-enabling requires a designated person, the compliance alert record id, the remediation action, and the measured latency.
- A calibrated `max_pre_trade_latency_ms`. The `5.0` default is a library default, **not** a regulatory threshold; no regulator publishes a maximum pre-trade risk-check latency.

## Workflow

1. **Build the scenario from the rule, not from the system.**
   - Set `expected_outcome` independently of what you expect the gateway to do — that independence is what lets the drill disagree with the system.
   - **Decision point — which phase can even see this breach?** A breach arriving with an order (`ROGUE_ALGO`, an over-limit new order) is `PRE_TRADE` / `BLOCK_AND_HALT`. A breach arriving *without* an order — option assignment, a close-of-day delta re-evaluation, a scheduled spot-month limit step-down, exposure aggregated across venues — is `POST_TRADE` / `ALERT_ONLY`: there is nothing to reject, and only RTS 6 Art. 17(4) maximum long/short/overall strategy position controls can catch it. Constructing it as a pre-trade block tests a control that was never going to fire.
   - **Decision point — is the correct answer "allow"?** Over-limit is not automatically a violation. CME Rule 562 gives one business day to liquidate an assignment-driven overage and excuses a position that exceeds limits on today's delta factors but not yesterday's; Rule 559 permits a bona fide hedging application filed within five business days of assuming the position. Those are `ALLOW` scenarios and each must carry an `exemption_basis` — the harness refuses an unexplained allowance, because it is otherwise indistinguishable from a gateway failure.

2. **Inject in a non-production environment and capture the response.**
   - Record the rejection, the halt, the alert id, the remediation action and the latency from the system's own logs.
   - Measure intraday, not at the close. CME Rule 562: "Any positions, including positions established intraday, in excess of those permitted under the rules of the Exchange shall be deemed position limit violations."

3. **Grade the drill.**
   - `run_fire_drill(scenario, observed)` compares the two and returns `passed` plus a `findings` tuple naming each deviation and the rule behind it.
   - **Decision point — a halt that re-enables itself is a finding.** RTS 6 Art. 15(3) requires a tripped system be "automatically disabled until re-enabled by a designated staff member", so `manual_reenable_required` is asserted whenever `trading_halted` is true.
   - **Decision point — which SLA applies.** Pre-trade drills are graded against the internal `max_pre_trade_latency_ms`. Post-trade drills are graded against `max_alert_latency_ms`, which defaults to the one timing number RTS 6 actually publishes: Art. 16(5), "Real-time alerts shall be generated within five seconds after the relevant event." An unrecorded latency fails the drill rather than passing silently.

4. **Grade the suite, including its coverage.**
   - `run_drill_suite(suite_id, cases)` requires at least one `ALLOW` negative control and at least one `POST_TRADE` scenario before it will report `all_passed`. A suite of nothing but "inject a breach, expect a block" cannot detect a gateway that blocks *everything*, and cannot detect a breach that arrives without an order.
   - Duplicate `scenario_id`s are rejected: drill records must be individually attributable in the validation report (RTS 6 Art. 9) or the annual 15c3-5(e) review file.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A harness that cannot fail.** Deriving "the gateway blocked it" from "the position exceeded the limit" produces a green report for a gateway that is wide open. The observation must be captured from the system under test; if scenario and observation share a source, the drill measures nothing.
- **No negative control.** A gateway that rejects every order passes 100% of breach-only drills. Without an `ALLOW` case — a within-limit order, or an over-limit position under a documented exemption — over-blocking is invisible, and over-blocking is what takes a desk out of the market on a busy day.
- **Drilling only the pre-trade path.** Under RTS 6 the derivative position controls sit in Art. 17(4) as *post-trade* controls; Art. 15's pre-trade list is price collars, order values, order volumes and message limits. Option assignment, delta re-evaluation at the close, and a spot-month limit stepping down on its scheduled effective date all put an account over the limit with no order to reject.
- **Treating every overage as a violation.** Blocking a position that CME Rule 562's assignment grace or Rule 559's five-business-day hedge filing permits is a control defect, not compliance.
- **Testing end-of-day positions only.** Intraday overages are violations under Rule 562 even if flat by the close, so an end-of-day drill exercises the wrong measurement point entirely.
- **Presenting the latency SLA as a compliance threshold.** SEC Rule 15c3-5 is a "reasonably designed" standard and specifies no latency number; neither does CFTC Part 150 nor RTS 6 for order rejection. Reporting a 5 ms internal target to an auditor as a regulatory requirement misrepresents the rule.
- **Assuming one jurisdiction's rule everywhere.** Since 28 February 2022, MiFID II Art. 57 position limits reach only agricultural commodity derivatives and critical or significant commodity derivatives (Directive (EU) 2021/338). SEC Rule 15c3-5 binds US broker-dealers with market access, not futures activity. CFTC Part 150 and exchange-set limits are separate again.
- **Drilling a staging stack whose limits have drifted from production.** The drill then certifies a configuration nobody trades against — see `configuration-drift-detection-across-environments`.

## Verification

- Instantiate `FireDrillSimulator()`. Grade an `EXCHANGE_LIMIT` scenario (12,000 contracts against a 10,000 limit, `STAGING`, `PRE_TRADE`, `BLOCK_AND_HALT`) against a healthy response (`order_rejected=True`, `trading_halted=True`, `manual_reenable_required=True`, an alert id, `risk_latency_ms=1.2`) ⟹ `status == "CONTROL_VERIFIED"`, `passed` true, `findings == ()`.
- Regression: grade the same scenario against `order_rejected=False, trading_halted=False`, no alert ⟹ `status == "CONTROL_FAILED"` with three findings. The previous version of this skill reported `BREACH_BLOCKED_KILL_SWITCH_ENGAGED` for exactly this input.
- Regression: a within-limit `ALLOW` scenario graded against `order_rejected=True` ⟹ fails with an "over-blocking" finding.
- Boundary: `is_over_limit(10_000.0, 10_000.0)` is `False` and `is_over_limit(10_001.0, 10_000.0)` is `True` — Rule 562 says "in excess of".
- Negative checks: a `PRODUCTION` scenario raises `ProductionEnvironmentError` (even with the simulator disabled); a `NaN` quantity, a non-positive `limit_threshold`, a negative latency, a blank `scenario_id`, a duplicate `scenario_id`, an unexplained over-limit `ALLOW`, and a `POST_TRADE` scenario expecting `BLOCK_AND_HALT` each raise.
- Coverage: a suite with no `ALLOW` case or no `POST_TRADE` case reports `all_passed is False` with a `coverage_findings` entry even when every individual scenario passed.
- Run `python -m unittest discover -s skills/position-limit-breach-simulation-fire-drills/scripts` and confirm a 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `leverage-limit-enforcement-across-instruments`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `position-limit-reporting-cftc-large-trader`
- `risk-limit-breach-escalation-matrix`
- `risk-control-unit-testing-framework`
- `configuration-drift-detection-across-environments`
- `chaos-engineering-for-trading-infrastructure`
