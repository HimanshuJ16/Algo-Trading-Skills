# Standards for Canary Releases

| Metric | Target |
|---|---|
| Canary Scale Factor | Typically $1\%$ to $10\%$ of final target capital. |
| Lot Rounding | Canary scaled orders must always round *down* (floor) to nearest board lot size, never up, to prevent exceeding the risk budget. |
| Promotion Criteria | Strict threshold-based gating (e.g., minimum 100 trades with slippage < 2bps before Prod promotion). |
