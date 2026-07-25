# Pre-Flight / Sign-off Checklist — options-backtesting-with-realistic-iv-surface

Use this before considering the skill's implementation complete.

- [ ] **IV Surface Interpolation:** Confirm strike-specific IV reflects skew and smile parameters.
- [ ] **OTM Put Skew Premium:** Confirm OTM puts ($K/S < 1.0$) have elevated IV relative to ATM.
- [ ] **Black-Scholes Pricing:** Confirm Call/Put prices satisfy put-call parity constraints.
- [ ] **Greeks Calculation:** Confirm Delta, Gamma, Theta, and Vega are computed per option.
- [ ] **Automated Testing:** Run `python scripts/test_options_iv_backtester.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
