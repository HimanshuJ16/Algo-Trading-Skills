# Standards for HK SFC Algorithmic Compliance

| Metric | Engineering Standard |
|---|---|
| Covered Short Selling | Short sell orders MUST require verified locate/borrow confirmation (`has_locate_borrow = True`). |
| Pre-Trade Value Limit | Single order value MUST NOT exceed max configured threshold (e.g. HKD $10,000,000$). |
| Price Deviation Limit | Order price MUST NOT deviate $> 5.0\%$ from current market price. |