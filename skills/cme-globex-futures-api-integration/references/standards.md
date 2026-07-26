# Standards for CME Globex Order Entry

| Metric | Engineering Standard |
|---|---|
| Rule 576 Tag 50 | Every order message MUST contain a registered Operator ID (Tag 50). Length must be between 2 and 18 characters. |
| Price Banding | Limit prices exceeding exchange price bands MUST be rejected locally pre-trade before network transmission. |
| Market-With-Protection | All Market orders MUST be converted to Limit-With-Protection using contract-specific protection point offsets. |
