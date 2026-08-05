---
name: schwab-api-oauth-pkce-flow
description: >-
  Production-grade Charles Schwab Developer API OAuth 2.0 PKCE manager (RFC 7636) implementing cryptographically secure code_verifier generation, S256 code_challenge derivation, atomic token persistence, and 7-day refresh token lifecycle tracking.
domain: Broker Integration & Authentication
subdomain: OAuth 2.0 PKCE Security
tags: ["schwab-api", "oauth2-pkce", "rfc-7636", "code-verifier", "code-challenge", "token-persistence"]
brokers_frameworks: ["Charles Schwab Developer API", "OAuth 2.0 PKCE (RFC 7636)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating automated trading bots or market data feeds with Charles Schwab's Developer API. Schwab requires the OAuth 2.0 Proof Key for Code Exchange (PKCE) flow (RFC 7636) to prevent authorization code injection attacks on public clients. Access tokens expire every 30 minutes, while refresh tokens remain valid for 7 days. This engine generates cryptographically secure PKCE verifier/challenge pairs, exchanges authorization codes, persists tokens atomically, and monitors the 7-day refresh token expiration.

## Prerequisites

- Schwab Developer App Key (`app_key`), App Secret (`app_secret`), and Callback Redirect URI (`redirect_uri`).
- Token persistence file path (`token_file_path`: default `schwab_tokens.json`).

## Workflow

1. **PKCE Verifier & S256 Challenge Generation**:
   - Generate cryptographically secure `code_verifier` (64 characters, unreserved URL characters).
   - Compute S256 `code_challenge` = Base64URL(SHA-256(`code_verifier`)) with padding stripped (`=`).
2. **Authorization Request URL Construction**:
   - Build Schwab auth URL with `code_challenge` and `code_challenge_method=S256`.
3. **Code Exchange & Atomic Token Persistence**:
   - Exchange `authorization_code` and `code_verifier` via POST `/oauth/token`.
   - Save `access_token` (30m expiry) and `refresh_token` (7d expiry) atomically via atomic temp file replace.
4. **Token Expiration Inspection**:
   - Inspect access token buffer (default 300s before expiry) and warn 24 hours prior to 7-day refresh token expiry.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect PKCE Base64 Padding**: Including `=` padding characters in the `code_challenge` string causes Schwab auth endpoints to reject the request.
- **Unbuffered Token Expiration**: Waiting until the access token expires (0s remaining) before refreshing, causing HTTP 401 Unauthorized errors during active order submission.
- **Unmonitored 7-Day Refresh Token Expiration**: Letting the 7-day refresh token expire without warning, forcing manual browser re-login during live trading hours.

## Verification

- Instantiate `SchwabPKCEGenerator`. Generate verifier (64 chars) and challenge $\implies$ verify unpadded Base64URL challenge format. Build auth URL $\implies$ verify `code_challenge` and `code_challenge_method=S256` parameters present. Perform code exchange $\implies$ verify token saved atomically. Check 7-day refresh warning $\implies$ verify warning triggered when $< 24\text{h}$ remaining.
- Run `python scripts/test_schwab_pkce_auth.py`.

## Related Skills

- `broker-agnostic-adapter-interface`
- `sandbox-credential-leakage-prevention`
---
