# Deep Workflow Reference — crypto-exchange-api-integration

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

Every exchange figure below is dated and sourced in `references/standards.md`. Published
limits change; confirm against the exchange before relying on any constant here.

## Full Procedure

1. **24/7 Rolling P&L Reset Boundaries:**
   - Crypto markets do not close, so no exchange-provided session boundary exists to reset "daily" risk state against. Use `Rolling24hPnLTracker` for a sliding window, or pick a fixed UTC 00:00 boundary and own it explicitly.
   - Timestamps are wall-clock so they line up with exchange fill timestamps; a supplied timestamp of `0.0` is a real epoch value, not "missing".

2. **Rate Limiting — Pick the Model Before the Number:**

   | Exchange | Model | Scope | Figures |
   |---|---|---|---|
   | Binance Spot | REQUEST_WEIGHT per **fixed clock window** (resets at :00, does not slide) | per IP | 6,000 weight/min (1,200 before 2023-08-25) |
   | Binance USD-M Futures | REQUEST_WEIGHT per fixed clock window | per IP | 2,400 weight/min |
   | Kraken Spot REST | **Decaying counter** — rises per call, decays continuously | per **API key** (subaccounts share the master's tier) | Starter 15 max / −0.33 per sec; Intermediate 20 / −0.5; Pro 20 / −1. Most calls cost 1, ledger & trade-history cost 2. `AddOrder`/`CancelOrder` use a separate limiter. |
   | Coinbase Advanced Trade | **Requests per second** | per IP | Figures differ between Coinbase pages — read the current one and configure explicitly. Usage is reported in `CB-RATE-LIMIT-*` headers. |

   - Use `WeightRateLimiter` for the Binance model and `KrakenDecayCounterLimiter` for Kraken's. These are not interchangeable, and no weight-per-window limiter can represent a decaying counter or a per-second throttle.
   - Prefer `CryptoExchangeRateLimiter.binance_from_exchange_info(rate_limits)` over the built-in constants: `GET /api/v3/exchangeInfo` returns the live `rateLimits` array (`{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": 6000}`).
   - Register every namespace explicitly. `get_limiter()` raises `UnknownNamespaceError` for anything unregistered rather than inventing a budget.
   - `safety_margin_pct` reserves headroom below the published limit. It is this module's own conservatism dial, not an exchange rule.

3. **Header Synchronization:**
   - Adopt `X-MBX-USED-WEIGHT-1M` as authoritative via `update_from_header()` immediately after reading each response, so the header still describes the window the limiter is in.
   - The sync must move the counter **down as well as up**. Other processes on the same IP consume the same budget (that is why the server value can exceed local), but a sync that only ratchets upward never follows the exchange's reset at the window boundary — the local counter pins at its ceiling and the bot silently stops sending.

4. **Order Semantics — Per Market, Not Per Exchange:**
   - Build orders with `CryptoOrderPayload`; `market_type` and `stp_mode` are both required arguments.
   - **Self-trade prevention:** `EXPIRE_MAKER`, `EXPIRE_TAKER`, `EXPIRE_BOTH`, `DECREMENT`, `NONE`. Omitting the parameter gives `NONE`. Allowed modes are **per symbol** — check `allowedSelfTradePreventionModes` in `exchangeInfo` before sending.
   - **Post-only:**

     | Market | Post-only spelling | Notes |
     |---|---|---|
     | Binance Spot | `type=LIMIT_MAKER`, no `timeInForce` | mandatory params are quantity and price only |
     | Binance USD-M Futures | `type=LIMIT`, `timeInForce=GTX` | `LIMIT_MAKER` is not a supported futures type |

   - There is **no `execInst` parameter on Binance** — that is BitMEX syntax. Sending it does not apply post-only; the order rests unprotected and can cross as a taker.
   - `timeInForce` sent where it is not required is rejected ("Parameter 'timeInForce' sent when not required"). Spot supports `GTC`, `IOC`, `FOK`; `GTX` and `GTD` are futures-only.

5. **Maintenance Windows & Backoff:**
   - Distinguish extended maintenance (HTTP 502/503/504) from brief network glitches.
   - Honour the `Retry-After` header on 429. Continuing to send after 429s escalates to an automated IP ban (HTTP 418) scaling from 2 minutes to 3 days for repeat offenders.
   - Use randomized exponential backoff with jitter on reconnect loops.

6. **WebSocket & REST Fill Reconciliation:**
   - Prefer WebSocket execution streams for live fill updates; reconcile over REST on reconnect.

## Known Failure Modes

- **Wrong rate-limit model:** applying a weight-per-minute pool to Kraken (a decaying counter capped at 20 with ~1/sec decay — roughly 60 calls/min steady state, not 1,000) produces immediate `EAPI:Rate limit exceeded` responses.
- **Header ratchet deadlock:** a sync that only increases the local counter never follows the exchange's window reset; the limiter saturates permanently and the bot stops trading while the exchange budget sits idle.
- **Fabricated namespace budget:** a mistyped namespace auto-created with an invented limit runs unthrottled against the real one.
- **Silent post-only loss:** `execInst: "PostOnly"` on a Binance payload is ignored, so a market-making bot crosses the spread and pays taker fees on orders it believed were maker-only.
- **Stale hard-coded limits:** Binance Spot's 1,200 weight/min figure was correct until 2023-08-25 and has been 5x too low since.
- **Missing session boundary:** failing to reset daily drawdown metrics on 24/7 markets, causing stale risk triggers.
- **Aggressive reconnect bans:** spamming reconnect attempts during scheduled maintenance, triggering an IP ban (HTTP 418).

## Production Implementation Reference

- Reference code: `scripts/weight_rate_limiter.py` — `WeightRateLimiter`, `KrakenDecayCounterLimiter`, `CryptoExchangeRateLimiter`, `CryptoOrderPayload`, `Rolling24hPnLTracker`.
- Automated unit tests: `scripts/test_weight_rate_limiter.py` (clock-injected; no wall-clock sleeps).
