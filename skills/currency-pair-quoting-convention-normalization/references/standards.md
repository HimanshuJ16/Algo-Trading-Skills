# Standards for Currency Pair Quoting Convention Normalization

| Metric | Engineering Standard |
|---|---|
| ISO 4217 Hierarchy | Currency pairs MUST follow market priority order: `EUR` > `GBP` > `AUD` > `NZD` > `USD` > `CAD` > `CHF` > `JPY`. |
| Spread Inversion Precision | Inverted bid/ask price conversion MUST use cross-inversion ($\text{Bid}_{\text{std}} = 1/\text{Ask}_{\text{inv}}$). |
| Pip Precision Tier | Pip sizes MUST equal $0.01$ for JPY counter currencies and $0.0001$ for non-JPY counter currencies. |
