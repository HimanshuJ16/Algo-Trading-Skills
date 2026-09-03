---
name: risk-control-unit-testing-framework
description: >-
  Use before promoting a change to a pre-trade risk control, to run positive, boundary,
  breach and malformed-input cases and check the engine still rejects exactly what it
  should, matching triggered rule ids.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-testing, unit-test-harness, pre-trade-risk, limit-breach, fat-finger-collar, fail-closed-gate, rts-6-art-15, sec-rule-15c3-5
  brokers_frameworks: "MiFID II RTS 6 (EU 2017/589) Art. 5, 7, 11, 15; SEC Rule 15c3-5 (17 CFR 240.15c3-5); FINRA Regulatory Notice 15-09; FIA Guide to the Development and Operation of Automated Trading Systems (Mar 2015); Python Dataclasses; unittest"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a change to a pre-trade risk control (order-size cap, position cap, price collar, daily loss limit) is about to be promoted, and you need a machine-checkable answer to one question: **does this risk engine still reject exactly what it is supposed to reject, and nothing else?** The framework runs a suite of `RiskTestCase` objects through the engine, compares the triggered rule ids **as an exact set**, verifies that every rule in `required_rule_coverage` was actually fired by some case, and returns an auditable `RiskControlTestExecutionReport` whose `status` you branch on.

The rules modelled here are the ones named in primary sources: MiFID II **RTS 6 Art. 15** requires price collars, maximum order values, maximum order volumes and maximum message limits as pre-trade controls on order entry; **17 CFR 240.15c3-5(c)(1)(ii)** requires controls reasonably designed to prevent erroneous orders "that exceed appropriate price or size parameters". Neither rule prescribes how to test those controls — that gap is what this skill fills, and the testing expectation itself comes from RTS 6 Art. 5(1)/(4) and Art. 11, FINRA Regulatory Notice 15-09 (guidance), and FIA's March 2015 guide.

## When NOT to Use

- **As a production risk gate.** `PreTradeRiskEngine` is a *test fixture*: single-threaded, stateless between orders, and with no throttle, no credit check and no self-match prevention. Copying it into a live order path gives you a risk engine that has never seen concurrency. Point the framework at your real engine instead — it accepts any object exposing `evaluate_order(order) -> RiskCheckResult`.
- **For cumulative or rate-based controls.** 15c3-5(c)(1)(ii) covers erroneous orders "on an order-by-order basis **or over a short period of time**", and RTS 6 Art. 15(d) requires maximum message limits. This framework evaluates one order at a time against a stateless engine, so it cannot exercise either limb. A suite that is green here has said nothing about your throttle.
- **As evidence of RTS 6 or 15c3-5 conformance.** 15c3-5 contains no algorithm-testing mandate at all; its testing-adjacent requirement is the annual review under §(e)(1) and the CEO certification under §(e)(2). A passing gate is an input to Art. 5(1)/Art. 11 evidence, not a discharge of either.
- **As a latency benchmark.** The report carries p50/p99 of the engine's own evaluation time, but an interpreted engine timed on a shared CI runner measures the runner. Across repeated fresh-process runs of the shipped standard suite the p99 sits around $2.5\times$ the p50 at single-digit microseconds, with occasional outliers beyond $100\,\mu\text{s}$ from cold code paths alone. `enforce_latency_budget` is off by default for that reason; budget work belongs in `risk-control-latency-budget`.
- **As a substitute for the kill switch.** RTS 6 Art. 12 requires the ability to cancel any or all unexecuted orders immediately. Nothing here tests that — see `kill-switch-and-drawdown-circuit-breakers`.
- **When the ruleset under test is disabled.** A `RiskRuleConfig` with `enabled=False` allows every order. The framework does not special-case it; every negative test case simply fails and the gate reports `RISK_TEST_FAILURES_DETECTED`.

## Prerequisites

- A risk engine exposing `evaluate_order(order) -> RiskCheckResult`, or the shipped `PreTradeRiskEngine` plus a `RiskRuleConfig` (`max_order_size`, `max_position_size`, `max_daily_loss_usd`, `max_price_collar_pct`). Limits must be finite and positive — a zero or absent limit raises rather than meaning "unlimited" (FIA §1.1).
- Test cases written against **your desk's** limits. The shipped standard suite is written against the library defaults (size $1000$, position $5000$, daily loss $10{,}000$, collar $5\%$); those are engineering placeholders, not thresholds any regulator publishes.
- Order state per case: `side` ∈ {BUY, SELL}, `quantity`, `price`, `current_mid_price`, `current_position`, `accumulated_daily_pnl_usd`, and — for a faithful position-cap test — `working_buy_quantity` / `working_sell_quantity`.
- Sign conventions: `accumulated_daily_pnl_usd` is signed (a loss is negative) while `max_daily_loss_usd` is a positive magnitude; `max_price_collar_pct` is a fraction ($0.05 = 5\%$).
- Threshold convention, applied uniformly: **the configured limit value is itself permitted; a breach requires exceeding it.** An order of exactly `max_order_size` passes.

## Workflow

1. **Scenario Suite Construction** — for *each* rule, write four cases: a valid order, one at the exact threshold (must be allowed), one just past the threshold, and the gross breach.
   - **Decision point — the boundary case is the one that finds bugs.** A suite that tests $500$ against a $1000$ cap and $2000$ against a $1000$ cap passes against an engine that uses `>=` and against one that uses `>`. Only the case at exactly $1000$ distinguishes them.
   - Add malformed-input cases: NaN/negative quantity, non-positive price, an unrecognised `side`, and an absent reference price.
2. **Expectation Specification**:
   - **Decision point — name the exact rule set, not one member.** `expected_triggered_rules` is compared with set equality. A case that expects `POSITION_CAP` and gets `{POSITION_CAP, MAX_ORDER_SIZE}` **fails**, because the extra rejection means a limit is firing where it should not — an over-tight risk control rejects good orders in production and no membership assertion would ever surface it.
   - `expected_allowed=True` combined with an expected rule is self-contradictory and raises; it is not silently resolved.
3. **Order Evaluation** — each case is passed to `evaluate_order`; the framework records `is_allowed`, the triggered rule ids, the rejection reasons and the per-evaluation latency.
   - **Decision point — malformed order data must be a rejection, not an exception.** A NaN quantity reaches a live gate for real, and every limit comparison against NaN is `False`. The engine returns `INVALID_ORDER` and stops, rather than evaluating on and allowing the order.
4. **Coverage Evaluation**:
   - **Decision point — a suite that never fired a rule has not tested it, however green it looks.** A suite of nothing but valid orders passes every case; `failed_tests` is `0` and `status` is `RISK_TEST_COVERAGE_INCOMPLETE`. Read `status`, never the count.
5. **CI/CD Gate Evaluation** — precedence is empty suite → case failures → missing coverage → (if enforced) latency budget → pass. Only `ALL_RISK_TESTS_PASSED` promotes the build.
6. **Report Generation** — persist the `RiskControlTestExecutionReport` (`ruleset_id`, `rules_exercised`, `missing_rule_coverage`, per-case `actual_triggered_rules`) against the change record, as the "means used for testing" evidence RTS 6 Art. 5(1) and Art. 11 contemplate.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Asserting rule membership instead of the exact rule set**: `expected_rule in triggered_rules` passes when the engine *also* fires two unrelated rules. The over-tight limit that silently rejects valid flow is invisible to every such assertion. Compare sets.
- **Letting NaN through the gate**: `float('nan') > max_order_size` is `False`, and so is every other comparison. An unvalidated NaN quantity therefore breaches no rule and the order is **allowed** — the classic fail-open. Validate first, reject on malformed input.
- **Treating an unrecognised side as a SELL**: `side.upper() == "BUY"` sends `"BUYY"`, `"Buy Ltd"` and `""` down the SELL branch, flipping the sign of the position projection so a long-side breach is projected short and passes. Whitelist the side.
- **Skipping the price collar when the reference price is missing**: `if mid_price > 0:` disables the fat-finger check exactly when a stale or absent feed makes a fat finger likeliest. FIA §1.3 requires the opposite — aberrant market data blocks orders while it is investigated. A collar that cannot be evaluated must reject.
- **Projecting the position from holdings alone**: FIA §1.2 requires working orders to be included, "such that limits would not be breached if that order is filled". A cap checked against `position + new_quantity` is breached by five individually-compliant orders resting at once.
- **Dividing to test a percentage collar**: `abs(price - mid) / mid > 0.05` spuriously rejects an order priced at exactly the collar for roughly $1\%$ of reference prices — e.g. mid $402.69$, price $422.8245$ evaluates to $0.05000000000000001$. Compare `abs(price - mid) > collar * mid`.
- **Reading `failed_tests == 0` as a pass**: it is also `0` for an empty suite and for an all-positive suite that exercised no rule at all. Both are fail-closed statuses; a pipeline branching on the count ships them.
- **Testing only in a backtest**: a backtest exercises the risk rule only on the paths the strategy happened to take. Boundary and malformed-input cases essentially never occur there.
- **Asserting a microsecond SLA in CI**: a single-shot timing of an interpreted engine on a shared runner is noise-dominated — the shipped suite's own p99 sits around $2.5\times$ its p50 run to run, and single evaluations beyond $100\,\mu\text{s}$ show up from cold code paths alone. Report the percentiles; enforce a budget only against the real engine on representative hardware.
- **Mutating limits mid-suite**: `RiskRuleConfig` is frozen precisely so case A cannot loosen a limit that case B then "passes" under.

## Verification

- Instantiate `RiskControlUnitTestFrameworkEngine()` and run `run_standard_suite()` $\implies$ `status == "ALL_RISK_TESTS_PASSED"`, `total_tests == 16`, `pass_rate_pct == 100.0`, `coverage_satisfied` true, and `rules_exercised` equal to all six rule ids including `INVALID_ORDER` and `REFERENCE_PRICE_UNAVAILABLE`.
- Boundary checks (must be **allowed**): quantity exactly $1000$; position $4000$ + BUY $1000$ against a $5000$ cap; `accumulated_daily_pnl_usd == -10000.0` against a $10{,}000$ limit; price $422.8245$ against mid $402.69$ at a $5\%$ collar. One increment past each must be rejected by exactly one rule.
- Fail-closed checks (must be **rejected** with `INVALID_ORDER`): NaN, infinite, zero and negative quantity; NaN and non-positive price; NaN position; NaN daily PnL; negative working quantity; `side` of `"BUYY"`, `""` and `None`. A mid price of `0.0` or NaN must reject with `REFERENCE_PRICE_UNAVAILABLE` — and must not mask a simultaneous `MAX_ORDER_SIZE` breach.
- Working-order check: position $4000$ + $900$ resting buys + BUY $200$ $\implies$ `POSITION_CAP`; the same case with $100$ instead of $200$ is allowed.
- Assertion-strength check: an order breaching both size and collar, asserted as `expected_triggered_rule=MAX_ORDER_SIZE`, must **fail** the test case.
- Gate checks: `run_suite([])` $\implies$ `RISK_TEST_SUITE_EMPTY`; a suite of three valid orders $\implies$ `RISK_TEST_COVERAGE_INCOMPLETE` with `failed_tests == 0`; a permissive engine that allows everything $\implies$ `RISK_TEST_FAILURES_DETECTED`.
- Mis-wiring checks (must **raise**): blank or duplicate `test_name`; `expected_allowed=True` with an expected rule; both rule arguments supplied; a bare string passed as `expected_triggered_rules`; `RiskRuleConfig` with a zero, negative, infinite or NaN limit; `enforce_latency_budget=True` without a budget.
- Run `python -m unittest discover -s skills/risk-control-unit-testing-framework/scripts` and confirm a 100% pass rate.

## Related Skills

- `risk-control-bypass-audit-logging`
- `risk-control-latency-budget`
- `execution-algorithm-regression-testing-suite`
- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `position-limit-breach-simulation-fire-drills`
