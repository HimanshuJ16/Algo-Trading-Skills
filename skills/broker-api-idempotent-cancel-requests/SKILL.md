---
name: broker-api-idempotent-cancel-requests
description: >-
  Use when managing order lifecycles to execute idempotent order cancel requests, handling Cancel-vs-Fill race conditions, network timeouts, and duplicate cancel retries without unhandled broker API exceptions.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "idempotency", "order-cancellation", "race-condition", "cancel-vs-fill", "resilience"]
brokers_frameworks: ["Idempotent Cancel Manager", "Python Trading Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when issuing order cancel requests in high-volume or automated algorithmic trading bots. Order cancellation carries distinct race conditions from order placement: an order may fill on the exchange matching engine micro-seconds before a cancel request arrives (Cancel-vs-Fill race), or a network timeout during a cancel call may cause duplicate retries that trigger `HTTP 404 Order Not Found` or `HTTP 400 Order Already Filled` broker errors. This skill provides idempotent cancel keys and normalizes race outcomes safely.

## Prerequisites

- Order ID or client order ID to cancel.
- Unique client cancel key (`client_cancel_id`).

## Workflow

1. **Generate Client Cancel Key**:
   - Assign unique `client_cancel_id = f"CANCEL_{order_id}_{seq}"` to deduplicate cancel retries.

2. **Dispatch Cancel Request**:
   - Issue DELETE or POST cancel request to broker API with client cancel key.

3. **Intercept & Classify Race Conditions**:
   - HTTP 200/202: Order successfully cancelled (`CANCELLED`).
   - HTTP 400 "Order already filled": Matching engine filled order before cancel arrived (`FILLED_BEFORE_CANCEL`).
   - HTTP 404 / 400 "Order not found or already cancelled": Repeated retry of previously cancelled order (`ALREADY_CANCELLED`).

4. **Return Normalized Idempotent Result**:
   - Return deterministic `CancelResult` without crashing strategy execution loops.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Uncaught Broker 404 / 400 Errors**: Treating "Order already filled" API rejections as fatal connection crashes.
- **Duplicate Cancel Storms**: Retrying cancel requests rapidly without client cancel key deduplication.
- **Ignoring In-Flight Fill Events**: Assuming a successful cancel HTTP response guarantees zero fills when a fill webhook is already in transit.

## Verification

- Simulate network timeout during cancel and retry with same `client_cancel_id`, verifying idempotent return.
- Simulate Cancel-vs-Fill 400 error and verify `FILLED_BEFORE_CANCEL` classification.
- Run `python scripts/test_cancel_manager.py` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `webhook-based-order-fill-notifications`
- `broker-agnostic-adapter-interface`
---
