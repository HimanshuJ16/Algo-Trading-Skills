# Deep Workflow Reference — monte-carlo-strategy-robustness-testing

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Ingest Historical Trade Returns:**
   - Extract sequence of trade returns $R = [r_1, r_2, \dots, r_N]$.

2. **Execute Trade Sequence Permutations (Shuffling):**
   - Perform $M=1,000$ iterations of random trade sequence shuffling without replacement.
   - Calculate maximum drawdown and final equity for each path.

3. **Execute Bootstrap Resampling:**
   - Sample $N$ trades with replacement $M=1,000$ times.
   - Construct distribution of out-of-sample Sharpe ratios and drawdowns.

4. **Calculate Quantiles & Risk of Ruin:**
   - Compute $95\text{th}$ percentile Max Drawdown ($DD_{95}$).
   - Compute Risk of Ruin $P(DD \ge \text{Limit})$.

5. **Strategy Deployment Sign-off:**
   - Require $DD_{95} \le \text{Limit}$ and $\text{Risk of Ruin} \le 1.0\%$ for production deployment.

## Failure Modes Observed in Production

- **Single Equity Curve Path Assumption:** Believing live trading will replicate the exact trade sequence of backtests.
- **Insufficient Sample Size:** Running $< 500$ iterations, creating noisy quantile estimates.

## Production Implementation Reference

- Reference code: `scripts/monte_carlo_engine.py` (`MonteCarloRobustnessEngine`, `MonteCarloResult`).
- Automated unit tests: `scripts/test_monte_carlo_engine.py`.
