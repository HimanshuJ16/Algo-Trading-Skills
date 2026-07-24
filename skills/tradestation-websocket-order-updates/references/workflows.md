# Deep Workflow Reference — tradestation-websocket-order-updates

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Establish WebSocket Order Stream:**
   - Open HTTP/WebSocket connection to TradeStation WebAPI endpoint `GET /v2/stream/orders/{account_id}`.
   - Include `Authorization: Bearer {access_token}` header.

2. **Stream Frame Parsing & Heartbeat Processing:**
   - Parse NDJSON frames. Ignore heartbeat messages (`{"Heartbeat": 1}`).
   - Extract order ID, status, filled quantity, and price.

3. **Reconnection & Disconnect Handling:**
   - Record disconnection timestamp ($T_{\text{disconnect}}$) on network failure.
   - Trigger exponential backoff reconnection.

4. **REST API Gap Catch-Up Query:**
   - Upon reconnecting, issue REST query `GET /v2/users/{user_id}/accounts/{account_id}/orders?since={last_update_timestamp}`.
   - Reconcile missed order fill updates occurring during downtime.

5. **Event Deduplication:**
   - Filter catch-up events against `processed_signatures` set (`OrderID:Status:FilledQuantity`) to prevent double-counting position updates.

## Failure Modes Observed in Production

- **Unreconciled Disconnection Gaps:** Relying solely on WebSocket streaming without REST catch-up queries on reconnect, losing fill events.
- **Double-Counting Fills:** Processing the same fill event twice (from REST catch-up and streaming recovery).
- **Frozen Socket Deadlocks:** Failing to monitor TradeStation heartbeat frames, leading to hung TCP connections.

## Production Implementation Reference

- Reference code: `scripts/tradestation_stream.py` (`TradeStationStreamManager`, `TradeStationOrderUpdate`).
- Automated unit tests: `scripts/test_tradestation_stream.py`.
