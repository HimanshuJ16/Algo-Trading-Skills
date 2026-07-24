---
name: token-lifecycle-live-probing
description: >-
  Use when a bot needs to decide whether a cached broker token is still valid before making trading calls — especially for brokers that invalidate tokens outside their documented expiry windows
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "fyers-api-v3", "icici-breeze-api", "zerodha-kite-connect"]
brokers_frameworks: ["Fyers API v3", "ICICI Breeze API", "Zerodha Kite Connect"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever bot startup logic needs to decide "reuse cached token" vs "re-authenticate." Do not use documented expiry timestamps as the sole source of truth for whether a token works — several brokers (Fyers in particular) invalidate tokens overnight via undocumented server-side processes unrelated to the stated expiry, and trusting the timestamp causes the bot to attempt trades with a dead token during market open, the worst possible time to discover an auth failure.

## Prerequisites

- A cached token store (file, Redis, or DB) with token value + issue timestamp
- A designated cheap, side-effect-free API call to use as a probe (e.g., fetch funds/margin, fetch profile) — must be a GET-style read call, never an order-related endpoint

## Workflow

1. On bot startup (or before any trading-critical call after idle periods), retrieve the cached token if present.
2. Immediately fire the designated low-cost probe call using the cached token — do not gate this behind an expiry-timestamp check first; the check itself is the probe, not a timestamp comparison.
3. Classify the probe response into exactly three outcomes, not two:
   - **Valid** (2xx with expected payload shape) → proceed using the cached token.
   - **Explicitly invalid** (auth error code the broker defines, e.g. 401/403 with a token-specific error code) → trigger full re-authentication immediately.
   - **Ambiguous** (timeout, 5xx, network error) → do NOT treat as invalid. Retry the probe with backoff up to a small bounded number of attempts before deciding; broker-side outages should not trigger unnecessary re-logins, which can trip rate limits on the login endpoint itself.
4. On explicit invalidity, invoke the full auth flow (see `headless-broker-auth-patterns`), then re-run the probe once against the new token to confirm before marking the bot ready to trade.
5. Log the token's actual observed lifetime (issue timestamp → invalidation-detected timestamp) over time. This builds an empirical picture of real expiry behavior per broker, which is more reliable than the broker's documentation and lets you tune when to proactively refresh (e.g., proactively re-auth 30 minutes before the empirically observed invalidation point rather than waiting for a failed probe during market hours).
6. Never assume token validity carries over silently across days without a probe — even if the documented expiry says "valid until midnight," probe at bot start regardless.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Checking `if now < token_issued_at + documented_ttl: reuse token` and skipping the live probe entirely — this is the exact anti-pattern this skill exists to prevent.
- Using an order-placement or order-modification call as the "cheap" probe — any call with a side effect risks an accidental live action during what should be a read-only health check.
- Treating a single timeout as proof of invalidity and re-authenticating aggressively, which can itself trigger the broker's login-rate-limit and lock the bot out longer than the original problem.
- Not persisting observed invalidation timing, which means every engineer re-learns "oh, Fyers tokens die randomly overnight" from scratch instead of having monitoring data to act on.

## Verification

- Simulate an expired/invalidated token in a test environment (e.g., manually revoke or use an intentionally malformed token) and confirm the bot detects it via the probe and re-authenticates without manual intervention.
- Confirm the probe call never appears in the broker's order history/audit log (proving it has no trading side effects).
- Check logs after a multi-day run show accurate classification of valid/invalid/ambiguous outcomes with no unnecessary re-logins during a known broker outage window.

## Related Skills

- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
- `paper-to-live-promotion-checklist`
