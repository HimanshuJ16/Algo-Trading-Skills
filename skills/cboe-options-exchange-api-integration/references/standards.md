# Standards for Cboe COB

| Metric | Engineering Standard |
|---|---|
| Maximum Legs | The system must gracefully reject strategies internally if they exceed Cboe's 16-leg limit. |
| Ratio Normalization | All multi-leg orders MUST be normalized mathematically to their simplest ratio before submission. |
| Net Pricing | For debit spreads, net price is strictly positive. For credit spreads, net price can be negative depending on broker/clearing firm conventions (always verify local clearing conventions). |
