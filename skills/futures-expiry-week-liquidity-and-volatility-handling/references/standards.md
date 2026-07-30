# Standards for Futures Expiry Week Handling

| Metric | Engineering Standard |
|---|---|
| Max Spread Limit | Market orders MUST be blocked if spread $> 2.0$ ticks during expiry week. |
| Depth Haircut Threshold | Position sizes MUST be hair-cut by 50% if top-of-book depth $< 30\%$ baseline. |
| Expiration Entry Cutoff | New entry orders MUST be rejected if $DBE \le 2$ days. |
