---
name: questrade-api-rate-limit-and-account-types
description: Use when building algorithmic trading bots for Canadian markets via Questrade
  API to handle OAuth2 refresh token rotation, account type routing (TFSA, RRSP, Margin),
  and token-bucket rate limiting.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- questrade
- canadian-markets
- oauth2
- rate-limiting
- tfsa-rrsp
brokers_frameworks:
- Questrade API
- Python requests
- Token Bucket Rate Limiter
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever developing algorithmic trading strategies for Canadian equities, ETFs, and options via Questrade's REST API. Questrade requires OAuth2 refresh token rotation, returns dynamic `api_server` URLs per session, enforces distinct rules across account types (`Margin`, `TFSA`, `RRSP`, `FHSA`), and strictly limits API request rates (e.g., max 30 requests/sec). Violating rate limits triggers HTTP 429 errors or temporary IP bans.

## Prerequisites

- Active Questrade account with API access enabled (App Key generated in Questrade Hub).
- Initial Refresh Token from Questrade App registration.
- Account numbers and target account types (`Margin`, `TFSA`, `RRSP`).

## Workflow

1. **OAuth2 Refresh Token Exchange**:
   - Exchange refresh token at `https://login.questrade.com/oauth2/token?grant_type=refresh_token&refresh_token={TOKEN}`.
   - Extract `access_token`, `api_server` (e.g. `https://api01.iq.questrade.com/`), and new `refresh_token`.

2. **Retrieve & Validate Account Types**:
   - Query `GET {api_server}v1/accounts`.
   - Map account numbers to types (`Margin`, `TFSA`, `RRSP`, `FHSA`).
   - Validate trading restrictions (e.g., no short selling or naked options in TFSA/RRSP registered accounts).

3. **Enforce Rate Limits (Token Bucket Algorithm)**:
   - Implement a sliding window token bucket rate limiter (e.g., max 30 calls/second).
   - Throttle outbound API requests before dispatching to avoid HTTP 429 limits.

4. **Order Dispatch & Execution**:
   - Route order to specified `account_id` via `POST {api_server}v1/accounts/{account_id}/orders`.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single-Use Refresh Tokens**: Questrade invalidates refresh tokens once used. The new refresh token must be stored immediately.
- **Short-Selling in TFSA/RRSP**: Attempting short sales or naked options in registered accounts results in broker rejection or tax penalties.
- **Dynamic API Server Endpoints**: Hardcoding Questrade API URLs instead of using the returned `api_server` parameter.

## Verification

- Simulate OAuth2 refresh token exchange and verify `api_server` and `access_token` extraction.
- Submit burst API requests and verify token-bucket rate limiter throttles calls to stay under 30 req/sec.
- Run `python scripts/test_questrade_client.py` and confirm 100% pass rate.

## Related Skills

- `upstox-oauth-refresh-token-rotation`
- `multi-broker-rate-limit-handling`
- `broker-agnostic-adapter-interface`
---
