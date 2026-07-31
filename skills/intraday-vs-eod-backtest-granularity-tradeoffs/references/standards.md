# Standards for Backtest Granularity Selection

| Metric | Engineering Standard |
|---|---|
| Intraday Stop-Loss Rule | Intraday stop-loss strategies MUST NOT be backtested solely on Daily EOD data. |
| High Frequency Rule | Strategies with $\ge 20$ trades/day MUST use Sub-Second / Tick or 1-Minute data. |
| Positional Strategy Rule | Long-term positional strategies SHOULD use Daily EOD data to minimize compute. |
