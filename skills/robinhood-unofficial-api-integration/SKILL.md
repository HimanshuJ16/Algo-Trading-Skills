---
name: robinhood-unofficial-api-integration
description: Use when integrating Robinhood via its unofficial API for algorithmic
  trading, with explicit acknowledgment of Terms of Service risk, authentication via
  device token and MFA challenge, and order placement/status polling patterns.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- robinhood
- unofficial-api
- mfa-auth
- commission-free
brokers_frameworks:
- Robinhood (unofficial)
- robin_stocks
- Python requests
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building a trading bot targeting Robinhood's commission-free platform.
Robinhood does **not** provide an official public API — all integrations rely on reverse-engineered
unofficial endpoints. This carries inherent risk:
- API may break without warning on any Robinhood app update.
- May violate Robinhood's Terms of Service; account suspension is possible.
- No official support or SLA.

This skill covers authentication (device token + MFA), order placement, position polling,
and structured error handling for the unofficial API.

## Prerequisites

- Robinhood account credentials (email/password).
- MFA device token or TOTP authenticator setup.
- Understanding of ToS risk and willingness to accept it.

## Workflow

1. **Authenticate**: Login with email/password + MFA challenge/response.
2. **Obtain Bearer Token**: Store short-lived OAuth2 bearer token with refresh.
3. **Query Positions**: Poll `/positions/` endpoint for current holdings.
4. **Place Orders**: Submit market/limit orders via `/orders/` endpoint.
5. **Poll Order Status**: Check fill status since WebSocket feeds are unavailable.
6. **Handle Token Expiry**: Auto-refresh bearer token before expiry.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **MFA Challenge Loop**: Not caching the device token causes repeated MFA prompts.
- **Rate Limiting**: Unofficial API has undocumented rate limits that cause 429 errors.
- **API Breaking Changes**: Endpoint paths/schemas change without notice.
- **ToS Violation Risk**: Automated trading may trigger account review or suspension.

## Verification

- Simulate authentication with MFA challenge and verify token acquisition.
- Simulate order placement and verify correct payload construction.
- Run `python scripts/test_robinhood_client.py` and confirm 100% pass rate.

## Related Skills

- `headless-broker-auth-patterns`
- `broker-agnostic-adapter-interface`
- `token-lifecycle-live-probing`
---
