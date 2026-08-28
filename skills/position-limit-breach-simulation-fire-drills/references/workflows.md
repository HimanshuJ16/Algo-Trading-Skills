# Workflows — position-limit-breach-simulation-fire-drills

## 0. Scope the drill programme

- Establish the binding regime first: CME Group exchange rules (559/562), CFTC Part 150
  federal limits, SEC Rule 15c3-5 (US broker-dealers with market access), RTS 6
  (EU/UK algorithmic trading), or a combination. The rule chosen determines what the
  correct outcome *is*, and therefore what a drill can conclude.
- Fix the cadence. SEC Rule 15c3-5(e)(1) requires an effectiveness review at least
  annually; RTS 6 Art. 10 ties stress testing to the Art. 9 annual self-assessment.
  Quarterly drills feed both without either becoming a scramble.
- Confirm the target environment's limit configuration matches production. A drill run
  against drifted staging limits certifies a configuration nobody trades against.

## 1. Author scenarios from the rule

Build a matrix that covers all three breach shapes. A suite covering only the first row
cannot fail in the ways that matter.

| Shape | `control_phase` | `expected_outcome` | Representative cases |
|---|---|---|---|
| Breach arrives with an order | `PRE_TRADE` | `BLOCK_AND_HALT` | Rogue algo accumulating past a house limit; a single order taking the account over an exchange spot-month limit; a broker-limit breach |
| Breach arrives without an order | `POST_TRADE` | `ALERT_ONLY` | Option assignment; a close-of-day delta re-evaluation; a scheduled spot-month limit step-down; exposure aggregated across venues or clearing members |
| Correct answer is "do nothing" | either | `ALLOW` | Order at exactly the limit; order comfortably within the limit; assignment overage inside the CME Rule 562 one-business-day grace; position under a granted Rule 559 hedge exemption |

Rules the scenario author must respect:

- `expected_outcome` is derived from the rule, never from what the system is believed to
  do. That independence is the only thing that lets a drill disagree with the system.
- An over-limit `ALLOW` requires an `exemption_basis` naming the rule relied on. The
  harness rejects an unexplained allowance because it cannot be told apart from a
  gateway failure.
- A `POST_TRADE` scenario cannot expect `BLOCK_AND_HALT` — there is no order to reject.
- Limits are compared as `abs(qty) > threshold`. Where net-long and net-short limits
  differ, or spot-month and all-months-combined limits both apply, write one scenario
  per limit.
- Convert to futures-equivalents before the quantity reaches the scenario. Delta
  weighting, month aggregation and referenced-contract equivalence (17 CFR 150.2) are
  upstream concerns.

## 2. Inject into a non-production stack

- Permitted environments: `SANDBOX`, `STAGING`, `PAPER`. `PRODUCTION` raises
  `ProductionEnvironmentError` (RTS 6 Art. 10: tests must not affect the production
  environment). An injected breach order that reaches a live matching engine is a real
  Rule 562 violation.
- Inject and measure **intraday**. Rule 562 deems intraday overages violations even
  where the position is flat by the close, so an end-of-day-only drill exercises the
  wrong measurement point.
- Announce the window to whoever monitors alerts, or the drill's own alerts become a
  real incident.

## 3. Capture the observed response

Record from the system under test's own logs — never from the scenario:

| Field | Source | Why it is asked for |
|---|---|---|
| `order_rejected` | Gateway reject log / FIX reject | The pre-trade control's actual verdict |
| `trading_halted` | Kill-switch state store | Whether the strategy was disabled |
| `manual_reenable_required` | Kill-switch configuration | RTS 6 Art. 15(3): disabled "until re-enabled by a designated staff member" |
| `compliance_alert_id` | Alerting pipeline record | Makes the alert auditable rather than asserted |
| `remediation_action` | Incident/ops record | RTS 6 Art. 17(1): adjust, shut down, or withdraw in an orderly manner |
| `risk_latency_ms` | Gateway timing instrumentation | Pre-trade SLA (internal) |
| `alert_latency_ms` | Alert pipeline timing | RTS 6 Art. 16(5) five-second alert requirement |

If a field cannot be captured, that is itself the finding. An unmeasured latency fails
the drill rather than passing quietly.

## 4. Grade

```python
sim = FireDrillSimulator(FireDrillSimulatorConfig(max_pre_trade_latency_ms=5.0))
result = sim.run_fire_drill(scenario, observed)
if not result.passed:
    raise_incident(result.scenario_id, result.findings)
```

- `CONTROL_VERIFIED` — behaviour matched the rule and the latency was within SLA.
- `LATENCY_SLA_BREACHED` — behaviour was correct but slow. Distinct from a control
  defect and remediated differently.
- `CONTROL_FAILED` — behaviour deviated, or no latency was recorded. `findings` names
  each deviation and the rule behind it.
- `DRILL_SKIPPED_SIMULATOR_DISABLED` — never a pass; the control was not exercised.

## 5. Grade the suite and its coverage

```python
report = sim.run_drill_suite("FIRE_DRILL_2026Q3", cases)
```

`all_passed` requires every scenario to pass **and** the suite to contain at least one
`ALLOW` negative control and at least one `POST_TRADE` scenario. Both gates can be
switched off in config, but doing so should be a recorded decision: without them a suite
of breach-only cases gives a clean bill of health to a gateway that blocks everything
and to a system blind to assignment-driven overages.

Duplicate `scenario_id`s are rejected so each drill record stays individually
attributable in the RTS 6 Art. 9 validation report or the 15c3-5(e) annual review file.

## 6. Close the loop

- File the suite report with the scenario definitions, the raw observations and the
  findings. The report is the evidence; the pass/fail verdict alone is not.
- Every `CONTROL_FAILED` becomes a remediation item with an owner and a date, and is
  re-drilled after the fix — RTS 6 Art. 9(4) requires deficiencies identified in the
  validation report be remedied.
- Feed root causes into `post-breach-root-cause-analysis-template`; feed escalation
  timing into `risk-limit-breach-escalation-matrix`.
