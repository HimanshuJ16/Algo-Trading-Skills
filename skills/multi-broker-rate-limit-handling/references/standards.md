# Broker & Framework Coverage — multi-broker-rate-limit-handling

| Broker / Framework | Rate Limit Specifications & Quota Rules |
|---|---|
| Fyers API v3 | 10 req/sec for order endpoints, 200 req/min for data endpoints. |
| Zerodha Kite Connect | 10 req/sec for order placement, 3 req/sec for quote API, 1 req/sec for historical API. |
| ICICI Breeze API | 100 req/min rate limit per API key across session. |
| Upstox API v2 | 10 req/sec order endpoints, 200 req/min data endpoints. |
| Alpaca Trading API | 200 req/min global account limit. |
| IBKR API | 50 messages/sec local TWS/Gateway socket limit. |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with broker API terms of service, exchange fair-access messaging limits, and exchange order-to-trade ratio (OTR) penalty regimes.
