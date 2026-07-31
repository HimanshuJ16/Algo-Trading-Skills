# Standards for Liquidity Seeking Algorithms

| Metric | Engineering Standard |
|---|---|
| Dark Midpoint Execution | Dark venue orders MUST be priced at the exact NBBO Midpoint price. |
| Minimum Quantity Protection | Dark IOC pings MUST enforce $Q_{\text{min\_dark}}$ to prevent signal leakage. |
| Rule 611 Compliance | Lit exchange routes MUST NOT trade through the NBBO. |