---
name: etrade-oauth1-signature-flow
description: >-
  Use when integrating E*TRADE's API which uses OAuth1 (not OAuth2) signature-based
  authentication, handling request token acquisition, user authorization callback,
  access token exchange, and HMAC-SHA1 request signing for every API call.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "etrade", "oauth1", "hmac-sha1", "request-signing"]
brokers_frameworks: ["E*TRADE", "OAuth1", "HMAC-SHA1"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building a trading bot for E\*TRADE. Unlike most modern broker APIs
that use OAuth2 bearer tokens, E\*TRADE uses **OAuth1** with HMAC-SHA1 request signing.
Every API request must include a signed `Authorization` header with nonce, timestamp, and
signature. This skill covers the full OAuth1 flow and per-request signing.

## Prerequisites

- E\*TRADE developer account with consumer key and consumer secret.
- Understanding of OAuth1 three-legged flow (request token → authorize → access token).

## Workflow

1. **Request Token**: POST to E\*TRADE's request token endpoint with consumer key.
2. **User Authorization**: Redirect user to E\*TRADE authorization URL; receive verifier code.
3. **Access Token**: Exchange request token + verifier for access token/secret.
4. **Sign Requests**: Every API call signed with HMAC-SHA1 using consumer + access secrets.
5. **Token Refresh**: Access tokens expire daily; renew before market open.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Clock Skew in Signatures**: OAuth1 timestamps must be within 5 minutes of server time.
- **Nonce Reuse**: Each request needs a unique nonce; reuse causes signature rejection.
- **URL Encoding**: OAuth1 parameter encoding is strict (RFC 5849 percent-encoding).

## Verification

- Generate an OAuth1 signature and verify it matches expected HMAC-SHA1 output.
- Simulate the full three-legged flow and verify token acquisition.
- Run `python scripts/test_etrade_auth.py` and confirm 100% pass rate.

## Related Skills

- `headless-broker-auth-patterns`
- `schwab-api-oauth-pkce-flow`
- `broker-agnostic-adapter-interface`
---
