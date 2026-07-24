# Broker / API Coverage

This is the cross-cutting index of which skills touch which broker or framework,
analogous in spirit to a framework-coverage matrix — but for broker APIs rather
than security frameworks, since that's the relevant "standard" in this domain.

| Broker / Framework | Skills that reference it |
|---|---|
| Fyers API v3 | `headless-broker-auth-patterns`, `token-lifecycle-live-probing`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` |
| Zerodha Kite Connect | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `websocket-reconnect-without-duplicate-subscriptions` |
| ICICI Breeze API | `headless-broker-auth-patterns`, `token-lifecycle-live-probing`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` |
| Upstox API v2 | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` |
| Alpaca Trading API | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` |
| IBKR TWS/Gateway API | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` |
| WebSocket streaming (broker-agnostic) | `producer-consumer-tick-pipeline`, `tick-buffering-burst-handling`, `backpressure-drop-degrade-policy`, `websocket-reconnect-without-duplicate-subscriptions` |
| systemd | `systemd-supervision-for-trading-bots` |
| Binance Spot/Futures API | `crypto-exchange-api-integration` |
| Coinbase Advanced Trade API | `crypto-exchange-api-integration` |
| Kraken REST/WebSocket v2 | `crypto-exchange-api-integration` |
| OANDA v20 REST API | `forex-broker-integration-oanda-mt5` |
| MetaTrader 5 (Python bridge) | `forex-broker-integration-oanda-mt5` |
| exchange_calendars / pandas_market_calendars | `global-exchange-holiday-calendar-handling` |
| IANA tz database (zoneinfo/pytz) | `multi-timezone-session-scheduling` |
| SPAN (Standard Portfolio Analysis of Risk) | `options-margin-span-calculation-global` |
| FINRA Rule 4210 (Pattern Day Trader) | `pattern-day-trader-rule-compliance-us` |
| MiFID II / MiFIR, RTS 6 | `mifid-ii-algo-trading-compliance-eu` |

See `docs/ROADMAP_500.md` for planned coverage of additional global venues (CME
Globex, Eurex, HKEX, SGX, ASX, JPX, Schwab, TradeStation, and others) not yet
built out as full skills.

## Notes

- This table reflects the brokers referenced when each skill was written. Broker APIs
  change without notice — treat this as a starting index for discovery, not a
  guarantee of current accuracy. Verify against the broker's live documentation
  before implementation.
- Coverage is intentionally weighted toward Indian equity/derivatives brokers
  (Fyers, Zerodha, ICICI Breeze, Upstox) reflecting the origin of this repo's first
  pass, alongside Alpaca and IBKR for broader applicability. Contributions extending
  coverage to other brokers (e.g. Interactive Brokers regional variants, Binance/crypto
  exchanges, other equities markets) are welcome — see `CONTRIBUTING.md`.
