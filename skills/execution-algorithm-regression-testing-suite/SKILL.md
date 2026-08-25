---
name: execution-algorithm-regression-testing-suite
description: >-
  Fail-closed CI/CD quality gate for execution algorithm code changes — replays a candidate build against a recorded production baseline across required stress scenarios and compares Implementation Shortfall, fill-rate ratio and peak participation before release.
domain: Execution Algorithms
subdomain: CI/CD Quality Gates & Regulatory Testing
tags: ["regression-testing", "ci-cd-quality-gate", "execution-algo", "implementation-shortfall", "backtesting-suite", "mifid-ii-rts-6", "fail-closed-gate"]
brokers_frameworks: ["MiFID II RTS 6 (EU 2017/589)", "FINRA Regulatory Notice 15-09", "FCA Algorithmic Trading Compliance (Feb 2018)", "Python Dataclasses", "CI/CD Pipeline Gates"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a change to an execution algorithm (TWAP, VWAP, POV, Implementation Shortfall) is about to be promoted to production and you need a machine-checkable answer to one question: **did this build get measurably worse than the baseline on the scenarios we replayed?** The engine replays baseline and candidate over a required set of market scenarios, compares $\Delta \text{IS}$, the fill-rate ratio and peak participation against configured thresholds, and returns `PASS_REGRESSION_APPROVED` / `FAIL_REGRESSION_REJECTED` plus an auditable `RegressionTestSuiteAuditReport` you can attach to a release record.

MiFID II **RTS 6 Article 5(1)** requires clearly delineated development and testing methodologies *prior to the deployment or substantial update* of a trading algorithm, and **Article 5(5)** requires further testing after substantial changes. A performance-regression gate is one component of that methodology, and the report it emits is the kind of artefact a venue asks for when it requires members to certify pre-deployment testing and **explain the means used** (RTS 7 Art. 10).

## When NOT to Use

- **As evidence that the algorithm does not contribute to disorderly trading conditions.** RTS 6 Art. 5(4)(d) requires exactly that, and this gate does not measure it. The FCA names the shortcut explicitly as *poor practice*: "Firms who conduct basic testing of their algorithmic trading strategies which only assess operational efficiency and focus on considerations such as their performance against certain benchmarks or the profit and loss of the strategy. In these cases, firms are unable to demonstrate the potential impact of their algorithmic trading strategies on market integrity." A benchmark-comparison gate is that testing. Pair it with a dynamic testing environment in which the candidate interacts with other algorithms in a shared order book.
- **As a substitute for SEC Rule 15c3-5 controls.** Rule 15c3-5 requires pre-trade risk controls plus an at-least-annual review and CEO certification (§ 240.15c3-5(e)). It contains **no** pre-deployment algorithm-testing mandate — passing this gate discharges nothing under 15c3-5. See `execution-algorithm-kill-switch-integration` for the controls the rule does require.
- **As the RTS 6 Article 10 stress test.** That is an annual self-assessment obligation with prescribed magnitudes — messaging and trade-volume tests at *twice* the highest levels of the previous six months — not a per-build gate. Coverage here means "a volatility-shock scenario was replayed", not "capacity was stressed".
- **When there is no trustworthy production baseline.** Every threshold is relative to $\text{IS}_{\text{base}}$ and $\text{FillRate}_{\text{base}}$. A first-ever algorithm, or one whose baseline replay filled nothing, has nothing to regress against; the engine raises rather than inventing a denominator.
- **As a profitability or alpha test.** A build can improve shortfall and still be a worse algorithm. Only the three configured dimensions are checked.

## Prerequisites

- Recorded baseline production metrics per scenario ($\text{IS}_{\text{base}}$ in bps, $\text{FillRate}_{\text{base}} \in (0, 1]$).
- Candidate build replayed over the **same** scenarios with the same input data, in an environment separated from production (RTS 6 Art. 7(1)).
- A scenario suite covering every kind named in `required_scenario_names` — by default `NORMAL_VOLATILITY`, `VOLATILITY_SHOCK`, `LIQUIDITY_CRUNCH`.
- Thresholds calibrated and signed off by the owning desk. The shipped defaults ($+2.0\text{ bps}$, $0.98$, $0.20$) are engineering conventions, **not** regulatory limits — see `references/standards.md`.
- Sign convention: `*_is_bps` is a **cost**, so a larger value is a worse execution. Feeding a P&L-signed series inverts every verdict.

## Workflow

1. **Scenario Suite Ingestion & Dual-Version Replay**:
   - Replay baseline and candidate over identical scenario inputs. Emit one `ScenarioTestResult` per scenario with a unique `scenario_id`.
   - **Decision point — a scenario the candidate could not complete is not a scenario you may drop.** Report it with whatever metrics it produced; a suite that silently shrinks when the candidate crashes is a suite that always passes.
2. **Harness Validation (raises, does not vote)**:
   - Out-of-range fill/participation rates, blank ids, duplicate ids and non-numeric metrics raise `ValueError`/`TypeError`. These indicate the harness is mis-wired, and a mis-wired harness must not produce a verdict at all.
   - **Decision point — distinguish a broken harness from a broken candidate.** A non-finite Implementation Shortfall is treated as a *scenario failure*, not an exception, because it is a symptom of the candidate build and belongs in the audit trail.
3. **Metric Variance Audit** (per scenario, compared on exact values and displayed rounded):
   - $\Delta \text{IS} = \text{IS}_{\text{candidate}} - \text{IS}_{\text{baseline}}$; fail if $\Delta \text{IS} > \Delta\text{IS}_{\text{max}}$.
   - Fill Rate Ratio $= \text{FillRate}_{\text{candidate}} / \text{FillRate}_{\text{baseline}}$; fail if below the minimum.
   - Peak participation $\alpha_{\text{candidate}} \le \alpha_{\text{max}}$ — a liquidity-consumption bound, the class of behavioural measure that produces repeatable pass/fail results, unlike price impact.
4. **Coverage Evaluation**:
   - **Decision point — a suite missing a required scenario is rejected even when every scenario it did run passed.** `scenarios_failed_count` will be `0` while `cicd_gate_status` is `FAIL_REGRESSION_REJECTED`; read the status, never the count.
5. **CI/CD Quality Gate Evaluation**: any scenario failure, non-evaluable metric, or missing required scenario $\implies$ `FAIL_REGRESSION_REJECTED`. Otherwise `PASS_REGRESSION_APPROVED`.
6. **Audit Report Generation**: persist the `RegressionTestSuiteAuditReport` — including `worst_is_degradation_bps` and `missing_required_scenarios` — against the build version.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Letting an unevaluable metric pass the gate**: `NaN > 2.0` is `False`, and so is every other comparison against `NaN`. A candidate whose shortfall calculation broke therefore breaches no rule and is *approved* — the broken build is the one that ships. Non-finite metrics must fail closed, never abstain.
- **Rounding before comparing**: rounding $\Delta \text{IS}$ to 2 dp before testing it against a $+2.0\text{ bps}$ limit ships a $+2.004\text{ bps}$ regression. Compare exact values; round only for display, and print enough digits that the message cannot read "+2.0bps exceeds allowed max +2.0bps".
- **Flooring a zero baseline denominator**: guarding $\text{FillRate}_{\text{base}}$ with `max(1e-4, x)` turns a baseline that filled nothing into a fill ratio of thousands, which passes every threshold. An undefined ratio must raise.
- **Reading a green gate as regulatory conformance**: no regulator publishes shortfall or fill-rate benchmarks, and ESMA expressly declined to prescribe the tests firms should run (ESMA70-156-4572 ¶187–188). "Passed RTS 6 benchmarks" is a claim about something that does not exist.
- **Bypassing the suite for "hotfixes"**: RTS 6 Art. 5(5) requires further testing on substantial change; an emergency patch to a live execution algorithm is a substantial change.
- **Testing only under quiet market conditions**: the FCA's August 2025 multi-firm review found simulation testing that "lacked sophistication or did not appear to consider a wide range of market scenarios". Enforce scenario coverage in the gate itself rather than trusting the pipeline to supply it.
- **Ignoring fill completion regressions**: a candidate that improves average slippage by quietly abandoning 20% of each order looks better on $\Delta \text{IS}$ alone. The fill-rate ratio is what catches it.
- **Trusting the average**: `avg_is_degradation_bps` is an unweighted mean over scenarios and a comfortable $-0.5\text{ bps}$ average can contain a $+6.0\text{ bps}$ single-scenario blow-up. Gate per scenario and read `worst_is_degradation_bps`.
- **Mutating the evidence**: an engine that writes verdicts back into the caller's scenario objects rewrites the recorded result of an earlier run when the suite is re-run with different thresholds. Reports must carry copies.

## Verification

- Instantiate `ExecutionAlgoRegressionTestSuite(max_allowed_is_degradation_bps=2.0, min_allowed_fill_ratio=0.98, max_allowed_participation_rate=0.20)` and supply all three required scenarios. Passing candidate ($\Delta \text{IS} = +0.5, +1.0, -0.3$ bps, fills at baseline) $\implies$ `PASS_REGRESSION_APPROVED` with `avg_is_degradation_bps == 0.40` and `worst_is_degradation_bps == 1.00`. Regressed candidate ($\Delta \text{IS} = +4.5\text{ bps}$, fill ratio $0.95$) $\implies$ `FAIL_REGRESSION_REJECTED`.
- Fail-closed checks: a `NaN` or `inf` shortfall on either side, and a $+2.004\text{ bps}$ degradation against a $+2.0\text{ bps}$ limit, must each be rejected.
- Coverage check: a lone `NORMAL_VOLATILITY` scenario $\implies$ `FAIL_REGRESSION_REJECTED`, `coverage_satisfied` false, `missing_required_scenarios == ["VOLATILITY_SHOCK", "LIQUIDITY_CRUNCH"]`, `scenarios_failed_count == 0`.
- Negative checks: an empty suite, duplicate `scenario_id`, `baseline_fill_rate == 0.0`, a fill rate above $1.0$, a negative participation rate, and a non-numeric metric must each raise.
- Confirm the engine leaves caller-supplied `ScenarioTestResult` objects untouched, and that a PASS report does not assert RTS 6 conformance.
- Run `python -m unittest discover -s skills/execution-algorithm-regression-testing-suite/scripts` and confirm a 100% pass rate.

## Related Skills

- `execution-algo-parameter-optimization-via-backtest`
- `execution-slippage-attribution-timing-vs-sizing`
- `execution-algorithm-kill-switch-integration`
- `canary-releases-for-strategy-code-changes`
- `market-data-replay-harness-for-integration-testing`
