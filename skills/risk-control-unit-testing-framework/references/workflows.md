# Workflows — risk-control-unit-testing-framework

## 0. Choose the engine under test

The framework accepts any object exposing `evaluate_order(order) -> RiskCheckResult`.

- **Testing your own gate** — pass it directly. This is the normal case; the shipped
  `PreTradeRiskEngine` is a fixture for exercising the framework, not a production gate.
- **Testing the shipped fixture** — construct `PreTradeRiskEngine(RiskRuleConfig(...))`.
  Limits must be finite and positive; a zero or absent limit raises rather than being read
  as "unlimited" (FIA §1.1).

`RiskRuleConfig` is frozen. Build one config per suite; never loosen a limit between cases.

## 1. Scenario construction

For **each** rule, write the four-case pattern:

| Case | Purpose | Example (default limits) |
|---|---|---|
| Valid | The rule does not fire on good flow | BUY $100$ @ $150$, mid $150$ |
| Exact threshold | Pins `>` vs `>=` | quantity exactly $1000$ → **allowed** |
| Smallest breach | Pins the direction of the boundary | quantity $1000.01$ → `MAX_ORDER_SIZE` |
| Gross breach | Sanity | quantity $2000$ → `MAX_ORDER_SIZE` |

Only the *exact threshold* case distinguishes an engine using `>` from one using `>=`;
a suite of $500$ and $2000$ passes against both.

Then add the malformed-input cases, which is where a risk gate fails open rather than
closed:

- `quantity` NaN, `+inf`, `0`, negative
- `price` NaN or $\le 0$
- `current_position` NaN, `accumulated_daily_pnl_usd` NaN
- `side` of `"BUYY"`, `""`, `None` — anything outside {BUY, SELL}
- `current_mid_price` of `0.0`, negative or NaN
- negative `working_buy_quantity` / `working_sell_quantity`

And the position-cap cases that only working orders reveal: position $4000$ + $900$
resting buys + BUY $200$ must breach a $5000$ cap even though $4000 + 200$ would not.

## 2. Expectation specification

```python
RiskTestCase(
    test_name="Position cap includes working orders",
    order=ProposedOrder("O8", "AAPL", "BUY", 200.0, 150.0,
                        current_mid_price=150.0, current_position=4000.0,
                        working_buy_quantity=900.0),
    expected_allowed=False,
    expected_triggered_rules=(RULE_POSITION_CAP,),
)
```

- `expected_triggered_rules` is matched by **set equality**, order-insensitive. A case
  expecting `POSITION_CAP` that also receives `MAX_ORDER_SIZE` fails — the extra rejection
  means a limit is firing where it should not, which in production rejects good flow.
- `expected_allowed=False` with no rules named accepts *any* rejection. Use it sparingly;
  it cannot tell a correct rejection from a coincidental one.
- `expected_allowed=True` with an expected rule raises. So does a blank `test_name`, a
  duplicate `test_name` in a suite, passing both rule arguments, or passing a bare string
  where a sequence of rule ids is expected (it would iterate into single characters).

## 3. Evaluation semantics

`PreTradeRiskEngine.evaluate_order` runs in this order:

1. **Structural validation.** Any failure emits `INVALID_ORDER` and **stops**. Limit
   comparisons against NaN are all `False`, so evaluating on would allow the order; and a
   rule list computed from garbage input is misleading in the audit trail.
2. **`MAX_ORDER_SIZE`** — `quantity > max_order_size`.
3. **`POSITION_CAP`** — worst case on each side, without netting:
   - `projected_long  = position + working_buys  + (qty if BUY  else 0)`
   - `projected_short = position - working_sells - (qty if SELL else 0)`
   - breach if `projected_long > cap` or `projected_short < -cap`.
   Netting resting sells against the long projection would understate the worst case: the
   buys can fill while the sells do not.
4. **`FAT_FINGER_PRICE_COLLAR` / `REFERENCE_PRICE_UNAVAILABLE`** — an unusable mid price
   blocks with `REFERENCE_PRICE_UNAVAILABLE` (it does not mask a simultaneous size or loss
   breach); otherwise `abs(price - mid) > collar * mid`, never the division form.
5. **`DAILY_LOSS_LIMIT`** — `accumulated_daily_pnl_usd < -max_daily_loss_usd`.

A `RiskRuleConfig` with `enabled=False` returns "allowed" for everything and logs a
warning. It is not special-cased by the gate: every negative case simply fails.

## 4. Coverage evaluation

`rules_exercised` is the union of rule ids the engine actually fired across the suite.
`required_rule_coverage` (default: all six) is checked against it. A rule that never fired
was never tested, and the suite is rejected with `RISK_TEST_COVERAGE_INCOMPLETE` even
though `failed_tests == 0`.

Set `required_rule_coverage` to the rules **your** engine implements; leaving a rule in the
requirement after retiring it keeps the gap visible rather than silently absent (FIA §5.2
says obsolete tests should be removed from the plan — deliberately, not by drift).

## 5. Gate evaluation and CI/CD wiring

Status precedence:

| Order | Status | Trigger |
|---|---|---|
| 1 | `RISK_TEST_SUITE_EMPTY` | no cases |
| 2 | `RISK_TEST_FAILURES_DETECTED` | any case failed |
| 3 | `RISK_TEST_COVERAGE_INCOMPLETE` | a required rule never fired |
| 4 | `RISK_TEST_LATENCY_BUDGET_EXCEEDED` | p99 over budget **and** `enforce_latency_budget=True` |
| 5 | `ALL_RISK_TESTS_PASSED` | promote |

```python
report = framework.run_standard_suite()
if report.status != "ALL_RISK_TESTS_PASSED":
    sys.exit(1)          # branch on status
# NOT: if report.failed_tests == 0  -> also 0 for an empty suite and for
#      a suite that exercised no rule at all.
```

A `ValueError`/`TypeError` from the framework means the harness is mis-wired. Let it fail
the build; catching and ignoring it converts a broken harness into a green pipeline.

## 6. Latency reporting

Each evaluation contributes one sample; the report carries `latency_p50_microseconds`,
`latency_p99_microseconds` and `latency_sample_count` (nearest-rank percentiles, no numpy).

Enforcement is opt-in and should stay off for the shipped fixture: across repeated
fresh-process runs the standard suite's p99 sits around $2.5\times$ its p50, with occasional
evaluations beyond $100\,\mu\text{s}$ from cold code paths, so a µs budget asserted on a
shared CI runner measures the runner. Budget the *real* engine on
representative hardware — see `risk-control-latency-budget`.

## 7. Report retention

Persist the full `RiskControlTestExecutionReport` against the change record:
`ruleset_id`, `status`, `rules_exercised`, `missing_rule_coverage`, and per case
`expected_triggered_rules` / `actual_triggered_rules` / `detail`. That is the evidence a
reviewer under RTS 6 Art. 11(1) — or the annual 15c3-5 §(e)(1) effectiveness review —
consumes. Record the *limits under test* alongside it: a green report is meaningless
without the thresholds it was green against.
