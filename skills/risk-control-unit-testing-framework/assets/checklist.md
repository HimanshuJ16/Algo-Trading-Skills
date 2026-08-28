# Pre-Flight Checklist

## Engine and configuration
- [ ] Is the suite pointed at the **real** risk engine, not the shipped
      `PreTradeRiskEngine` fixture (which is stateless, single-threaded, and has no
      throttle, credit check or self-match prevention)?
- [ ] Are the limits under test this desk's calibrated limits, rather than the library
      defaults (size $1000$, position $5000$, daily loss $10{,}000$, collar $5\%$) — which
      are engineering placeholders, not thresholds any regulator publishes?
- [ ] Is every limit finite and positive, so a missing limit raises rather than being read
      as "unlimited" (FIA §1.1)?
- [ ] Is the ruleset actually `enabled` for the run?
- [ ] Are the limits recorded alongside the report? A green report is meaningless without
      the thresholds it was green against.

## Case coverage
- [ ] Does **every** active pre-trade rule have a case: valid, exact threshold, smallest
      breach, gross breach?
- [ ] Is the exact-threshold case asserted as **allowed**, so `>` and `>=` are
      distinguishable? (A suite of $500$ and $2000$ against a $1000$ cap passes for both.)
- [ ] Are malformed inputs covered — NaN / infinite / zero / negative quantity, non-positive
      price, NaN position, NaN daily PnL, an unrecognised `side`, negative working
      quantities?
- [ ] Is an absent or zero reference price asserted to **block** (not to skip the collar)?
- [ ] Does a position-cap case include resting orders, so the cap cannot be breached by
      several individually-compliant orders (FIA §1.2)?
- [ ] Is `required_rule_coverage` set to the rules this engine actually implements?

## Assertion strength
- [ ] Is every negative case asserted with an **exact** `expected_triggered_rules` set,
      rather than membership of one rule id? A membership assertion cannot detect a
      spurious extra rejection — the over-tight limit that rejects good flow.
- [ ] Would each regression case actually fail against the behaviour it replaced, rather
      than passing either way?
- [ ] Are expected values derived from the documented threshold convention rather than
      copied from the implementation's own arithmetic?

## Gate behaviour
- [ ] Does the pipeline branch on `report.status` only — never on `failed_tests == 0`,
      which is also `0` for an empty suite and for an all-positive suite?
- [ ] Is `RISK_TEST_SUITE_EMPTY` confirmed to fail the build?
- [ ] Is `RISK_TEST_COVERAGE_INCOMPLETE` confirmed to fail the build even at a 100% pass
      rate?
- [ ] Does a raised `ValueError`/`TypeError` from the harness fail the build rather than
      being caught and ignored?
- [ ] Is a failing risk test treated as blocking for release (FIA §5.2), with no "hotfix"
      bypass — RTS 6 Art. 11(1) puts a designated reviewer in front of material changes?

## Latency
- [ ] Are p50/p99 reviewed as an indicator rather than asserted as an SLA on a shared CI
      runner?
- [ ] If `enforce_latency_budget` is on, is it enforced against the real engine on
      representative hardware, with a budget traceable to `risk-control-latency-budget`?

## Scope boundaries acknowledged
- [ ] Are cumulative / rate-based controls tested elsewhere — 15c3-5(c)(1)(ii)'s "over a
      short period of time" limb and RTS 6 Art. 15(d) message limits are **not** covered
      here?
- [ ] Are credit/capital thresholds (15c3-5(c)(1)(i)), restricted-list and duplicate-order
      controls covered by another suite?
- [ ] Is kill functionality (RTS 6 Art. 12) tested separately —
      `kill-switch-and-drawdown-circuit-breakers`?
- [ ] Is the release record free of claims that a passing gate evidences RTS 6 or SEC Rule
      15c3-5 conformance? (15c3-5 contains no algorithm-testing mandate at all.)
- [ ] Is disorderly-trading testing (RTS 6 Art. 5(4)(d)) covered by a dynamic testing
      environment rather than by this unit suite?

## Audit trail
- [ ] Is the full `RiskControlTestExecutionReport` persisted against the change record,
      including `ruleset_id`, `rules_exercised`, `missing_rule_coverage` and per-case
      `actual_triggered_rules`?
- [ ] Has the change been reviewed and authorised by the designated person before
      deployment (RTS 6 Art. 5(2), Art. 11(1)), and communicated to the compliance and risk
      functions (Art. 11(2))?
