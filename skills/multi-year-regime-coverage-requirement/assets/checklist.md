# Pre-Flight / Sign-off Checklist — multi-year-regime-coverage-requirement

Use this before considering the skill's implementation complete.

- [ ] **Regime Segmentation:** Confirm historical prices are classified into Bull, Bear, Crash, and Range regimes.
- [ ] **Multi-Year History Check:** Confirm backtest duration spans $\ge 3.0$ years.
- [ ] **Unique Regime Count:** Confirm at least 3 distinct regimes are represented.
- [ ] **De-averaged Performance:** Confirm Sharpe, win rate, and max drawdown are broken down by regime.
- [ ] **Automated Testing:** Run `python scripts/test_regime_coverage.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
