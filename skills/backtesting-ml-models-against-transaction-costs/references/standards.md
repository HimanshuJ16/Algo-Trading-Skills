# ML Transaction Cost Standards

| Metric | Rule |
|--------|------------|
| Cost Model | Must use Half-Turn costs. A round trip (buy then sell) incurs $2 \times$ Half-Turn cost. |
| Hurdle Rate | `signal_threshold` must be strictly $\ge$ `2 * bps_cost` to prevent structurally unprofitable trades. |
| Turnover | A flip from Long (+1) to Short (-1) incurs $2$ units of turnover (sell long, sell short). |
