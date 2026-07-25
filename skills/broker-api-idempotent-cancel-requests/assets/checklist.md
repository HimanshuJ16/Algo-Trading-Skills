# Pre-Flight / Sign-off Checklist — broker-api-idempotent-cancel-requests

Use this before considering the skill's implementation complete.

- [ ] **Client Cancel Key Generation:** Confirm `client_cancel_id` is generated and tracked per order.
- [ ] **Idempotency Cache Audit:** Confirm repeated cancel calls with identical `client_cancel_id` return cached status.
- [ ] **Race Condition Handling:** Confirm HTTP 400 "already filled" responses map to `FILLED_BEFORE_CANCEL`.
- [ ] **Not Found Handling:** Confirm HTTP 404 / "already cancelled" responses map to `ALREADY_CANCELLED`.
- [ ] **Automated Testing:** Run `python scripts/test_cancel_manager.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
