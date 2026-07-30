# Standards for Eurex Market Data and Order API

| Metric | Engineering Standard |
|---|---|
| FESX Tick Compliance | EURO STOXX 50 Futures (`FESX`) order prices MUST be exact multiples of 1.0 index point. |
| Price Reasonability Band | Order prices MUST NOT deviate $> 50$ index points from prevailing market mid-price. |
| ETI Protocol Format | Order entry MUST conform to T7 ETI binary FIX 5.0 SP2 message schemas. |
