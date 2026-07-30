---
name: upstox-oauth-refresh-token-rotation
description: Use when implementing Upstox API v2 OAuth2 token management to handle
  single-use refresh token rotation, thread-safe token persistence, and seamless session
  renewal for long-running trading bots
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- upstox-api-v2
- oauth2
- token-rotation
- refresh-token
brokers_frameworks:
- Upstox API v2
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a trading bot integrates with Upstox API v2 for automated trading or market data streaming. Upstox enforces OAuth2 refresh token rotation: each time a refresh token is exchanged for a new access token, the broker invalidates the old refresh token and issues a brand-new refresh token in the response payload. If a long-running bot fails to persist the new refresh token atomically or allows concurrent execution threads to attempt multi-use refreshes, the session will be abruptly invalidated, requiring manual re-authentication during live market hours.

## Prerequisites

- Upstox API v2 `API_KEY` and `API_SECRET`.
- Secure persistent token store (JSON file, SQLite, or Redis) with read/write permissions.
- Thread-safe or async lock mechanism for token refresh operations.

## Workflow

1. **Load Current Token State**:
   - Retrieve stored `access_token`, `refresh_token`, and `expires_at` timestamp from local vault or token store.

2. **Preemptive Token Expiry Inspection**:
   - Check if current `access_token` is near expiration ($T_{\text{now}} \ge T_{\text{expires}} - 900\text{s}$). If valid, proceed using existing token.

3. **Thread-Safe Refresh Token Rotation**:
   - Acquire exclusive lock (`threading.Lock` or `asyncio.Lock`) before initiating token refresh to prevent concurrent worker threads from reusing the old single-use refresh token.

4. **Issue Exchange Request & Atomic Persistence**:
   - POST request to Upstox token endpoint (`https://api.upstox.com/v2/login/auth/token`).
   - Extract `access_token` and `refresh_token` from JSON response.
   - Atomically overwrite persistent token storage with new tokens BEFORE releasing execution lock.

5. **Fallback & Emergency Re-Authentication**:
   - If token exchange returns `invalid_grant` or `401 Unauthorized`, flag session as unrecoverable and invoke headless re-authentication workflow (`headless-broker-auth-patterns`).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reusing Rotated Refresh Tokens**: Attempting to call the refresh endpoint twice with the same refresh token, triggering immediate invalidation of all active tokens.
- **In-Memory-Only Refresh Storage**: Keeping new refresh tokens in RAM without writing to disk, losing valid tokens on bot restarts.
- **Non-Atomic Token Swaps**: Crashes or process terminations occurring between receiving new tokens and updating persistent storage.
- **Concurrent Thread Race Conditions**: Multiple async strategy workers triggering simultaneous token refreshes.

## Verification

- Perform a mock token refresh cycle and confirm old `refresh_token` is replaced by `new_refresh_token` in persistent storage.
- Simulate concurrent worker threads requesting tokens simultaneously and verify only 1 HTTP refresh request is executed.
- Simulate `invalid_grant` failure and verify emergency re-auth callback is invoked.
- Run unit test suite `python scripts/test_upstox_auth.py` and confirm 100% pass rate.

## Related Skills

- `token-lifecycle-live-probing`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
---
