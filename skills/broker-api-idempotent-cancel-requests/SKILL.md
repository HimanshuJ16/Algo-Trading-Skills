---
name: broker-api-idempotent-cancel-requests
description: Use when managing order lifecycles to execute idempotent order cancel
  requests, handling Cancel-vs-Fill race conditions, network timeouts, 5xx server
  errors, and duplicate cancel retries without unhandled broker API exceptions.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- idempotency
- order-cancellation
- race-condition
- cancel-vs-fill
- resilience
- concurrency
brokers_frameworks:
- Idempotent Cancel Manager
- Python Trading Engine
- FIX Protocol Concepts
version: '2.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when issuing order cancel requests in high-volume, concurrent, or automated algorithmic trading systems. Order cancellation carries distinct race conditions from order placement: an order may fill on the exchange matching engine micro-seconds before a cancel request arrives (Cancel-vs-Fill race). Additionally, a network timeout during a cancel call may cause duplicate retries that trigger `HTTP 404 Order Not Found` or `HTTP 400 Order Already Filled` broker errors. This skill provides thread-safe idempotent cancel tracking, exponential backoff retries, and normalizes race outcomes safely.

## Prerequisites

- Order ID or client order ID (`ClOrdID`) to cancel.
- Unique client cancel key (`client_cancel_id`) per attempt.

## Workflow

1. **Generate Client Cancel Key**:
   - Assign unique `client_cancel_id = f"CANCEL_{order_id}_{seq}_{timestamp}"` to deduplicate cancel retries.
   
2. **Idempotency Cache Audit**:
   - Safely verify via thread-locking if `client_cancel_id` has been processed. If yes, return cached result to avoid cancel storms.

3. **Dispatch Cancel Request with Backoff**:
   - Issue DELETE or POST cancel request to broker API.
   - Retry on 5xx or Connection Errors with exponential backoff.

4. **Intercept & Classify Race Conditions**:
   - HTTP 200/202: Order successfully cancelled (`CANCELLED`).
   - HTTP 400 "Order already filled": Matching engine filled order before cancel arrived (`FILLED_BEFORE_CANCEL`).
   - HTTP 404 / 400 "Order not found or already cancelled": Repeated retry of previously cancelled order (`ALREADY_CANCELLED`).

5. **Return Normalized Idempotent Result**:
   - Return deterministic `CancelResult` securely tracked in cache.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Uncaught Broker 404 / 400 Errors**: Treating "Order already filled" API rejections as fatal connection crashes.
- **Duplicate Cancel Storms**: Retrying cancel requests rapidly without client cancel key deduplication or proper exponential backoff.
- **Ignoring In-Flight Fill Events**: Assuming a successful cancel HTTP response guarantees zero fills when a fill webhook is already in transit.
- **Memory Leaks**: Unbounded tracking of historical cancel requests (solved here via `OrderedDict` capacity constraints).
- **Concurrency Hazards**: Multiple threads attempting to cancel the same order simultaneously without proper locking.

## Verification

- Simulate network timeout during cancel and retry with same `client_cancel_id`, verifying idempotent return.
- Simulate Cancel-vs-Fill 400 error and verify `FILLED_BEFORE_CANCEL` classification.
- Run `python scripts/test_cancel_manager.py` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `webhook-based-order-fill-notifications`
- `broker-agnostic-adapter-interface`
---
