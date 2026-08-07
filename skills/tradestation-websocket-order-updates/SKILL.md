---
name: tradestation-websocket-order-updates
description: Use when consuming TradeStation's WebAPI order update stream to manage
  streaming WebSocket connections, gap-reconciliation via REST fallback, and fill
  deduplication across network reconnects
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- tradestation-api
- websocket-stream
- order-updates
- gap-reconciliation
brokers_frameworks:
- TradeStation WebAPI v2/v3
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever an algorithmic trading bot consumes real-time order fill updates from TradeStation's streaming WebAPI (`/v2/stream/orders`). Disconnections on WebSocket streams during active market hours can result in missed fill notifications. Implementing heartbeat monitoring, automatic exponential backoff reconnects, REST API catch-up queries for missed order states during offline windows, and composite key event deduplication is mandatory to guarantee zero missed or duplicated fills.

## Prerequisites

- TradeStation WebAPI Bearer Access Token.
- Target TradeStation Account ID (`SIM123456` for Paper / `12345678` for Live).
- In-memory or database order ledger tracking last processed order update timestamp/sequence.

## Workflow

1. **Establish WebSocket Order Stream**:
   - Open HTTP/WebSocket connection to TradeStation streaming endpoint: `GET /v2/stream/orders/{account_id}` with `Authorization: Bearer {access_token}`.

2. **Stream Message Parsing & Heartbeat Check**:
   - Parse incoming line-delimited JSON (NDJSON) messages. Filter heartbeat objects (`{"Heartbeat": 1}`) and extract order update records (`OrderID`, `Status`, `FilledQuantity`, `AveragePrice`).

3. **Reconnection & Gap-Detection Trigger**:
   - Maintain `last_update_timestamp`. Upon WebSocket connection drop, initiate exponential backoff reconnection.
   - Record disconnection timestamp ($T_{\text{disconnect}}$).

4. **REST API Catch-Up Query**:
   - Immediately upon reconnecting, query REST endpoint `GET /v2/users/{user_id}/accounts/{account_id}/orders?since={last_update_timestamp}`.
   - Ingest missed order fill events that occurred while the WebSocket stream was disconnected.

5. **Deduplicating Catch-Up vs WebSocket Events**:
   - Hash each processed event using `OrderID:Status:FilledQuantity`.
   - Skip any catch-up event already processed prior to disconnection to prevent double-counting position updates.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming WebSocket Reliability**: Relying solely on the WebSocket stream without implementing REST catch-up queries on reconnect, resulting in silent missing fills.
- **Double-Counting Fills**: Ingesting the same order fill event twice (once from REST catch-up and once when the WebSocket stream resumes).
- **Ignoring Heartbeats**: Failing to detect a frozen TCP connection when TradeStation stops sending heartbeat frames.

## Verification

- Process incoming stream events and verify order status updates are logged correctly.
- Simulate a network disconnect/reconnect and confirm REST catch-up queries fill events occurring during the offline window.
- Verify duplicate events are filtered by `is_duplicate()` deduplication.
- Run unit test suite `python scripts/test_tradestation_stream.py` and confirm 100% pass rate.

## Related Skills

- `websocket-reconnect-without-duplicate-subscriptions`
- `order-placement-idempotency`
- `webhook-based-order-fill-notifications`
---
