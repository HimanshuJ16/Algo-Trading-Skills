# Standards for Options Greeks Real-Time Portfolio Aggregation

| Metric | Engineering Standard |
|---|---|
| Contract Multiplier | Standard US equity options multiplier MUST be 100. |
| Dollar Delta Formula | $\text{DollarDelta} = \text{NetDeltaShares} \times \text{UnderlyingSpotPrice}$. |
| Risk Limit Enforcement | $|\text{DollarDelta}_{\text{net}}| \le \text{MaxLimit}$ MUST be continuously audited. |