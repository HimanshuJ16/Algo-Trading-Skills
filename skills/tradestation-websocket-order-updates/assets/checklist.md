# Pre-Flight / Sign-off Checklist — tradestation-websocket-order-updates

Use this before considering the skill's implementation complete.

- [ ] **Stream Message Parsing:** Confirm NDJSON frame parser extracts order updates and filters heartbeat messages.
- [ ] **REST Catch-Up on Reconnect:** Confirm `reconcile_missed_orders()` queries REST endpoint using `last_update_timestamp`.
- [ ] **Event Deduplication:** Confirm composite key `OrderID:Status:FilledQuantity` prevents duplicate fill updates.
- [ ] **Automated Testing:** Run `python scripts/test_tradestation_stream.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
