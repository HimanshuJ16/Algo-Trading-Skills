# Pre-Flight / Sign-off Checklist — monte-carlo-strategy-robustness-testing

Use this before considering the skill's implementation complete.

## Input Integrity

- [ ] **Units:** Confirm the trade log holds **fractional returns on account equity** ($0.02 = +2\%$), not absolute currency P&L.
- [ ] **Finite Inputs:** Confirm NaN/Inf trade returns raise `MonteCarloError` rather than being skipped — an unvalidated peak/drawdown loop reports `is_robust=True` on a corrupted log.
- [ ] **Total-Loss Bound:** Confirm any return $\le -1.0$ is rejected; the compounding model cannot represent equity below zero.
- [ ] **Sample Size:** Confirm $N \ge 5$ trades (hard floor) and preferably $N \ge 30$.

## Configuration

- [ ] **Capital & Limit:** Confirm `initial_capital` $> 0$ and `max_drawdown_limit` $\in (0, 1]$.
- [ ] **Path Count:** Confirm $M \ge 500$ simulations; below that, $DD_{95}$ is too noisy to gate a deployment decision.
- [ ] **Breach Ceiling:** Confirm `max_risk_of_ruin_pct` reflects the desk's own risk appetite — the $1.0\%$ default is a house convention, not a standard.
- [ ] **Reproducibility:** Confirm the seed is recorded with the result, that two runs on the same engine and seed agree, and that the caller's global `random` stream is untouched.

## Simulation Modes

- [ ] **Sequence Shuffling:** Confirm `run_sequence_shuffling()` evaluates the drawdown distribution across $M$ permutations.
- [ ] **Permutation Invariance Understood:** Confirm terminal equity is **not** interpreted from the shuffling run — $\prod (1 + r_i)$ is order-invariant, so `median_final_equity` is a constant there (`final_equity_is_path_invariant=True`).
- [ ] **Bootstrap Resampling:** Confirm `run_bootstrap_resampling()` samples with replacement, and that its $DD_{99}$ is at least as deep as the shuffling $DD_{99}$.
- [ ] **Noise Injection:** Confirm `run_noise_injection()` ran with `noise_std` calibrated from measured fill dispersion, **and** at least one pass with a negative `mean_shift` — symmetric zero-mean noise is not a slippage test.

## Interpretation

- [ ] **Exchangeability:** Confirm the strategy's trade outcomes are not serially dependent. If wins/losses cluster (trend-following, performance-reactive sizing), resampling **understates** drawdown — treat the result as a floor and use a block bootstrap.
- [ ] **Quantile Threshold:** Confirm $DD_{95} \le$ the drawdown limit in **all three** modes.
- [ ] **Breach Probability:** Confirm $P(DD \ge \text{Limit}) \le$ the configured ceiling in all three modes, and record which mode bound.
- [ ] **Terminology:** Confirm `risk_of_ruin_pct` is reported as a *drawdown-limit breach probability*, not as the probability of losing the account.
- [ ] **Scope:** Confirm this result is not being used in place of out-of-sample validation, nor as a live risk control.

## Automated Testing

- [ ] Run `python -m unittest discover -s skills/monte-carlo-strategy-robustness-testing/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Reviewed by: ___________________________
- Seed / simulation count recorded: ___________________________
