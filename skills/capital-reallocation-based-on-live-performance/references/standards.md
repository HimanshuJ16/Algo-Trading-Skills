# Standards for Capital Allocation

| Metric | Engineering Standard |
|---|---|
| Reallocation Frequency | Weekly or Monthly. Never intraday to avoid performance chasing noise. |
| The Kelly Multiplier | Max $0.5$ (Half-Kelly). Recommended $0.25$ (Quarter-Kelly) for highly volatile crypto/equities. |
| Hard Caps | Every strategy MUST have a hard-coded absolute capital ceiling (capacity limit) that cannot be breached regardless of performance. |
| Hurdle Rate | A strategy must maintain a minimum Sharpe (e.g., $> 0.5$) to warrant *any* capital increase. |