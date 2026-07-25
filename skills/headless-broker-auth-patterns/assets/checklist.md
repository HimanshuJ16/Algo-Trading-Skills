# Pre-Flight / Sign-off Checklist — headless-broker-auth-patterns

Use this before considering the skill's implementation complete.

- [ ] **TOTP Safety Window:** Confirm TOTP generation incorporates `TOTPHelper.get_totp_safe()` to avoid near-expiration turnover failures.
- [ ] **Checksum Signature Verification:** Confirm REST brokers (Fyers/Zerodha) calculate correct SHA-256 signatures via `ChecksumHelper` — for Fyers specifically, confirm `auth_code` is sent as a separate `code` field and is NOT included in the hash.
- [ ] **Headless Browser Cleanup:** Confirm browser automation uses `HeadlessBrowserContext` and leaves no orphaned Chrome processes (`ps aux | grep chrome`).
- [ ] **Bounded Element Waits:** Confirm `browser_login` uses `WebDriverWait` (not fixed `time.sleep()`) for every element interaction, with a sane `element_timeout_sec`.
- [ ] **Live-Probe Before Reuse:** Confirm token acquisition goes through `get_valid_session()` with a real `probe_fn` wired in (per `token-lifecycle-live-probing`) — not direct calls to `rest_login`/`browser_login` that skip cache-probe-first logic.
- [ ] **Token Cache File Permissions:** Confirm cache files under `.auth_cache/` are `0600` (owner-read/write only), not world-readable.
- [ ] **Automated Testing:** Run `python -m unittest scripts/test_auth_probe.py -v` and confirm 100% test pass rate (12 tests as of this revision, including regression tests for the Fyers checksum fix and the Archetype B WebDriverWait flow).

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
