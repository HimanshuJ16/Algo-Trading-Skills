---
name: multi-broker-rate-limit-handling
description: Use when a bot makes frequent API calls across one or more brokers and
  must avoid rate-limit bans while ensuring risk-critical calls (cancel, kill-switch)
  are never queued behind non-critical data polling
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- fyers-api-v3
- zerodha-kite-connect
- icici-breeze-api
brokers_frameworks:
- Fyers API v3
- Zerodha Kite Connect
- ICICI Breeze API
- Upstox API v2
- Alpaca Trading API
- IBKR API
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when integrating any broker's REST API for anything beyond a handful of manual calls, or when running multiple strategies/instruments that multiply call volume against a single broker account. Each broker enforces different, often per-endpoint, rate limits (e.g., separate limits for order endpoints vs quote endpoints vs historical-data endpoints) — a single global rate limiter is insufficient and will either throttle risk-critical calls unnecessarily or fail to protect against a ban on a specific endpoint.

## Prerequisites

- Documented (or empirically measured) per-endpoint rate limits for each broker in use
- A priority classification for every call type the bot makes (at minimum: kill/cancel > order placement > order status > quotes/data)

## Workflow

1. Build a per-broker, per-endpoint-class rate limiter (token bucket is simplest) rather than one limiter per broker — quote polling and order placement usually have independent limits and conflating them either wastes headroom or risks a ban on the stricter endpoint.
2. Classify every outbound call by criticality tier before it enters any queue:
   - **Tier 0 (immediate, never queued/delayed):** kill-switch triggers, order cancellations tied to risk breaches.
   - **Tier 1 (high priority):** new order placement, order modification.
   - **Tier 2 (normal):** order status polling, position/margin checks.
   - **Tier 3 (best-effort):** market data polling, historical data backfill.
3. Implement the queue so Tier 0 calls bypass rate-limit queuing entirely if the broker's limit allows (most brokers give order/cancel endpoints separate, often more generous, limits than data endpoints specifically because of this use case) — verify this per broker rather than assuming.
4. For Tier 2/3 calls, implement exponential backoff with jitter on 429/rate-limit responses, and cap total backoff so a data-polling backlog cannot starve the bot's ability to eventually check order status.
5. Never let a burst of Tier 3 calls (e.g., polling quotes for 40 correlated instruments) block the queue in front of a Tier 0/1 call — use separate queues per tier, not a single FIFO queue with priority sorting, since sorting doesn't prevent an in-flight low-priority call from occupying the only connection slot when a Tier 0 call arrives.
6. Log every rate-limit rejection with which endpoint/tier it hit — this data is what lets you tune polling frequency down before hitting limits in production rather than discovering the limit via live 429s during market hours.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Applying one rate limiter across all endpoint types, which causes heavy quote polling to consume the same budget as order placement.
- Treating all 429 responses identically regardless of tier — a Tier 3 quote-poll hitting a rate limit should silently back off; a Tier 0 kill-switch cancel hitting a rate limit needs an alert and an alternate path (e.g., broker's mobile app / manual intervention notification), not silent backoff.
- Not accounting for rate limits being enforced per API key/account rather than per bot instance — running two bot processes against the same broker credentials doubles effective call volume against a shared limit that neither process is aware of.
- Backing off so conservatively on Tier 1/2 calls that order-status confirmation is delayed past the point where the reconciliation logic in `order-placement-idempotency` needs a timely answer.

## Verification

- Load-test against the broker's sandbox/paper environment (or a wrapped mock respecting documented limits) with simulated Tier 3 bursts and confirm Tier 0/1 calls still complete within an acceptable latency bound.
- Confirm logs show zero 429s on Tier 0 calls over a multi-day live run, and any Tier 3 429s are followed by successful backoff-retries without cascading failures.
- Confirm two bot instances (if run) sharing one broker account do not collectively exceed the documented per-account limit.

## Related Skills

- `token-lifecycle-live-probing`
- `order-placement-idempotency`
- `kill-switch-and-drawdown-circuit-breakers`
