# Workflows for Strategy Performance Decay Detection vs Market-Wide Decay

1. **Return Alignment**:
   - Align target strategy daily returns with peer benchmark index returns.
2. **Rolling Sharpe Estimation**:
   - Estimate 60-day rolling Sharpe ratios for strategy and peer index.
3. **Z-Score Attribution**:
   - Calculate relative Sharpe difference Z-score.
4. **Actionable Classification**:
   - Issue `IDIOSYNCRATIC_ALPHA_DECAY` or `MARKET_WIDE_REGIME_SHIFT` diagnostic report.