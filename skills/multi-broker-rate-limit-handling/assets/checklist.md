# Pre-Flight / Sign-off Checklist — multi-broker-rate-limit-handling

Use this before considering the skill's implementation complete.

- [ ] **Endpoint Category Isolation:** Confirm distinct token buckets are registered per broker and endpoint type via `register_endpoint_bucket()`.
- [ ] **Tier 0 Priority Bypass:** Confirm Tier 0 emergency kill-switch calls bypass market data queuing delays.
- [ ] **Jittered Backoff Verification:** Confirm HTTP 429 rate limits trigger exponential backoff with randomized jitter on Tiers 1–3.
- [ ] **Telemetry Logging:** Confirm `RateLimiterMetrics` tracks total calls, 429 rate limit hits, and Tier 0 bypasses.
- [ ] **Automated Testing:** Run `python scripts/test_rate_limiter.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
