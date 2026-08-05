---
name: risk-control-unit-testing-framework
description: >-
  Dedicated unit testing framework engine for pre-trade and intra-trade risk rules executing automated scenario suites (positive orders, limit breaches, boundary edge cases, price collars, daily loss limits) and asserting deterministic risk behavior.
domain: Risk Management & Software Engineering
subdomain: Automated Testing & Risk Verification
tags: ["risk-testing", "unit-test-harness", "pre-trade-risk", "limit-breach", "fat-finger-collar", "automated-testing"]
brokers_frameworks: ["Risk Control Unit Testing Harness", "Python Dataclasses", "Unittest"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing, refactoring, or auditing pre-trade risk controls (order size caps, position limits, fat-finger price collars, daily loss limits, credit thresholds). Pre-trade risk engines protect trading firms from catastrophic losses; software bugs in risk rules can cause market disruption or bankruptcy. Regular software unit test frameworks often fail to simulate stateful order accumulation, price collar bounds, or ultra-low-latency assertions. This dedicated framework executes automated risk scenario test suites and asserts deterministic risk behavior.

## Prerequisites

- Pre-trade risk configuration (`max_order_size`, `max_position_size`, `max_daily_loss_usd`, `max_price_collar_pct`).
- Proposed order structure (`order_id`, `symbol`, `side`, `quantity`, `price`, `current_mid_price`, `current_position`, `accumulated_daily_pnl_usd`).

## Workflow

1. **Scenario Test Suite Construction**:
   - Construct positive test cases (valid orders that MUST pass).
   - Construct negative test cases (orders breaching max size, position cap, price collar, or daily loss limit).
   - Construct boundary edge cases (orders at exact threshold limits).
2. **Order Evaluation**:
   - Pass order through `PreTradeRiskEngine` and capture `RiskCheckResult` (allowed status, triggered rules, latency).
3. **Assertion & Test Verification**:
   - Assert actual risk decision matches expected allowed state and specific triggered rule IDs.
4. **Execution Report Generation**: Output structured `RiskControlTestExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing Risk Rules Only in Backtests**: Relying on backtests instead of isolated unit tests allows subtle rule implementation bugs to reach production.
- **Ignoring Boundary Limits**: Testing only 500 qty vs 1000 max, without testing 1000 (exact limit) and 1001 (breach edge).
- **Unchecked Latency Regressions**: Failing to assert that risk rule evaluation completes within microsecond SLA budgets.

## Verification

- Instantiate `RiskControlUnitTestFrameworkEngine`. Execute `run_standard_suite()` $\implies$ verify 100% pass rate (`ALL_RISK_TESTS_PASSED`) across normal orders, max size breaches, position cap breaches, fat-finger collars, and daily loss limits. Run single test case with mismatched expectation $\implies$ verify test failure correctly flagged.
- Run `python scripts/test_risk_unit_test_harness.py`.

## Related Skills

- `risk-control-bypass-audit-logging`
- `execution-algorithm-regression-testing-suite`
---
