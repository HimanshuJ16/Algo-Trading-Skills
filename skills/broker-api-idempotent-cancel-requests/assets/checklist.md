# Pre-Flight / Sign-off Checklist — broker-api-idempotent-cancel-requests

Use this before considering the skill's implementation complete.

- [ ] **Thread-Safe Client Cancel Key Generation:** Confirm `client_cancel_id` is generated robustly and safely across concurrent threads.
- [ ] **Idempotency Cache Audit:** Confirm repeated cancel calls with identical `client_cancel_id` return cached status.
- [ ] **Bounded Memory Usage:** Confirm the cache employs an eviction strategy (e.g. `OrderedDict` popitem) to avoid memory leaks.
- [ ] **Exponential Backoff:** Confirm 5xx Server Errors and Connection Exceptions gracefully trigger retry loops before failing.
- [ ] **Race Condition Handling:** Confirm HTTP 400 "already filled" responses cleanly map to `FILLED_BEFORE_CANCEL`.
- [ ] **Not Found Handling:** Confirm HTTP 404 / "already cancelled" responses cleanly map to `ALREADY_CANCELLED`.
- [ ] **Automated Testing:** Run `python -m unittest test_cancel_manager.py` in the `scripts` folder and achieve a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
