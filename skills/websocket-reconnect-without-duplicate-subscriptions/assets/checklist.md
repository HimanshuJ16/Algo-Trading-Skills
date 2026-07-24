# Pre-Flight / Sign-off Checklist — websocket-reconnect-without-duplicate-subscriptions

Use this before considering the skill's implementation complete.

- [ ] **Decoupled Subscription State:** Confirm `desired_symbols` set acts as the single source of truth for resubscriptions.
- [ ] **Fresh Resubscription on Reconnect:** Confirm reconnect triggers a single fresh resubscription call from `desired_symbols` without appending logs.
- [ ] **Jittered Exponential Backoff:** Confirm `calculate_backoff()` applies exponential growth and randomized jitter.
- [ ] **Gap Window Backfill:** Confirm `on_reconnect()` logs disconnect gap duration and invokes REST backfill callback.
- [ ] **Tick Deduplication:** Confirm `TickDeduplicator` filters out duplicate sequence number / timestamp ticks downstream.
- [ ] **Automated Testing:** Run `python scripts/test_reconnect_manager.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
