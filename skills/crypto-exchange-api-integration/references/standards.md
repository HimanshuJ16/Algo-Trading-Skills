# Broker & Framework Coverage — crypto-exchange-api-integration

| Exchange / Platform | Relevance to this skill |
|---|---|
| Binance (Spot & USD-M/COIN-M Futures) | Weight-based rate limits (`x-mbx-used-weight-1m`), STP modes (`EXPIRE_MAKER`, `EXPIRE_TAKER`, etc.), distinct Spot vs. Futures API keys and endpoints. |
| Coinbase Advanced Trade | REST & WebSocket API, product-specific rate limit pools, candle & orderbook depth throttles. |
| Kraken REST & WebSocket v2 | Tiered rate-limit decay counter, nonce management, post-only and immediate-or-cancel execution flags. |

## Category

`global-market-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with global crypto asset regulatory frameworks (e.g. EU MiCA regulations, US FinCEN rules, key custody isolation, and exchange API permission scoping).
