---
name: new-strategy-onboarding-checklist
description: >-
  Use when deciding whether a newly researched strategy may be put in front of live capital, evaluating four conjunctive governance gates (Backtest Robustness, Operational Runtime, Model Risk, Compliance Sign-off) against recorded thresholds and emitting an auditable pass/reject record.
domain: Portfolio Multi Strategy
subdomain: Strategy Lifecycle Governance & Production Onboarding
tags: ["onboarding", "strategy-governance", "gatekeeper", "model-risk", "paper-trading", "compliance-approval", "production-readiness"]
brokers_frameworks: ["MiFID II RTS 6 (EU) 2017/589", "SEC Rule 15c3-5", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when promoting a new quantitative strategy from R&D backtesting into live capital deployment. Deploying an unverified strategy risks severe capital loss through over-fitted backtests, execution bugs, missing kill switches, or regulatory non-compliance. This engine enforces a **Four-Gate Governance Check**, all four conjunctive:

1. **Backtest & Robustness Gate**: walk-forward score $\ge 0.70$, $\ge 3$ market regimes covered, backtest Sharpe $\ge 1.5$.
2. **Operational Runtime Gate**: $\ge 14$ days paper trading, 0 critical execution errors, kill switch integrated.
3. **Model Risk Gate**: a completed model card exists.
4. **Compliance & Legal Gate**: compliance sign-off is recorded.

The value it adds is not arithmetic — four boolean comparisons need no engine. It is that the four claims are captured **together**, against **thresholds recorded in the report itself**, in a record you can hand to a reviewer six months later and reproduce.

## When NOT to Use

- **As verification of anything.** Every field is an **attestation**. `model_card_completed=True` means somebody typed True. The engine never opens the model card, re-runs the backtest, reads the paper-trading logs, or contacts compliance. If the strategy author populates the whole payload, the gate certifies the author's own opinion of their own strategy — supply each flag from whoever owns that control.
- **As the deployment authorisation.** For an EU or UK investment firm, MiFID II RTS 6 Article 5(2) requires that "[a] person designated by the senior management of the investment firm shall authorise the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy." This report is an input to that human decision, never a replacement for it.
- **As a pre-trade control.** `ONBOARDING_PASSED` allocates no capital, sets no limit and blocks no order. The controls that actually bound a live strategy are RTS 6 Article 8 predefined deployment limits and, for a US broker-dealer with market access, the pre-set credit/capital thresholds of SEC Rule 15c3-5(c)(1)(i).
- **As evidence of edge.** The Sharpe floor screens out visibly broken strategies. Selecting on an *in-sample* Sharpe rewards the most over-fitted candidate in the pipeline — see Common Pitfalls.
- **For sizing the initial live allocation.** Passing this gate says "may begin"; how much capital and on what ramp is `incremental-capital-deployment-for-new-strategies`.
- **For re-validating a strategy already live.** This engine is stateless and has no notion of a second audit. Decay and retirement are `strategy-performance-decay-detection-vs-market-wide-decay` and `strategy-lifecycle-retirement-criteria`.

## Prerequisites

- Strategy onboarding payload (`strategy_id`, `strategy_name`, `author`, `walk_forward_score`, `regimes_covered`, `backtest_sharpe`, `paper_trading_days`, `paper_trading_errors`, `kill_switch_integrated`, `model_card_completed`, `compliance_approved`).
- Onboarding policy config (`min_walk_forward_score`, `min_regimes_covered`, `min_backtest_sharpe`, `min_paper_trading_days`, `max_paper_trading_errors`).
- **Four caller conventions the engine cannot verify:**
  - The three attestation flags must be real `bool` values. Strings, `0`/`1` and `None` are rejected rather than coerced — `bool("false")` is `True`, and that is exactly the payload an agent or a CSV import produces.
  - `walk_forward_score` has no scale imposed here. Whatever convention produced the number must be the convention that set the threshold.
  - `paper_trading_days` may be calendar or trading days — pick one and keep it. 14 calendar days is roughly 10 trading days.
  - `paper_trading_errors` is only as trustworthy as the error detection that produced it. Zero from a system that never logged errors is indistinguishable from a genuinely clean run.
- Thresholds you are willing to defend. **The defaults are house heuristics, not standards** — no regulator mandates a paper-trading duration or a performance metric. See `references/standards.md`.

## Workflow

1. **Validate the package before trusting any gate**:
   - Non-`bool` attestation flags, non-finite metrics, negative counts and blank identifiers raise `ValueError` — the audit does not return a verdict on corrupt input.
   - **Decision point — corrupt input is a data failure, not a strategy failure.** Every gate is a threshold comparison and every comparison against `NaN` is `False`, so a `NaN` walk-forward score used to surface as `ONBOARDING_REJECTED`: indistinguishable from a genuinely weak strategy, and routed to the wrong team. An *infinite* Sharpe is worse — it is what a zero-variance return series produces, i.e. a broken backtest, and it clears any finite floor.
2. **Gate 1 — Backtest Robustness Audit**:
   - Verify walk-forward score $\ge$ threshold, regimes covered $\ge$ threshold, Sharpe $\ge$ threshold. Thresholds are inclusive floors: exactly 0.70 passes, 0.69 does not.
   - **Decision point — read `failed_criteria`, not just `passed`.** The gate bundles three criteria; "BACKTEST_GATE failed" is the difference between a Sharpe of 1.49 and a strategy that saw one market regime.
3. **Gate 2 — Operational & Paper Trading Audit**:
   - Verify paper-trading duration $\ge$ threshold, errors $\le$ threshold, kill switch integrated.
   - **Decision point — duration is not coverage.** Fourteen quiet days prove the plumbing works, not that the strategy survives stress. Regime coverage of the *backtest* is Gate 1; regime coverage of the *paper period* is measured by nothing here. If the paper window was calm, say so in the sign-off rather than treating the elapsed day count as the finding.
4. **Gate 3 — Model Risk Documentation Audit**:
   - Verify a completed model card exists. **Existence only** — whether the card documents parameter limits, decay conditions and failure modes is a human review question against `model-card-documentation-for-trading-models`.
5. **Gate 4 — Compliance & Governance Sign-Off**:
   - Confirm compliance sign-off is recorded. Recording a sign-off is not the same as obtaining the RTS 6 Article 5(2) senior-management authorisation.
6. **Audit Report Generation**: output the structured `OnboardingAuditReport` **and persist it**.
   - **Decision point — the verdict is meaningless without `policy_applied`.** A config of zeros passes every strategy and emits the identical `ONBOARDING_PASSED` string as the strict default. The report therefore embeds the thresholds actually applied, and `policy_weakened` names any threshold set below the shipped default. A stored verdict without its policy snapshot is not an audit record.
   - **Decision point — the gates are conjunctive, never scored.** `total_gates_passed` is a diagnostic. 3/4 is exactly as rejected as 0/4; there is no waiver path and no override flag, deliberately.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passing a string where a boolean belongs.** Every non-empty string is truthy in Python. Before validation existed, `compliance_approved="false"` with `model_card_completed="NO"` returned `ONBOARDING_PASSED` and left the string `'NO'` sitting in the report's boolean `passed` field. Any payload assembled from CSV, JSON, YAML or by an LLM agent is a candidate for exactly this.
- **Treating the backtest Sharpe floor as a quality bar.** Raising `min_backtest_sharpe` selects harder on an *in-sample* statistic: the maximum over many research trials exceeds the mean by construction, and the overstatement grows with trial count, so a higher floor preferentially admits the most over-fitted candidate. Use it as a floor against visibly broken strategies and correct for trial count downstream — `factor-research-multiple-testing-correction`, or the Deflated Sharpe Ratio.
- **Letting the strategy author populate the whole payload.** Self-attestation across all four gates makes the record a formality. Each flag should come from the control owner: risk for the kill switch, model risk for the card, compliance for the sign-off.
- **Storing the verdict without the policy.** `ONBOARDING_PASSED` from a zeroed config is byte-identical to `ONBOARDING_PASSED` from the strict default. Persist `policy_applied` with every verdict or the audit trail proves nothing.
- **Bypassing paper trading.** Deploying a backtested strategy straight to live capital because the backtest looked convincing.
- **Accepting a kill-switch attestation that was never exercised.** A kill switch that has never been triggered in this deployment has been integrated, not proven — see `paper-to-live-promotion-checklist` and `position-limit-breach-simulation-fire-drills`.
- **Reading `total_gates_passed` as a score.** "It's at 3 out of 4, close enough to ship" is the failure mode a gate exists to prevent.
- **Treating an onboarding pass as an ongoing licence.** The audit is a point-in-time snapshot with no expiry and no re-audit tracking; a substantial update to the strategy is a new deployment under RTS 6 Article 5, not a continuation of this one.

## Verification

- Instantiate `NewStrategyOnboardingEngine()` and audit the compliant fixture (WF 0.82, 4 regimes, Sharpe 2.1, 20 paper days, 0 errors, all flags `True`) $\implies$ `ONBOARDING_PASSED`, `total_gates_passed` 4, `failed_gates` empty, and `type(gate.passed) is bool` on every gate.
- Audit the same package with `paper_trading_days=5` and `compliance_approved=False` $\implies$ `ONBOARDING_REJECTED`, `total_gates_passed` 2, `failed_gates == ["OPERATIONAL_GATE", "COMPLIANCE_GATE"]`, and `audit_notes` containing `paper_trading_days: 5 < 14`.
- Boundary checks: exactly 0.70 / 3 regimes / 1.50 / 14 days / 0 errors passes; 0.69, 2 regimes, 1.49, 13 days and 1 error each fail — and each fails *only* the gate it belongs to.
- Conjunctivity: `kill_switch_integrated=False` alone $\implies$ 3/4 passed and still `ONBOARDING_REJECTED`.
- Negative checks that must **raise** `ValueError`: any attestation flag as `"yes"`, `"false"`, `1`, `0`, `None` or a list; `NaN` or $\pm\infty$ on `walk_forward_score` or `backtest_sharpe`; a negative `regimes_covered`, `paper_trading_days` or `paper_trading_errors`; `paper_trading_days=True`; a blank `strategy_id`, `strategy_name` or `author`; a config with a negative or `NaN` threshold; a non-`bool` flag assigned *after* construction and only then audited.
- Auditability: a config of `min_walk_forward_score=0.0, min_regimes_covered=0, min_backtest_sharpe=0.0, min_paper_trading_days=0, max_paper_trading_errors=99` must still return `ONBOARDING_PASSED` for an unfit strategy, with all five thresholds named in `report.policy_weakened` and recorded in `report.policy_applied`. A tightened config must report `weakened_thresholds() == []`.
- Run `python test_new_strategy_onboarding_checklist.py` from the `scripts/` directory and confirm a 100% pass rate.

## Related Skills

- `paper-to-live-promotion-checklist`
- `incremental-capital-deployment-for-new-strategies`
- `model-card-documentation-for-trading-models`
- `strategy-research-to-production-pipeline-governance`
- `multi-year-regime-coverage-requirement`
- `kill-switch-and-drawdown-circuit-breakers`
- `factor-research-multiple-testing-correction`
