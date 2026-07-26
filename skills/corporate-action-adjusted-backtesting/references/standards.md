# Standards for Corporate Action Adjusted Backtesting

| Metric | Engineering Standard |
|---|---|
| Signal vs Execution Separation | Signals MUST be computed on adjusted prices; Execution and cash accounting MUST use raw unadjusted prices. |
| CAF Anchoring | Cumulative Adjustment Factors (CAF) MUST be normalized to $1.0$ at current/latest date. |
| Volume Symmetry | Trading volume MUST be adjusted inversely to price adjustments ($V_{adj} = V_{raw} / \text{CAF}$). |
