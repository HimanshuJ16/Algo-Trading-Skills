# Deep Workflow Reference — webhook-based-order-fill-notifications

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **HMAC-SHA256 Signature Verification:**
   - Compute `HMAC-SHA256(raw_body, secret)`.
   - Compare with request signature header using `hmac.compare_digest()`.
   - Reject request immediately on signature mismatch.

2. **Replay Attack Defense:**
   - Parse timestamp field and verify $|T_{\text{now}} - T_{\text{event}}| \le 300\text{s}$.

3. **Composite Key Deduplication:**
   - Create signature `order_id:exec_id`.
   - Check `processed_executions` set. If present, log notice and return `DUPLICATE_SKIPPED`.

4. **Monotonic Sequence Ordering:**
   - Track `sequence_num` per `order_id`. Log warning if sequence drops out of order.

5. **Acknowledge Broker Delivery:**
   - Return HTTP 200 OK after atomic ledger mutation to clear broker retry queues.

## Failure Modes Observed in Production

- **Unauthenticated Receivers:** Ingesting unverified HTTP POST payloads without HMAC validation, allowing webhook injection attacks.
- **Double Position Adjustments:** Processing duplicate webhook retries, skewing position state.
- **Out-of-Order Execution Bugs:** Applying terminal order fills before preliminary partial fills.

## Production Implementation Reference

- Reference code: `scripts/webhook_consumer.py` (`WebhookConsumerManager`, `WebhookIngestionResult`).
- Automated unit tests: `scripts/test_webhook_consumer.py`.
