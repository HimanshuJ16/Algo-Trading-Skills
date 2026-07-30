---
name: monte-carlo-strategy-robustness-testing
description: Use when validating trading strategy robustness to run Monte Carlo trade
  sequence shuffling, bootstrap resampling, and price noise perturbations to compute
  Risk of Ruin and 95th percentile drawdown limits
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- monte-carlo
- risk-of-ruin
- robustness-testing
- bootstrap-resampling
brokers_frameworks:
- Backtrader
- VectorBT
- PyPFcon
- Custom Backtesting Engines
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a quantitative backtest yields a promising equity curve, before deploying real capital. A single backtested equity curve represents only one historical path. If the sequence of winning and losing trades is shuffled, or if execution prices suffer slight slippage perturbations, a strategy may breach maximum drawdown limits or trigger a margin call. Running Monte Carlo simulations ($N \ge 1,000$) using trade sequence shuffling, bootstrap resampling, and price noise injection to calculate the $95\text{th}$ percentile Maximum Drawdown and Risk of Ruin is mandatory.

## Prerequisites

- Trade log containing historical trade P&L percentage returns or absolute P&L values.
- Initial account starting capital.
- Maximum acceptable drawdown risk limit (e.g. 20%).

## Workflow

1. **Ingest Trade P&L Series**:
   - Extract sequence of trade returns $R = [r_1, r_2, \dots, r_N]$.

2. **Execute Trade Sequence Shuffling (Resampling Without Replacement)**:
   - Perform $M=1,000$ iterations of random trade sequence permutations.
   - Reconstruct equity curves for each shuffled sequence to measure sequence-dependent drawdown risk.

3. **Execute Bootstrap Resampling (Sampling With Replacement)**:
   - Randomly sample $N$ trades with replacement $M=1,000$ times to evaluate parameter stability under varying market regimes.

4. **Inject Execution Slippage & Price Noise**:
   - Add Gaussian noise $\epsilon \sim \mathcal{N}(0, \sigma_{\text{noise}})$ to trade returns to simulate execution slippage.

5. **Calculate Risk Metrics & Risk of Ruin**:
   - Compute $95\text{th}$ percentile Max Drawdown ($DD_{95}$).
   - Compute Risk of Ruin: $P(\text{Max Drawdown} \ge \text{Risk Limit})$.
   - A strategy passes sign-off if $DD_{95} \le \text{Risk Limit}$ and $\text{Risk of Ruin} \le 1.0\%$.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on a Single Equity Curve Path**: Assuming historical trade sequence will repeat exactly in live trading.
- **Ignoring Compounding Sequence Effects**: Measuring drawdowns without compounding sequential gains and losses.
- **Under-Sampling Simulations**: Running fewer than 500 Monte Carlo iterations, resulting in noisy quantile estimates.

## Verification

- Submit historical trade P&L series to `MonteCarloRobustnessEngine` ($M=1,000$ iterations) and verify 95th percentile Max Drawdown is computed.
- Verify `calculate_risk_of_ruin()` correctly flags strategies where $> 1\%$ of simulated paths breach the 25% drawdown limit.
- Run unit test suite `python scripts/test_monte_carlo_engine.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-optimization-window-management`
- `survivorship-bias-free-universe-construction`
- `kill-switch-and-drawdown-circuit-breakers`
---
