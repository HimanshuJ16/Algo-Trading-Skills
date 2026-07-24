# Pre-Flight / Sign-off Checklist — headless-broker-auth-patterns

Use this before considering the skill's implementation complete.

- [ ] **TOTP Safety Window:** Confirm TOTP generation incorporates `TOTPHelper.get_totp_safe()` to avoid near-expiration turnover failures.
- [ ] **Checksum Signature Verification:** Confirm REST brokers (Fyers/Zerodha) calculate correct SHA-256 signatures via `ChecksumHelper`.
- [ ] **Headless Browser Cleanup:** Confirm browser automation uses `HeadlessBrowserContext` and leaves no orphaned Chrome processes (`ps aux | grep chrome`).
- [ ] **Date-Keyed Token Caching:** Confirm tokens are cached and probed prior to triggering fresh login requests.
- [ ] **Automated Testing:** Run `python scripts/test_auth_probe.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
