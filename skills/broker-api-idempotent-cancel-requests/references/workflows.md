# Deep Workflow Reference — broker-api-idempotent-cancel-requests

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Generate Client Cancel Key**:
   - Assign unique `client_cancel_id` (e.g. `CANCEL_{order_id}_{seq}_{timestamp}`) to deduplicate retries.

2. **Thread-Safe Idempotency Cache Audit**:
   - Acquire lock and check if `client_cancel_id` exists in the bounded LRU cache (e.g., `OrderedDict`).
   - If cached, return previous `CancelResult` immediately.

3. **Dispatch Cancel, Retry & Intercept Race Conditions**:
   - Dispatch HTTP cancel call.
   - If a 5xx Server Error or Connection Error occurs, implement exponential backoff up to `max_retries` (e.g. 3 attempts).
   - Classify 200/202 as `CANCELLED`.
   - Classify 400 "Order filled" or "already executed" as `FILLED_BEFORE_CANCEL`.
   - Classify 404 / 400 "Not found" or "already cancelled" as `ALREADY_CANCELLED`.
   - Any other errors result in `FAILED`.

4. **Return Normalized CancelResult**:
   - Store the result securely back in the bounded cache, evicting the oldest entries if capacity is reached.
   - Return clean `CancelResult` without throwing unhandled exceptions to caller loops.

## Production Implementation Reference

- Reference code: `scripts/cancel_manager.py` (`IdempotentCancelManager`, `CancelStatus`, `CancelResult`).
- Automated unit tests: `scripts/test_cancel_manager.py`.
