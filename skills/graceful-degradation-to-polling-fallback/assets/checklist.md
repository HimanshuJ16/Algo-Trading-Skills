# Pre-Flight / Sign-off Checklist — graceful-degradation-to-polling-fallback

Use this before considering the skill's implementation complete.

- [ ] **Silence Timeout Detection:** Confirm `check_feed_health()` degrades to REST polling when silence exceeds threshold.
- [ ] **REST Polling Ingestion:** Confirm REST fallback worker fetches ticks when mode is `DEGRADED_POLLING`.
- [ ] **Timestamp Deduplication:** Confirm ticks with timestamps $\le$ last ingested timestamp are skipped.
- [ ] **Stream Stabilization:** Confirm WebSocket mode is restored after $N$ consecutive stable ticks.
- [ ] **Automated Testing:** Run `python scripts/test_feed_fallback_manager.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
