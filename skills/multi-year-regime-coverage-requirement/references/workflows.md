# Deep Workflow Reference — multi-year-regime-coverage-requirement

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Market Regime Segmentation**:
   - Classify rolling 20-day price segments into `BULL_TREND`, `BEAR_MARKET`, `HIGH_VOLATILITY_CRASH`, or `LOW_VOLATILITY_RANGE`.

2. **Audit Multi-Year Coverage**:
   - Verify dataset spans $\ge 3.0$ years and covers $\ge 3$ distinct market regimes.

3. **De-average Performance Metrics**:
   - Compute Sharpe ratio, win rate, and max drawdown individually per regime.

4. **Enforce Promotion Thresholds**:
   - Trigger `REGIME VETO` if strategy exceeds max allowed drawdown limit ($25\%$) in any single regime.

## Production Implementation Reference

- Reference code: `scripts/regime_coverage.py` (`MarketRegimeCoverageEngine`, `MarketRegime`, `RegimeCoverageAuditReport`).
- Automated unit tests: `scripts/test_regime_coverage.py`.
