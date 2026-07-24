# Deep Workflow Reference — headless-broker-auth-patterns

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### Archetype A — REST-Based Headless Authentication (Fyers, Upstox, Alpaca, Zerodha)

1. **TOTP Window Safety Check:**
   - Generate TOTP code using `TOTPHelper.get_totp_safe()`.
   - Verify at least 5 seconds remain in the 30-second TOTP window before issuing HTTP request to prevent expiration in transit.

2. **SHA-256 Checksum Signature Generation:**
   - Generate SHA-256 signature using `ChecksumHelper` per broker specification (e.g. `hashlib.sha256(f"{app_id}:{auth_code}:{secret}".encode())`).

3. **Daily Date-Keyed Token Caching:**
   - Cache access token in `TokenCacheManager` (`.auth_cache/<broker>_<date>.json`).
   - Probe cached token at bot startup before performing fresh headless login.

### Archetype B — Browser Automation Authentication (ICICI Breeze)

1. **Headless Browser Context Management:**
   - Wrap Selenium / Playwright instances in `HeadlessBrowserContext` to guarantee `driver.quit()` execution even on unhandled exceptions.
   - Prevent background Chrome zombie processes under systemd/cron restarts.

2. **Explicit WebDriver Element Waits:**
   - Replace static `time.sleep()` calls with `WebDriverWait` explicit element visibility conditions.

3. **Redirect Session Token Interception:**
   - Intercept redirect URL parameters (`session_token` / `api_session`) and immediately initialize broker SDK session objects.

## Failure Modes Observed in Production

- **Expired TOTP in Transit:** Submitting TOTP codes at 29s in the 30s window, causing intermittent authentication rejection.
- **Orphaned Chrome Zombies:** Failing to quit headless browser instances, exhausting host RAM under systemd auto-restarts.
- **Missing Checksum Signatures:** Omitting broker SHA-256 signature hashes, causing 401 Unauthorized API responses.
- **Uncached Daily Re-Logins:** Triggering fresh headless logins on every bot restart, hitting broker login rate limits.

## Production Implementation Reference

- Reference code: `scripts/auth_probe.py` (`TOTPHelper`, `ChecksumHelper`, `HeadlessBrowserContext`, `TokenCacheManager`).
- Automated unit tests: `scripts/test_auth_probe.py`.
