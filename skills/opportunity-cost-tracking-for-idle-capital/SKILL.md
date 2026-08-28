---
name: opportunity-cost-tracking-for-idle-capital
description: >-
  Opportunity cost tracking engine measuring idle-capital return drag as the spread between a money-market benchmark (SOFR, ACT/360) and the yield the cash already earns, sizing the sweepable balance net of an operational buffer, and testing whether a cash sweep clears its round-trip cost.
domain: Treasury Management & Multi-Strategy
subdomain: Capital Allocation & Cash Sweep Optimization
tags: ["opportunity-cost", "idle-capital", "treasury-management", "cash-sweep", "sofr-benchmark", "act-360-day-count", "return-drag", "capital-allocation"]
brokers_frameworks: ["SOFR / US Treasury Benchmark", "FRBNY SOFR Averages & Index", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-strategy portfolios, fund treasuries, or crypto trading accounts where unallocated cash sits earning less than it could. Idle capital is a hidden drag on portfolio Sharpe and total return. This engine computes the idle capital ratio ($\text{IdleCash} / \text{TotalCapital}$), measures the opportunity cost drag as the **spread between a money-market benchmark and the yield the cash already earns**, and tests whether sweeping the non-buffer balance clears its round-trip transaction cost.

## When NOT to Use

- **When the cash balance is negative.** A negative balance is a margin debit — a borrowing cost, not an idle-capital opportunity cost. The engine raises rather than reporting a negative drag. See `margin-utilization-circuit-breaker` and `broker-margin-interest-accrual-tracking`.
- **As a yield forecast or a P&L projection.** SOFR measures the cost of borrowing cash overnight collateralized by Treasuries; it is a reference rate, not an instrument you can buy. A real sweep lands in a money-market fund, a T-bill ladder, or a broker credit-interest program, each yielding *near but not equal to* the benchmark, with its own fees and settlement lag. Treat the output as a decision threshold.
- **As a liquidity plan.** The engine does not model redemption timing. Cash swept into a T+1 money-market fund is not available for an intraday margin call. That risk is priced by `operational_buffer_usd`, and setting it to zero asserts you have none.
- **For multi-currency cash.** One benchmark rate is applied to one pool. Per-currency balances need per-currency rates — see `multi-currency-pnl-and-fx-conversion`.
- **For after-tax decisions.** Sweep yield is generally taxable income; the net gain reported here is pre-tax.

## Prerequisites

- Portfolio capital state (`total_capital`, `allocated_capital`, `unallocated_cash`, `benchmark_rate_pct`, `holding_period_days`, `cash_yield_pct`). `allocated_capital + unallocated_cash` must reconcile to `total_capital`.
- A **benchmark rate observed for the relevant date** — there is no default. Pull SOFR from the Federal Reserve Bank of New York's daily publication.
- The rate the idle cash **already earns** (`cash_yield_pct`) — broker credit interest or the current sweep yield. Leaving it at $0.0$ asserts the cash earns nothing.
- Sweep policy config (`min_sweep_threshold_usd`, `sweep_transaction_cost_usd` as the **all-in round-trip** cost, `target_idle_ratio_max` as a *fraction*, `operational_buffer_usd`, `day_count`).

## Workflow

1. **Idle Capital Ratio & Drag Calculation**:
   - Reconcile capital first: if $\text{Allocated} + \text{Unallocated} \ne \text{Total}$, the idle ratio is not interpretable and the audit must stop.
   - Compute Idle Capital Ratio: $\text{IdleRatio} = \text{UnallocatedCash} / \text{TotalCapital}$.
   - **Decision point — net the yield already earned.** Opportunity cost is the *foregone* yield, not the whole benchmark:
     $$r_{\text{net}} = r_{\text{benchmark}} - r_{\text{cash}}$$
     Cash earning $4.80\%$ against a $5.25\%$ benchmark has a $45$ bp drag, not $525$ bp. Charging the full benchmark overstates the case for sweeping by more than an order of magnitude.
   - **Decision point — pick the day count from how the rate is quoted, not by habit.** SOFR and T-bills are quoted **ACT/360**; using $\text{Days}/365$ understates the drag by $365/360 - 1 = 1.39\%$. SONIA and coupon Treasury yields are ACT/365F.
     $$\text{GrossDrag}_{\text{USD}} = \text{UnallocatedCash} \times \frac{r_{\text{net}}}{100} \times \frac{\text{Days}}{\text{Basis}}$$
   - Report period and annualized drag **separately**: $\text{Drag}^{\text{period}}_{\text{bps}} = (\text{GrossDrag} / \text{TotalCapital}) \times 10{,}000$ scales with the holding period; $\text{Drag}^{\text{ann}}_{\text{bps}} = \text{IdleRatio} \times (r_{\text{net}}/100) \times 10{,}000$ does not. Conflating them misstates the drag by the ratio of the horizon to a year.
2. **Net Yield & Cash Sweep Optimization**:
   - **Decision point — only the non-buffer balance is sweepable.** $\text{Sweepable} = \max(0, \text{UnallocatedCash} - \text{OperationalBuffer})$. The buffer still costs yield, so it stays in the reported drag, but it must never enter the sweep decision.
   - $\text{NetYieldGain} = \text{Sweepable} \times \text{PeriodYield} - \text{SweepCost}_{\text{round-trip}}$.
   - Report $\text{Breakeven} = \text{SweepCost} / \text{PeriodYield}$ — the balance below which the sweep destroys value regardless of policy thresholds.
   - Sweep only if $\text{Sweepable} \ge \text{MinSweepThreshold}$ **and** $\text{NetYieldGain} > 0$ **and** $r_{\text{net}} > 0 \implies$ `SWEEP_TO_YIELD_BENCHMARK`. Otherwise `MAINTAIN_IDLE_CASH` with a `sweep_blocked_reason` naming which gate failed.
3. **Threshold Compliance Audit**:
   - Flag `IDLE_CAPITAL_RATIO_EXCEEDED` if $\text{IdleRatio} > \text{TargetIdleRatioMax}$ (strict $>$: exactly at the cap is not a breach).
4. **Audit Report Generation**: Output structured `OpportunityCostReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Charging the full benchmark as the drag**: idle cash in a brokerage account is rarely earning zero. Subtracting the credit interest it already receives is the difference between a marginal decision and an obvious one; skipping it manufactures a case for sweeping that the arithmetic does not support.
- **Accruing a SOFR-quoted rate over $\text{Days}/365$**: SOFR, the SOFR Averages, the SOFR Index, and T-bills are all quoted ACT/360. The $365$ denominator understates every drag figure by $1.39\%$ — small per period, systematic across every audit.
- **Reporting period drag as if it were annualized**: $8.75$ bps over $30$ days is $105$ bps annualized. Presenting the period figure under an "annualized" label understates the drag by $12\times$ at a monthly horizon.
- **Sweeping the operational buffer**: sweeping $100\%$ of cash leaves nothing liquid for a margin call or a settlement obligation, and a T+1 money-market redemption does not arrive in time for an intraday call. Reserve the buffer explicitly rather than assuming a rounding margin will cover it.
- **Counting only one leg of the sweep**: a sweep is a round trip. Charging the outbound fee alone makes marginal sweeps look profitable when the return leg is what tips them negative. `sweep_transaction_cost_usd` must be the all-in round-trip cost.
- **Treating a negative cash balance as idle capital**: a margin debit produces a negative idle ratio and a negative drag, which reads as a *surplus*. The account paying borrowing interest is the one case where "idle capital healthy" is most wrong.
- **Hardcoding a static benchmark rate**: a rate baked into a default silently accrues against a level that may not have been current for years. `benchmark_rate_pct` is deliberately required, with no default.
- **Passing a fraction where a percentage is expected**: `benchmark_rate_pct=0.0525` instead of `5.25` understates the drag $100\times$ and produces a confident `MAINTAIN_IDLE_CASH`. Likewise `target_idle_ratio_max=5` instead of `0.05` disables the idle-ratio alert entirely — both are rejected or warned on.

## Verification

- Instantiate `OpportunityCostTrackerEngine()`. Input \$10M total capital with \$2M idle cash ($20\%$ idle ratio) over $30$ days at a $5.25\%$ benchmark and $0\%$ cash yield: verify gross drag $= \$8{,}750.00$ (ACT/360, i.e. $2{,}000{,}000 \times 0.0525 \times 30/360$), period drag $= 8.75$ bps, annualized drag $= 105.00$ bps, and recommendation `SWEEP_TO_YIELD_BENCHMARK`.
- Set `cash_yield_pct=4.80` on the same state: verify the net spread is $0.45\%$ and gross drag falls to $\$750.00$ — the netting is not cosmetic.
- Set `day_count=DayCount.ACT_365F`: verify the drag returns to the $\$8{,}630.14$ ACT/365 figure, confirming the convention is what moved it.
- Set `operational_buffer_usd=450000` with \$500k idle: verify `sweepable_cash_usd == 50000`, `MAINTAIN_IDLE_CASH`, and `sweep_blocked_reason == "BELOW_MIN_SWEEP_THRESHOLD"`, while gross drag still reflects the full \$500k.
- Verify `breakeven_sweep_notional_usd` equals $\text{cost}/\text{period yield}$, that a balance exactly at breakeven is *not* swept, and that one dollar above it is.
- Negative checks: negative `unallocated_cash`, non-finite `benchmark_rate_pct`, non-positive `holding_period_days`, unreconciled capital, `total_capital <= 0`, and `target_idle_ratio_max=5.0` must each raise.
- Run `python scripts/test_opportunity_cost_tracking_for_idle_capital.py` and confirm 100% pass rate.

## Related Skills

- `capital-reallocation-based-on-live-performance`
- `margin-utilization-circuit-breaker`
- `broker-margin-interest-accrual-tracking`
- `multi-strategy-capital-allocation-limits`
- `capital-efficiency-across-cross-margined-strategies`
