---
name: schwab-api-oauth-pkce-flow
description: >-
  Use when connecting a trading bot to the Charles Schwab Developer API to implement OAuth 2.0 PKCE (RFC 7636) authentication, code verifier/challenge generation, and unattended token refresh
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "schwab-api", "oauth2-pkce", "rfc-7636", "token-refresh"]
brokers_frameworks: ["Charles Schwab Developer API v1"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever integrating an algorithmic trading bot with the Charles Schwab Developer API (post-TD Ameritrade migration). Schwab mandates OAuth 2.0 with Proof Key for Code Exchange (PKCE) for authorization code grants. Implementing cryptographic `code_verifier` and `code_challenge` generation, exchanging authorization codes, managing short-lived access tokens (30-minute validity), and scheduling 7-day refresh token renewals is mandatory for unattended bot sessions.

## Prerequisites

- Schwab Developer App `App Key` (Client ID) and `App Secret`.
- Registered HTTPS Redirect URI (e.g., `https://127.0.0.1:8080/callback` or custom URI).
- Secure token storage with atomic file persistence.

## Workflow

1. **Generate PKCE Pair (RFC 7636)**:
   - Generate cryptographically secure `code_verifier` (43–128 characters from `[A-Z, a-z, 0-9, -, ., _, ~]`).
   - Derive `code_challenge = Base64URL(SHA256(code_verifier))` using `S256` method.

2. **Construct Schwab Authorization URL**:
   - Format URL: `https://api.schwabapi.com/v1/oauth/authorize?client_id={app_key}&redirect_uri={redirect_uri}&response_type=code&code_challenge={code_challenge}&code_challenge_method=S256`.

3. **Exchange Auth Code for Tokens**:
   - POST to `https://api.schwabapi.com/v1/oauth/token` sending `grant_type=authorization_code`, `code`, `redirect_uri`, and original `code_verifier`.
   - Parse `access_token` (expires in 1800s) and `refresh_token` (expires in 7 days).

4. **Preemptive Access Token Refresh**:
   - Access tokens expire after 30 minutes. Schedule refresh 5 minutes ($300\text{s}$) prior to expiry using `grant_type=refresh_token`.

5. **7-Day Refresh Token Expiration Warning**:
   - Schwab refresh tokens expire strictly after 7 days and cannot be refreshed automatically past expiration. Monitor expiration timestamp and alert 24 hours in advance to prompt re-authorization.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect Base64 Encoding for PKCE**: Using standard Base64 instead of URL-safe Base64 without padding for `code_challenge`.
- **Mismatching Code Verifier**: Sending a different `code_verifier` during token exchange than the one used to derive `code_challenge`.
- **Unhandled 7-Day Refresh Token Expiry**: Allowing the 7-day refresh token to expire silently, abruptly killing bot sessions during trading hours.
- **Short Access Token Expiration**: Failing to refresh access tokens before their 30-minute window closes.

## Verification

- Generate PKCE pair and verify `code_challenge` matches RFC 7636 `S256` test vectors.
- Mock token exchange and verify `access_token` and `refresh_token` are saved atomically.
- Simulate token expiry within 300s buffer and verify automatic token refresh execution.
- Run unit test suite `python scripts/test_schwab_pkce_auth.py` and confirm 100% pass rate.

## Related Skills

- `upstox-oauth-refresh-token-rotation`
- `token-lifecycle-live-probing`
- `headless-broker-auth-patterns`
---
