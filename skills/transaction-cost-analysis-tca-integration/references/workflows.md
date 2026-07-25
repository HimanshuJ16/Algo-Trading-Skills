# Deep Workflow Reference — transaction-cost-analysis-tca-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Implementation Shortfall Calculation**:
   - Compute total shortfall $IS = \frac{P_{\text{fill}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10^4$ (bps).

2. **Decompose TCA Components**:
   - **Delay Cost**: $\frac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}}$
   - **Spread Cross**: $\frac{\text{Spread}}{2 \cdot P_{\text{decision}}}$
   - **Market Impact**: $\gamma \sqrt{\frac{\text{OrderSize}}{\text{ADV}}}$
   - **Commissions & Exchange Fees**: Fixed fee rate.

3. **Calibrate Backtest Returns**:
   - Deduct implementation shortfall drag from gross backtest returns.

## Production Implementation Reference

- Reference code: `scripts/tca_integrator.py` (`TCABacktestIntegrator`, `TCATradeBreakdown`, `TCAPortfolioSummary`).
- Automated unit tests: `scripts/test_tca_integrator.py`.
