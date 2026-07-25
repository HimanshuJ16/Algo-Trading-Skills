# Deep Workflow Reference — broker-api-idempotent-cancel-requests

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Generate Client Cancel Key**:
   - Assign unique `client_cancel_id` (e.g. `CANCEL_{order_id}_{seq}`) to deduplicate retries.

2. **Idempotency Cache Audit**:
   - Check if `client_cancel_id` has already been processed. If cached, return previous result immediately.

3. **Dispatch Cancel & Intercept Race Conditions**:
   - Dispatch HTTP cancel call.
   - Classify 200/202 as `CANCELLED`.
   - Classify 400 "Order filled" as `FILLED_BEFORE_CANCEL`.
   - Classify 404 / 400 "Not found" as `ALREADY_CANCELLED`.

4. **Return Normalized CancelResult**:
   - Return clean `CancelResult` without throwing unhandled exceptions to caller loops.

## Production Implementation Reference

- Reference code: `scripts/cancel_manager.py` (`IdempotentCancelManager`, `CancelStatus`, `CancelResult`).
- Automated unit tests: `scripts/test_cancel_manager.py`.
