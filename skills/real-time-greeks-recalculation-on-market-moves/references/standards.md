# Standards for Real-Time Greeks Recalculation on Market Moves

| Metric | Engineering Standard |
|---|---|
| Recalculation Threshold | Moves $\le 0.5\%$ MUST use fast Taylor series; moves $> 0.5\%$ MUST trigger full BS. |
| Processing Latency | Taylor series update MUST execute in $< 5\mu s$ per option position. |
| Portfolio Aggregation | Portfolio Net Delta, Gamma, and Vega MUST be aggregated on every tick. |