# Pre-Flight / Sign-off Checklist — token-lifecycle-live-probing

Use this before considering the skill's implementation complete.

- [ ] **Read-Only Probe Endpoint:** Confirm probe uses a side-effect-free GET call (`/profile` or `/margins`).
- [ ] **3-Outcome Classification:** Confirm responses are classified into `VALID`, `INVALID`, and `AMBIGUOUS`.
- [ ] **Exponential Backoff on Ambiguous Outcomes:** Confirm network timeouts retry with backoff before declaring token failure.
- [ ] **Re-Authentication Flow:** Confirm 401/403 responses trigger `reauth_fn()` and re-verify the new token.
- [ ] **Automated Testing:** Run `python scripts/test_token_probe.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
