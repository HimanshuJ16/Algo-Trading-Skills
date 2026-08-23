# Pre-Flight / Sign-off Checklist — demo-account-realism-gap-assessment

Use this before considering the skill's implementation complete.

- [ ] **Execution Log Ingestion:** Confirm demo and live execution logs are captured with environment label, side, timestamps, arrival prices, fill prices, and requested/filled quantities.
- [ ] **Non-Transposition:** Confirm demo logs are labelled `DEMO` and live logs `LIVE`, and that swapping them raises rather than scoring 1.0.
- [ ] **Fail-Closed Validation:** Confirm non-finite prices, non-positive prices, zero live latency, over-fills, and reversed timestamps are rejected — not scored as parity.
- [ ] **Matched Instruments:** Confirm demo and live logs cover the same symbol set, or that a flagged mismatch has been consciously accepted.
- [ ] **Signed Slippage:** Confirm slippage is signed by side, and that demo price improvement *lowers* the realism score rather than cancelling live adverse cost.
- [ ] **Metric Calculation:** Confirm mean latency (ms), mean signed slippage (bps), and fill rates are calculated accurately.
- [ ] **Realism Score Computation:** Confirm Realism Score $R$ is bounded between 0.0 and 1.0.
- [ ] **Sample Sufficiency:** Confirm `is_sample_sufficient` is checked before the score informs any capital decision.
- [ ] **Sharpe Discount Direction:** Confirm a positive demo Sharpe is scaled down by $R$, and that a non-positive demo Sharpe is returned unchanged with a warning.
- [ ] **Parameter Calibration:** Confirm `slippage_decay_bps` and `promotion_threshold` were set deliberately for this strategy rather than left at defaults.
- [ ] **Complementary Checks:** Confirm execution realism is not being used as a substitute for a selection-bias correction (e.g. Deflated Sharpe Ratio).
- [ ] **Disclosure:** If demo results are presented externally, confirm the applicable hypothetical-performance disclosure obligations have been reviewed (see `references/standards.md`).
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/demo-account-realism-gap-assessment/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
