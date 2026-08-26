---
name: monte-carlo-strategy-robustness-testing
description: Use when validating trading strategy robustness before capital deployment
  to run Monte Carlo trade sequence shuffling, IID bootstrap resampling, and execution
  noise perturbation, and to compute the 95th percentile maximum drawdown and the
  probability that a simulated path breaches the drawdown limit
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
- NumPy
- Custom Backtesting Engines
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a quantitative backtest yields a promising equity curve, before deploying real capital. A single backtested equity curve is one realized path drawn from a distribution of paths the same strategy could have produced. If the sequence of winning and losing trades is reordered, or if execution prices suffer slippage the backtest did not model, the strategy may breach its maximum drawdown limit or trigger a margin call on a path that is no less likely than the historical one. Running $M \ge 500$ simulations across trade sequence shuffling, bootstrap resampling, and execution noise injection, and reading the $95\text{th}$ percentile maximum drawdown ($DD_{95}$) and the drawdown-breach probability off the resulting distribution, is what converts one path into a risk estimate.

## When NOT to Use

- **Serially dependent trade outcomes.** Shuffling and IID bootstrap both assume trade returns are *exchangeable*. Trend-following strategies, anything sized off recent performance, and anything holding correlated concurrent positions produce clustered wins and losses. Resampling destroys that clustering and reports a drawdown that is **optimistically biased**. Use a block bootstrap that preserves runs of consecutive trades (see `synthetic-data-generation-for-backtest-augmentation`) and treat the numbers here as a floor, not an estimate.
- **Fewer than ~30 trades.** The engine's hard floor is 5 trades, but resampling cannot manufacture information the trade log does not contain. With a handful of trades the drawdown quantiles are dominated by sampling error in the trade log itself.
- **As a substitute for out-of-sample testing.** Every method here resamples the *same* trades. A strategy overfitted to its sample is overfitted in every resample of that sample. Monte Carlo tests path risk, not whether the edge is real — that is `walk-forward-validation-setup` and `backtest-parameter-sensitivity-analysis`.
- **As a substitute for a live risk control.** A $DD_{95}$ estimate is a research artifact. Enforcement at runtime belongs to `kill-switch-and-drawdown-circuit-breakers`.
- **Tail-risk sizing for fat-tailed instruments.** Bootstrap never draws a loss larger than the worst historical trade. If the strategy has not yet lived through a gap, a limit-down, or a liquidity crunch, no amount of resampling will invent one. Use `stress-testing-against-historical-crash-scenarios` alongside.

## Prerequisites

- Trade log of **fractional per-trade returns on account equity** ($0.02 = +2\%$), strictly greater than $-1.0$. Absolute currency P&L must be divided by account equity first — feeding dollars into a compounding model drives equity negative and reports meaningless drawdowns. The engine rejects both cases rather than simulating them.
- No NaN or Inf in the trade log. Resolve gaps in the log; do not simulate them.
- Initial account capital ($> 0$).
- A maximum drawdown limit, and a breach-probability ceiling reflecting the desk's risk appetite (the $1.0\%$ default below is a house convention, not a regulatory threshold).
- An execution-cost estimate calibrated from the desk's own fills, for the noise stage (see `transaction-cost-analysis-tca-integration`).

## Workflow

1. **Ingest and Validate the Trade Return Series**:
   - Extract the sequence of fractional trade returns $R = [r_1, r_2, \dots, r_N]$.
   - Reject the run if any $r_i$ is NaN, Inf, or $\le -1.0$. Do not coerce, clip, or drop such values silently: a NaN in a trade log is a data defect, and a return $\le -100\%$ is either a wiped account (ruin is already certain, no simulation needed) or a units error.
   - Record the seed. A sign-off that cannot be reproduced is not a sign-off (`backtest-determinism-and-reproducibility`).

2. **Execute Trade Sequence Shuffling (Resampling Without Replacement)**:
   - Perform $M \ge 500$ random permutations of $R$ and rebuild the compounded equity curve for each.
   - Read **drawdown only** from this stage. Terminal equity is invariant across permutations — $C \prod_i (1 + r_i)$ does not depend on order — so a "median final equity" from shuffling is a constant, not a distribution statistic. The engine flags this as `final_equity_is_path_invariant`.

3. **Execute Bootstrap Resampling (Sampling With Replacement)**:
   - Draw $N$ trades with replacement, $M$ times (Efron 1979). Each path draws a different multiset, so this stage *does* produce terminal-wealth dispersion alongside drawdown.
   - Expect a deeper tail than shuffling: bootstrap can draw the worst trade repeatedly. If bootstrap $DD_{99}$ is *not* at least as deep as shuffling $DD_{99}$, the run is suspect.

4. **Inject Execution Noise**:
   - Perturb each trade return by $\epsilon \sim \mathcal{N}(\mu_{\text{cost}}, \sigma_{\text{noise}})$, holding the original trade order fixed so execution sensitivity is isolated from sequence effects.
   - Set $\sigma_{\text{noise}}$ from measured fill dispersion, not from a round number. There is no universal slippage magnitude, which is why the engine requires it rather than defaulting it.
   - **Set $\mu_{\text{cost}} < 0$.** Zero-mean symmetric noise is a sensitivity test, not a slippage model: real slippage is one-sided and always costs. A strategy that survives symmetric noise but dies under a $5$ bps per-trade cost drag has not passed this stage.

5. **Read the Sign-off Metrics**:
   - Compute the $95\text{th}$ and $99\text{th}$ percentile maximum drawdown by nearest rank, so the reported figure is a drawdown some simulated path actually realized rather than an interpolation between two paths.
   - Compute the drawdown-breach probability $P(DD_{\max} \ge \text{Limit})$ — reported as `risk_of_ruin_pct` for API compatibility. Note that this is **not** classical risk of ruin: it is the probability of breaching a chosen drawdown limit, not of depleting capital.
   - Sign off only if $DD_{95} \le \text{Limit}$ **and** breach probability $\le$ the configured ceiling (default $1.0\%$), across **all three** stages. A strategy that passes shuffling and fails noise injection has an execution problem, not a robustness result.

> Full step-by-step procedure with implementation detail: see `references/workflows.md`.
> Method comparison table and source citations for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a resampled drawdown as an upper bound.** Shuffling and IID bootstrap assume exchangeability. If the strategy's losses cluster, resampling breaks the clusters apart and reports a *shallower* drawdown than the strategy will actually experience. The direction of this bias is optimistic, which is the dangerous direction for a capital-deployment gate.
- **Reading terminal wealth off a shuffling run.** Every permutation of a fixed multiset compounds to the identical terminal equity. A "median final equity" from shuffling is that constant. Comparing it against initial capital tests nothing.
- **Letting a NaN through the gate.** `float('nan')` compares False against every bound, so a naive peak/drawdown loop silently skips the NaN trade and reports a small drawdown and a passing verdict on a corrupted log. Validate at the boundary and fail closed.
- **Passing absolute P&L where fractional returns are expected.** `equity *= (1 + (-1500.0))` drives equity negative, after which "peak minus equity over peak" exceeds $100\%$ and later multiplications flip the sign. The result looks like a number and means nothing.
- **Under-sampling.** The empirical $q$-quantile of $M$ paths is an order statistic whose rank has standard error $\sqrt{M q (1-q)}$: at $q = 0.95$ that is $\pm 2.2$ ranks out of $100$ paths but $\pm 6.9$ out of $1{,}000$ — i.e. $2.2\%$ of the distribution versus $0.69\%$. Below $\sim 500$ paths $DD_{95}$ moves run to run and cannot gate a decision.
- **Seeding the global RNG.** Calling module-level `random.seed()` inside a simulation engine hijacks the caller's random stream and makes repeated runs on the same engine draw from different states. Use a private `random.Random` instance re-seeded per run, and record the seed with the result.
- **Mistaking a drawdown-limit breach for ruin.** $P(DD \ge 25\%)$ is not the probability of losing the account. Reporting it as "Risk of Ruin" to a risk committee overstates one and understates the other.
- **Symmetric noise reported as a slippage test.** Zero-mean Gaussian perturbation is as likely to improve a fill as to worsen it, so the median edge survives almost unchanged. Charge a one-sided cost.
- **Running Monte Carlo on an already-overfitted trade log.** Resampling an overfitted sample produces confident-looking quantiles around an edge that does not exist out of sample.

## Verification

- Submit a historical fractional trade return series to `MonteCarloRobustnessEngine` ($M \ge 500$) and confirm `run_sequence_shuffling`, `run_bootstrap_resampling`, and `run_noise_injection` each return $DD_{95}$, $DD_{99}$, and a breach probability.
- Verify the gate fails closed: a trade log containing NaN, Inf, or a return $\le -1.0$ must raise `MonteCarloError`, never return `is_robust=True`.
- Verify permutation invariance is surfaced: `run_sequence_shuffling(...).final_equity_is_path_invariant` must be `True` and `median_final_equity` must equal $C \prod_i (1 + r_i)$.
- Verify reproducibility: two consecutive runs on the same engine and seed must return equal results, and the caller's module-level `random` stream must be unchanged after a run.
- Verify `run_noise_injection(..., noise_std=0.0)` reproduces the unperturbed path exactly, and that a negative `mean_shift` lowers median terminal equity.
- Verify the breach-probability ceiling is honoured: the same distribution must pass at a lenient `max_risk_of_ruin_pct` and fail at a strict one.
- Run unit test suite `python -m unittest discover -s skills/monte-carlo-strategy-robustness-testing/scripts` and confirm 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `walk-forward-optimization-window-management`
- `backtest-parameter-sensitivity-analysis`
- `backtest-determinism-and-reproducibility`
- `synthetic-data-generation-for-backtest-augmentation`
- `stress-testing-against-historical-crash-scenarios`
- `risk-limit-calibration-against-historical-drawdowns`
- `transaction-cost-analysis-tca-integration`
- `survivorship-bias-free-universe-construction`
- `kill-switch-and-drawdown-circuit-breakers`
