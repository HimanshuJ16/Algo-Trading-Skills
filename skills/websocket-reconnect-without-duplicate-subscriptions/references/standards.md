# Broker & Framework Coverage — websocket-reconnect-without-duplicate-subscriptions

Each row is behaviour that changes how reconnection must be written, with the source it
was checked against. Limits and SDK internals change; re-verify against the version you
are integrating before relying on a number here.

| Broker / streaming API | Behaviour that shapes the reconnect design | Source |
|---|---|---|
| Zerodha Kite Connect v3 WebSocket | Up to **3000 instruments on one connection**; **3 WebSocket connections per API key**. In `pykiteconnect`, `KiteTicker` keeps its own `subscribed_tokens` dict and calls `resubscribe()` from `_on_open` on every connect after the first — so the SDK *already* restores subscriptions and modes. An application that also resubscribes from its own reconnect handler sends the subscribe twice, and because `subscribe()` resets each token's entry to `MODE_QUOTE`, an app-issued resubscribe that omits `set_mode` can also silently downgrade the streaming mode. | [kite.trade WebSocket docs](https://kite.trade/docs/connect/v3/websocket/); [`kiteconnect/ticker.py`](https://github.com/zerodha/pykiteconnect/blob/master/kiteconnect/ticker.py) |
| Alpaca real-time market data stream | Connection count is plan-limited and "in many subscriptions (or without one) this limit is **1**" — a reconnect that races the old session gets error **406 `connection limit exceeded`**. Authentication must precede subscription (**401 `not authenticated`**, **404 `auth timeout`**). Subscribe messages are **additive**, and the server answers each one with the session's **entire** current subscription list — that reply is an exact reconciliation oracle, not an estimate. | [Alpaca streaming market data docs](https://docs.alpaca.markets/docs/streaming-market-data) |
| IBKR TWS API / IB Gateway | **Not a WebSocket** — `eConnect` opens a plain **TCP socket** (default port 7497). Default entitlement is **100 market data lines**; beyond that TWS returns an error and a line must be cancelled before another instrument can be requested, so a bulk resubscription can be silently truncated by the quota. IBKR's own guidance: a client "should not proceed assuming the connection is ok" when an issued request has not produced its expected callback. | [TWS API — Streaming Market Data](https://interactivebrokers.github.io/tws-api/market_data.html); [TWS API — Connectivity](https://interactivebrokers.github.io/tws-api/connection.html) |
| IBKR Client Portal Web API | This is the IBKR product with an actual WebSocket (`wss://api.ibkr.com/v1/api/ws`). Per IBKR's Web API documentation, market data requests made with the `smd` topic **terminate after 10 minutes**; a new `smd` request is required to keep receiving data. Subscriptions therefore expire with no disconnect event at all, which connection-triggered resubscription alone will never notice. | IBKR Web API documentation / changelog (`interactivebrokers.com/docs/web-api/changelog`) |
| Fyers Data WebSocket | The per-session symbol cap has **changed between API versions** and the published figures disagree — the Fyers support KB states a maximum of 200 symbols, while Fyers' v3 announcement material cites a far larger figure for current SDKs. Treat the cap as version-specific: read it from the official docs for the SDK release you are on and enforce it in code, rather than hard-coding a constant from any secondary source. | [Fyers support KB — symbol subscription limit](https://support.fyers.in/portal/en/kb/articles/what-is-the-maximum-number-of-symbols-i-can-subscribe-to-in-the-data-websocket-20-11-2023) |
| RFC 6455 (the WebSocket protocol itself) | Close code **1006** means the connection closed abnormally, "e.g., without sending or receiving a Close frame" — the case where no clean-close callback ever fires. **Ping/Pong** frames (§5.5.2–5.5.3) exist to "verify that the remote endpoint is still responsive", which is the only in-protocol way to detect a half-open TCP connection. §7.2.3 directs an endpoint recovering from abnormal closure to back off with randomisation rather than hammering reconnects. | [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455) |

## Regulatory & Operational Notes

No regulator prescribes a WebSocket reconnection algorithm. What the reconnect record feeds
is generic: automated-trading regimes expect a firm to be able to reconstruct what its system
knew and when, and a market-data gap is part of that reconstruction. The audit fields this
skill emits (disconnect and reconnect timestamps, measured gap, symbols restored, whether the
gap was backfilled, whether the broker confirmed the subscription set) exist so a strategy
anomaly can be attributed to — or cleared of — a connectivity event after the fact.

For the actual jurisdictional obligations, use the dedicated skills rather than inferring
requirements here: `mifid-ii-algo-trading-compliance-eu`, `uk-fca-algorithmic-trading-systems-controls`,
`sec-rule-15c3-5-risk-controls-us`, and `record-retention-periods-by-jurisdiction`.
