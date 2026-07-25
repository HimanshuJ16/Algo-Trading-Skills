# Deep Workflow Reference — robinhood-unofficial-api-integration

## Full Procedure

1. Generate a persistent `device_token` (UUID4) and cache it to avoid repeated MFA.
2. POST to `/oauth2/token/` with email, password, device_token, and client_id.
3. If 400 with `mfa_required`, prompt for MFA code and retry with `mfa_code` in payload.
4. Store bearer token; refresh before `expires_in` seconds.
5. Place orders via POST to `/orders/` with symbol, side, type, quantity.
6. Poll positions via GET `/positions/`.

## WARNING

This uses an **unofficial, reverse-engineered API**. It may violate Robinhood's ToS.

## Production Implementation Reference

- Code: `scripts/robinhood_client.py` (`RobinhoodUnofficialClient`).
- Tests: `scripts/test_robinhood_client.py`.
