# Pre-Flight Checklist

- [ ] Is target strategy performance evaluated relative to an appropriate peer benchmark index?
- [ ] Is relative Sharpe Z-score computed over a 60-day rolling window?
- [ ] Is `IDIOSYNCRATIC_ALPHA_DECAY` flagged when $Z \le -1.96$ while peer benchmark remains healthy?
- [ ] Is `MARKET_WIDE_REGIME_SHIFT` flagged when both target strategy and peer index display Sharpe decay $< 0.50$?