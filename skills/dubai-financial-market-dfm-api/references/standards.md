# Standards for Dubai Financial Market (DFM) API Integration

| Metric | Engineering Standard |
|---|---|
| Mandatory NIN Tagging | ALL DFM orders MUST contain a valid 10-digit National Investor Number (NIN) in Tag 1. |
| AED Tick Compliance | Order prices MUST conform strictly to DFM tick size bands. |
| Circuit Breaker Band | Order prices MUST NOT exceed $\pm 10\%$ of prior settlement. |
