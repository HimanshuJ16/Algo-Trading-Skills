# Standards for IBKR Global Routing

| Metric | Engineering Standard |
|---|---|
| Primary Exchange | SmartRouted equity orders MUST include `primaryExchange` hint. |
| HKEX Symbol Format | HKEX stock codes MUST be 5-digit zero-padded numeric strings (e.g. `00700`). |
| Currency Matching | Contract currency MUST match target market region (USD/US, EUR/EU, HKD/HK). |
