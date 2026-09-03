---
name: incremental-capital-deployment-for-new-strategies
description: >-
  Portfolio risk management engine implementing 4-tier stage-gated capital ramp-up (Paper -> 10% Seed -> 50% Scale -> 100% Full) with realized Sharpe and drawdown promotion gates, one-step maintenance demotion, and an emergency drawdown deactivation gate.
domain: Portfolio Multi-Strategy
subdomain: Strategy Onboarding & Stage-Gated Scaling
tags: ["capital-deployment", "stage-gated-scaling", "strategy-onboarding", "portfolio-risk", "drawdown-limits", "sharpe-ratio-gate"]
brokers_frameworks: ["Portfolio Multi-Strategy Engine", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when onboarding newly researched quantitative trading strategies into live production. Allocating 100% target capital to an unproven strategy risks severe drawdowns from unexpected execution slippage, regime shifts, or overfitting. This module enforces a **4-Tier Stage-Gated Ramp-up Framework** (Tier 0 Sandbox 0% -> Tier 1 Seed 10% -> Tier 2 Scale 50% -> Tier 3 Full 100%), evaluating realized live Sharpe ratios, drawdowns, and execution slippage to govern promotion, retention, and demotion decisions.

## When NOT to Use

- **As a kill switch or a position-flattening control.** This engine decides a capital *entitlement*. `EMERGENCY_DEACTIVATED` sets that entitlement to $0; it does not cancel orders, flatten positions, or halt the strategy process. Wire it to `kill-switch-and-drawdown-circuit-breakers`, which must remain structurally independent of strategy logic.
- **As evidence that a strategy has edge.** The Sharpe gates are floors that exclude visibly broken strategies, not statistical proof. At the Tier 1 gate (30 daily observations) the standard error of the annualized Sharpe is ~2.90 against a threshold of 1.0 — see Workflow step 3. A strategy passing the gate is "not obviously broken", never "validated".
- **As the pre-trade control that enforces the allocation.** The report is an input to order sizing and to a pre-trade capital check (SEC Rule 15c3-5(c)(1)(i) for US broker-dealers), not a substitute for one. Nothing here prevents a strategy from trading beyond its tier.
- **For allocating across a portfolio of strategies.** Each strategy is evaluated independently against its own target capital. Summing tier allocations can exceed account equity and ignores correlation — see `multi-strategy-capital-allocation-limits` and `capital-reallocation-based-on-live-performance`.
- **To decide whether a strategy is worth running at all.** Ramp-down of a decayed strategy is `strategy-lifecycle-retirement-criteria`; first-time production readiness is `new-strategy-onboarding-checklist` and `paper-to-live-promotion-checklist`.

## Prerequisites

- Strategy state (`strategy_id`, `current_tier`: 0, 1, 2, 3, `days_in_tier`, `realized_sharpe`, `realized_max_drawdown_pct`, `slippage_vs_backtest_ratio`, `target_full_capital_usd`, `execution_errors_in_tier`).
- Target full production capital USD (e.g. $1,000,000$).
- Stage-gated promotion rules (`tier1_min_days = 30`, `tier1_min_sharpe = 1.0`, `max_allowed_dd = 12.0%`).
- **Three caller conventions the engine cannot verify.** Getting any of them wrong inverts the safety logic:
  - `realized_max_drawdown_pct` is a **positive magnitude in percent** (`4.5`, never `-4.5`), measured over the **current tier's window only** — the same window `days_in_tier` counts. A since-inception drawdown ratchets: a running maximum never decreases, so a strategy that once breached could never be re-promoted.
  - `days_in_tier` **resets to 0 on every tier change**. The report returns `next_days_in_tier` for exactly this purpose — persist that value.
  - `realized_sharpe` and `slippage_vs_backtest_ratio` at Tier 1+ are measured on **live fills**, not paper fills.

## Workflow

1. **Validate the observation before trusting any gate**:
   - Non-finite values are rejected, not tolerated. Every gate is a threshold comparison and **every comparison against `NaN` is False** — so a `NaN` drawdown makes $\text{DD} \ge 12.0\%$ False and silently *bypasses the emergency demotion*, leaving a failing strategy at full allocation. A negative (signed) drawdown is worse: it passes every $\le$ promotion gate and fails the $\ge$ emergency gate, so a strategy in a 14.5% drawdown gets *promoted to 100% capital*.
2. **Emergency Demotion & Drawdown Audit** (resolves first, outranks everything):
   - If $\text{Realized Max DD} \ge 12.0\% \implies$ Action `EMERGENCY_DEACTIVATED` (demote to Tier 0, $0 allocation), regardless of how good every other metric is.
3. **Maintenance Audit — does the strategy still deserve the tier it already holds?** (resolves before promotion):
   - Breach $\implies$ step down **exactly one tier** (`DEMOTED_MAINTENANCE_BREACH`), not to Tier 0. Limits: Max DD $> 8.0\%$ (Tier 1), $> 10.0\%$ (Tiers 2-3), or Slippage $> 2.0\times$ at any tier.
   - **Decision point — demote on drawdown and slippage, never on Sharpe.** A realized drawdown and a realized slippage ratio are facts about fills that already happened. A short-window Sharpe is not: per Lo (2002), $\text{SE}(\text{SR}_{\text{ann}}) = \sqrt{(q + \text{SR}_{\text{ann}}^2/2)/T}$, which at $T=30$ daily observations and $q=252$ is $\approx 2.90$ — larger than the 1.0 threshold itself. De-risking on that number thrashes capital between tiers on noise.
   - Maintenance limits are deliberately **looser than the entry gate above them** (enter Tier 2 at DD $\le 5\%$, leave Tier 1 at DD $> 8\%$). That hysteresis band is what stops a strategy oscillating across the boundary it just cleared.
4. **Stage-Gated Promotion Evaluation** (at most one tier per evaluation):
   - **Tier 0 -> Tier 1**: $\ge 14$ paper trading days, Max DD $\le 5.0\%$, and **0 execution crashes**. Allocates 10% capital. This is the transition that first commits *real* capital, so it is screened, not a pure elapsed-time check. Paper Sharpe is deliberately *not* gated — it is not evidence of live edge.
   - **Tier 1 -> Tier 2**: $\ge 30$ live days, Realized Sharpe $\ge 1.0$, Max DD $\le 5.0\%$, Slippage $\le 1.5\times$. Allocates 50% capital.
   - **Tier 2 -> Tier 3**: $\ge 60$ live days, Realized Sharpe $\ge 1.2$, Max DD $\le 8.0\%$, Slippage $\le 1.5\times$. Allocates 100% capital.
   - **Decision point — when promotion is blocked, read `failed_gates`, not just the status.** It names every failing condition (`min_days_in_tier: 15 < 30`), which is the difference between "three days short" and "the Sharpe is 0.2".
5. **Allocated Capital Calculation**:
   - $\text{Allocated USD} = \text{Target Full USD} \times \text{Tier Allocation Pct}$.
6. **Audit Report Generation**: Output structured `IncrementalDeploymentReport`, then **persist `next_days_in_tier`** and record the decision. In an EU/UK regulated firm, a tier change is a change to strategy exposure requiring authorisation by a person designated by senior management (MiFID II RTS 6 Article 5) — treat the report as a recommendation for authorisation, not an auto-executing capital change.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A `NaN` drawdown silently disarming the kill gate**: `float('nan') >= 12.0` is False, so the emergency branch never fires and the strategy keeps its full allocation on unusable data — the largest possible position justified by the worst possible input. Reject non-finite values at construction; never let them reach a comparison.
- **Passing a signed drawdown**: if your risk system reports drawdown as $-14.5\%$, feeding it here passes every $\le$ gate and fails the $\ge$ gate, so the engine *promotes* a strategy that should be deactivated. The convention is a positive magnitude, and violating it is silent.
- **Carrying `days_in_tier` across a promotion**: a 70-day *paper* record left un-reset satisfies the 30-day *live* gate, so the strategy jumps Tier 0 -> 1 -> 2 in two evaluations having never traded a single live day at Tier 1. Persist `next_days_in_tier`.
- **Reading a passing Sharpe gate as demonstrated edge**: with a standard error near 2.90 at 30 days, a realized Sharpe of 1.4 sits ~0.14 standard errors above a 1.0 threshold. Reaching $\pm 1.0$ precision at 95% confidence needs ~1,010 daily observations (about four years). Check `sharpe_gate_conclusive` before writing "validated" in an allocation memo.
- **Comparing drawdowns across tiers of different lengths**: for a driftless process the expected running maximum drawdown grows roughly with $\sqrt{\text{window}}$, so a 30-day Tier 1 window observes a systematically *smaller* max drawdown than a 60-day Tier 2 window on the identical strategy. A fixed emergency limit is therefore most permissive early in the ramp, exactly when the track record is weakest.
- **Immediate 100% Capital Allocation**: allocating 100% target capital on Day 1 of live trading, suffering immediate drawdown during unexpected execution anomalies.
- **Ignoring Live Slippage Discrepancies**: promoting a strategy whose live slippage exceeds backtest estimates by $3\times$. Note the 50% $\to$ 100% step is the largest single capital increase in the ladder and needs a slippage gate at least as strict as the step below it.
- **Relying on a single cliff-edge limit**: a strategy sitting at 11.9% drawdown against a 12% emergency limit holds full capital right up to the moment it holds none. Graduated one-tier step-downs de-risk before the cliff.
- **Treating the entitlement as an enforcement**: the report says how much capital a strategy *may* use. Something else must actually stop it trading more.

## Verification

- Instantiate `IncrementalCapitalDeploymentEngine`. Test Tier 1 Strategy (35 days in Tier 1, Realized Sharpe 1.4, Max DD 3.2%, Slippage 1.1x, Target $1M) $\implies$ verify engine promotes to `TIER_2_SCALE` allocating $500,000 (50%), with `next_days_in_tier == 0`. Test Drawdown Breach (Max DD 14.5% > 12.0%) $\implies$ verify engine triggers `EMERGENCY_DEACTIVATED` to Tier 0 ($0 allocation).
- Verify `annualized_sharpe_standard_error(1.50, 60, periods_per_year=1) == 0.188` and `(3.00, 60, periods_per_year=1) == 0.303`, reproducing Lo (2002) Table 1, and `required_observations_for_sharpe_precision(0.5, 1.0) == 1010`.
- Negative checks — each must raise `ValueError`, not allocate: a `NaN` or infinite drawdown/Sharpe/slippage/capital, a negative (signed) drawdown, `current_tier` outside $\{0,1,2,3\}$, negative `days_in_tier`, negative `target_full_capital_usd`, and a maintenance limit configured at or above the emergency limit.
- Boundary checks: DD exactly $12.0\%$ deactivates (inclusive); DD exactly at a maintenance limit retains (exclusive); `days_in_tier` exactly at the gate promotes (inclusive).
- Run `python -m unittest discover -s skills/incremental-capital-deployment-for-new-strategies/scripts` and confirm 100% pass rate.

## Related Skills

- `new-strategy-onboarding-checklist`
- `paper-to-live-promotion-checklist`
- `strategy-capacity-estimation-before-scaling-capital`
- `multi-strategy-capital-allocation-limits`
- `capital-reallocation-based-on-live-performance`
- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-lifecycle-retirement-criteria`
