# Pre-Flight / Sign-off Checklist — monte-carlo-strategy-robustness-testing

Use this before considering the skill's implementation complete.

- [ ] **Sequence Shuffling:** Confirm `run_sequence_shuffling()` evaluates drawdown distributions across $1,000$ iterations.
- [ ] **Bootstrap Resampling:** Confirm `run_bootstrap_resampling()` evaluates sampling variation with replacement.
- [ ] **Quantile Threshold:** Confirm $95\text{th}$ percentile Max Drawdown ($DD_{95}$) is $\le$ max drawdown limit.
- [ ] **Risk of Ruin:** Confirm Risk of Ruin $P(DD \ge \text{Limit}) \le 1.0\%$.
- [ ] **Automated Testing:** Run `python scripts/test_monte_carlo_engine.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
