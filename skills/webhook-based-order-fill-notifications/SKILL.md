---
name: webhook-based-order-fill-notifications
description: Use when building webhook consumers for broker order fill notifications
  to handle HMAC-SHA256 signature verification, at-least-once delivery deduplication,
  and out-of-order execution sequencing
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- webhooks
- order-fills
- deduplication
- at-least-once-delivery
brokers_frameworks:
- Generic Broker Webhooks
- Interactive Brokers
- TradeStation
- Alpaca
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a trading bot receives order fill or execution status notifications over HTTP webhooks. Brokers implement at-least-once delivery guarantees, meaning the same webhook payload may be delivered multiple times due to network retries, or arrive out of sequence. Implementing HMAC-SHA256 signature validation, timestamp freshness checks, composite key deduplication (`order_id:exec_id`), and sequence number ordering is mandatory to prevent duplicate position ledger updates.

## Prerequisites

- Shared webhook secret for HMAC-SHA256 signature verification.
- In-memory or database tracking of processed execution IDs.
- Execution sequence tracker per `order_id`.

## Workflow

1. **HMAC-SHA256 Signature Verification**:
   - Extract `X-Webhook-Signature` or `X-Hub-Signature` header.
   - Compute `expected_sig = HMAC-SHA256(raw_body, secret)`. Compare using `hmac.compare_digest()`.

2. **Timestamp Freshness & Replay Defense**:
   - Verify payload timestamp is within 300 seconds of current server time to reject replayed HTTP requests.

3. **Composite Event Deduplication**:
   - Check unique event signature `order_id:exec_id` against `processed_executions` store. Skip processing if signature was already ingested.

4. **Monotonic Sequence Ordering**:
   - Inspect payload `sequence_num` or execution timestamp. If an incoming fill has a lower sequence number than an already-processed fill for that order, log warning and queue for re-ordering.

5. **Acknowledge & Sync Order Ledger**:
   - Return HTTP 200 OK to the broker immediately after persisting the verified event to the order ledger.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Verified Webhook Endpoints**: Processing unauthenticated HTTP POST requests, allowing malicious webhook spoofing.
- **Duplicate Position Adjustments**: Applying retried webhook deliveries to position ledgers multiple times.
- **Out-of-Order Execution Processing**: Processing a `FILLED` event before a `PARTIALLY_FILLED` event, corrupting order state transitions.

## Verification

- Submit valid signed webhook payload and verify `process_webhook()` returns `SUCCESS`.
- Submit tampered signature payload and confirm rejection (`INVALID_SIGNATURE`).
- Submit duplicate webhook event and confirm `DUPLICATE_SKIPPED` response.
- Run unit test suite `python scripts/test_webhook_consumer.py` and confirm 100% pass rate.

## Related Skills

- `zerodha-kite-postback-webhook-verification`
- `order-placement-idempotency`
- `websocket-reconnect-without-duplicate-subscriptions`
---
