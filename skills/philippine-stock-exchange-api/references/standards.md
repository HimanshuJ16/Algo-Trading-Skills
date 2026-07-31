# Standards for Philippine Stock Exchange API Integration

| Metric | Engineering Standard |
|---|---|
| Base Currency | Philippine Peso (PHP). |
| Static Circuit Breakers | Price Ceiling $+50\%$, Price Floor $-50\%$ of previous close. |
| Board Lot Range | $1,000,000$ (penny) to $5$ shares (high-priced stocks). |
