# Deep Workflow Reference — questrade-api-rate-limit-and-account-types

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **OAuth2 Refresh Token Exchange**:
   - Issue GET `https://login.questrade.com/oauth2/token?grant_type=refresh_token&refresh_token={REFRESH_TOKEN}`.
   - Immediately store the newly issued `refresh_token` (single-use token rotation).

2. **Retrieve API Server & Account Registry**:
   - Extract `api_server` URL (e.g. `https://api01.iq.questrade.com/`).
   - Query `GET {api_server}v1/accounts` with `Authorization: Bearer {access_token}`.
   - Categorize accounts into `Margin`, `TFSA`, `RRSP`, or `FHSA`.

3. **Enforce Rate Limits**:
   - Configure `TokenBucketRateLimiter` with capacity of 30 req/sec.
   - Wrap all REST requests with rate limiter check (`acquire()`).

4. **Validate Registered Account Restrictions**:
   - Reject short selling (`Short`, `SellShort`) or uncovered options on registered accounts (`TFSA`, `RRSP`).

## Production Implementation Reference

- Reference code: `scripts/questrade_client.py` (`QuestradeClient`, `TokenBucketRateLimiter`).
- Automated unit tests: `scripts/test_questrade_client.py`.
