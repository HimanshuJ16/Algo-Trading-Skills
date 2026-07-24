# Broker & Framework Coverage — tradestation-websocket-order-updates

| Broker / Streaming Endpoint | Format | Gap Reconciliation Method |
|---|---|---|
| TradeStation WebAPI v2/v3 | NDJSON Streaming | REST GET `/orders?since={ts}` query on reconnect |
| Interactive Brokers (IBKR) | TWS Socket / eWrapper | Order status snapshot request `reqOpenOrders()` |
| Binance Futures WS | WebSockets | User Data Stream listenKey + REST `/fapi/v1/userTrades` |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with real-time order tracking, audit trail compliance, and network stream resiliency standards.
