# Data Provider Standards — corporate-action-adjusted-backtesting

| Data Provider | Adjustment Field / Standard | Split Handling | Dividend Handling |
|---|---|---|---|
| CRSP (Center for Research in Security Prices) | `CFACPR` (Cumulative Factor Price), `CFACSHR` (Cumulative Factor Shares) | Multiplicative factor | Continuous dividend ratio factor |
| Polygon.io / Yahoo Finance | `adj_close` vs `close` | Pre-calculated backward factors | Subtracted / ratio adjusted |
| Interactive Brokers Historical API | `useRTH=1, whatToShow=ADJUSTED_LAST` | Real-time split/div adjustment | Cash dividends included |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with corporate action processing, accounting for dividend yields in total return benchmarks, and unadjusted vs adjusted price series integrity.
