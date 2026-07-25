# Pre-Flight / Sign-off Checklist — feed-handler-canary-deployment

Use this before considering the skill's implementation complete.

- [ ] **Canary Allocation Strategy:** Confirm symbol hashing or whitelist routes $10\%$ traffic to $V_{\text{canary}}$.
- [ ] **Comparative Audit Verification:** Confirm tick prices from $V_{\text{canary}}$ are diffed against $V_{\text{stable}}$.
- [ ] **Exception Monitor:** Confirm feed handler runtime exceptions increment canary error counters.
- [ ] **Auto-Rollback Circuit Breaker:** Confirm error rate breaches automatically revert 100% traffic to $V_{\text{stable}}$.
- [ ] **Automated Testing:** Run `python scripts/test_canary_router.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
