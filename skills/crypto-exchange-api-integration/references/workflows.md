# Deep Workflow Reference — crypto-exchange-api-integration

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **24/7 Rolling P&L Reset Boundaries:**
   - Because crypto markets do not close, initialize `Rolling24hPnLTracker` to maintain sliding 24h P&L boundaries or fixed UTC 00:00 resets for risk circuit breakers.

2. **Weight-Based Rate Limiting & Header Synchronization:**
   - Use `WeightRateLimiter` to track sliding-window request weights (e.g. 1,200 weight/min for Binance Spot, 2,400 weight/min for Binance Futures).
   - Dynamically sync local counter with server headers (e.g., `x-mbx-used-weight-1m` header) via `update_from_header()`.
   - Separate rate limit pools per API namespace via `CryptoExchangeRateLimiter` (`binance_spot`, `binance_futures`, `coinbase_trade`, `kraken_rest`).

3. **Explicit Self-Trade Prevention (STP) & Order Semantics:**
   - Build orders with `CryptoOrderPayload` explicitly specifying `SelfTradePreventionMode` (`EXPIRE_MAKER`, `EXPIRE_TAKER`, `EXPIRE_BOTH`, or `NONE`).
   - Use `LIMIT_MAKER` or `post_only=True` to guarantee maker fee execution.

4. **Maintenance Window Handling & Exponential Backoff:**
   - Differentiate 24/7 maintenance window disconnects (HTTP 502, 503, 504, 418) from brief network glitches.
   - Enforce randomized exponential backoff with jitter on reconnect loops.

5. **WebSocket & REST Fill Reconciliation:**
   - Prefer WebSocket execution streams for live fill updates; perform REST polling fallback on reconnect.

## Failure Modes Observed in Production

- **Request-Count Rate Limiter Failure:** Assuming all endpoints cost 1 request unit, hitting exchange rate limit bans when calling high-weight depth endpoints.
- **Missing Session Boundary:** Failing to reset daily drawdown metrics on 24/7 markets, causing stale risk triggers.
- **Default STP Mode Rejection:** Unexpected order cancellation caused by default exchange self-trade rules during automated market-making.
- **Aggressive Reconnect Bans:** Spamming reconnect attempts during scheduled exchange maintenance windows, triggering IP ban HTTP 418.

## Production Implementation Reference

- Reference code: `scripts/weight_rate_limiter.py` (`WeightRateLimiter`, `CryptoExchangeRateLimiter`, `CryptoOrderPayload`, `Rolling24hPnLTracker`).
- Automated unit tests: `scripts/test_weight_rate_limiter.py`.
