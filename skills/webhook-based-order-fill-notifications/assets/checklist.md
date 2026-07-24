# Pre-Flight / Sign-off Checklist — webhook-based-order-fill-notifications

Use this before considering the skill's implementation complete.

- [ ] **HMAC Validation:** Confirm raw HTTP body HMAC-SHA256 signature is verified in constant time.
- [ ] **Replay Defense:** Confirm timestamps older than 300s are rejected.
- [ ] **Deduplication:** Confirm `order_id:exec_id` composite signatures prevent duplicate event processing.
- [ ] **Sequence Ordering:** Confirm monotonic `sequence_num` tracking per order.
- [ ] **Automated Testing:** Run `python scripts/test_webhook_consumer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
