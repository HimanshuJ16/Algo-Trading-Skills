# Deep Workflow Reference — etrade-oauth1-signature-flow

## Full Procedure

1. POST request token endpoint with consumer key → receive request token + secret.
2. Redirect user to E*TRADE auth URL with request token.
3. User authorizes; receive verifier code via callback.
4. Exchange request token + verifier → access token + secret.
5. Sign every API request with HMAC-SHA1 using consumer + access secrets.
6. Renew access token daily before market open.

## Production Implementation Reference

- Code: `scripts/etrade_auth.py` (`ETradeOAuth1Client`).
- Tests: `scripts/test_etrade_auth.py`.
