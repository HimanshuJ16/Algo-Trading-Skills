---
name: robinhood-unofficial-api-integration
description: >-
  Production-grade client for Robinhood's unofficial/reverse-engineered API handling device-token OAuth2 authentication with MFA resolution, order placement, and position polling.
domain: Broker Integration & Connectivity
subdomain: Unofficial API Adapters
tags: ["robinhood", "broker-integration", "unofficial-api", "mfa-auth", "oauth2", "order-routing"]
brokers_frameworks: ["Robinhood Unofficial API", "OAuth2 Password Grant", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting automated trading applications to Robinhood accounts using their unofficial/reverse-engineered REST endpoints. Because Robinhood does not provide an official public trading API for retail accounts, retail algorithmic trading requires device-token based OAuth2 password grants, multi-factor authentication (MFA) challenge resolution, and position/order endpoints. Note: Unofficial APIs may break without warning upon broker backend updates.

## Prerequisites

- Robinhood credentials (`email`, `password`, optional `mfa_code`).
- Generated or cached UUID device token (`device_token`).
- Pluggable HTTP transport function (`http_fn`).

## Workflow

1. **OAuth2 Device Token Authentication**:
   - Issue POST request to `/oauth2/token/` with client ID, username, password, and `device_token`.
2. **MFA Challenge Resolution**:
   - If HTTP 400 with `mfa_required` is returned, prompt for MFA code and re-authenticate.
3. **Order Placement**:
   - Place market or limit orders via `/orders/` with time-in-force `gfd` and immediate trigger.
4. **Position Polling & Filtering**:
   - Poll `/positions/` and filter out zero-quantity holdings.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Uncached Device Token**: Generating a new UUID device token on every login triggers repeated MFA challenges and account locks.
- **Unannounced Endpoint Deprecation**: Reverse-engineered endpoints can change schema or break without notice from the broker.
- **Rate Limit Bans**: Polling endpoints too aggressively without rate-limit throttling can cause IP or account bans.

## Verification

- Instantiate `RobinhoodUnofficialClient`. Authenticate with credentials $\implies$ verify access token issued. Test MFA flow $\implies$ verify `MFA_REQUIRED` exception raised without code, succeeds with code. Place market order $\implies$ verify order created in `queued` state. Poll positions $\implies$ verify non-zero positions returned.
- Run `python scripts/test_robinhood_client.py`.

## Related Skills

- `broker-agnostic-adapter-interface`
- `broker-api-deprecation-notice-monitoring`
---
