---
name: crypto-exchange-api-integration
description: >-
  Use when integrating a crypto exchange (Binance, Coinbase Advanced Trade, Kraken) for spot or derivatives trading, where 24/7 markets, different rate-limit models, and exchange-specific order semantics break assumptions carried over from equities broker integration
domain: algorithmic-trading
subdomain: global-market-integration
tags: ["global-market-integration", "binance-spot-futures-api", "coinbase-advanced-trade-api", "kraken-rest-websocket-v2"]
brokers_frameworks: ["Binance Spot/Futures API", "Coinbase Advanced Trade API", "Kraken REST/WebSocket v2"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot needs to place orders or consume market data on a crypto exchange rather than a traditional equities/derivatives broker. Crypto exchanges differ from the equities-broker patterns covered elsewhere in this repo in three structural ways that matter for correctness: markets run 24/7 with no session close to reset daily state against, rate limits are typically weight-based (each endpoint costs a different number of "weight" units against a rolling window) rather than simple request-count limits, and order semantics (time-in-force options, self-trade prevention, post-only flags) vary meaningfully exchange to exchange even for superficially similar order types.

## Prerequisites

- API key + secret with the correct permission scopes (read, trade, withdraw — request only what the bot needs; never grant withdrawal permission to a key used for trading logic)
- Understanding of the specific exchange's weight-based rate-limit accounting (not just "N requests per minute" — most crypto exchanges assign different weights to different endpoints, e.g. an order-book snapshot may cost more weight than a single ticker fetch)
- IP allowlisting configured if the exchange supports it (reduces blast radius if a key leaks)

## Workflow

1. Because there is no daily market close, any logic that assumes a natural "reset point" (e.g. daily P&L resetting at session close, as in `kill-switch-and-drawdown-circuit-breakers`) must define its own reset window explicitly (e.g. rolling 24h or a fixed UTC daily boundary) rather than relying on an exchange-provided session boundary that doesn't exist.
2. Implement weight-based rate limiting distinct from the request-count model in `multi-broker-rate-limit-handling`: track cumulative weight consumed in the exchange's rolling window (commonly 1 minute), not just call count, and back off based on weight headroom, not request headroom — a single order-book-depth call can consume as much weight budget as a dozen ticker calls.
3. Verify self-trade prevention (STP) behavior explicitly: most crypto exchanges offer configurable STP modes (cancel-newest, cancel-oldest, cancel-both) to prevent a bot from trading against its own resting orders; pick a mode deliberately rather than accepting the exchange default, since the default may not match the strategy's intent (e.g. a market-making bot vs a directional bot want different STP behavior).
4. For exchanges offering both a WebSocket user-data stream and a REST order-status endpoint, prefer the WebSocket stream as the primary source of truth for fills (lower latency, less rate-limit pressure) but retain REST reconciliation on reconnect exactly as in `websocket-reconnect-without-duplicate-subscriptions` — user-data streams on crypto exchanges are just as prone to silent gaps around a reconnect as any other WebSocket feed.
5. Handle maintenance windows explicitly: even "24/7" exchanges have scheduled maintenance (and unscheduled outages) during which order placement may be rejected or the WebSocket disconnected for an extended period; the bot's reconnect/backoff logic must not treat an extended crypto-exchange outage as a transient blip requiring aggressive retry, which can itself contribute to (and be penalized by) the exchange's abuse-detection systems.
6. Distinguish spot from margin/futures API namespaces explicitly even when using the "same" exchange — Binance Spot and Binance Futures, for example, are effectively separate API surfaces with separate rate limits, separate WebSocket endpoints, and separate account balances; a bot must never assume a single unified rate-limit budget or balance view across them.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Carrying over a request-count-based rate limiter from equities-broker integration work without adapting to weight-based accounting, causing the bot to hit limits well before its naive request counter suggests it should.
- Assuming a daily P&L/risk reset happens automatically at some session boundary that doesn't exist on a 24/7 market, silently carrying stale "daily" risk-limit state across what the bot incorrectly treats as day boundaries.
- Accepting the exchange's default self-trade-prevention mode without considering whether it matches the strategy's actual intent.
- Treating an extended maintenance-window outage the same as a brief network blip, retrying aggressively enough to trigger the exchange's own abuse/rate-limit escalation.
- Assuming spot and futures/margin balances or rate limits are shared when the exchange treats them as fully separate account/API surfaces.

## Verification

- Confirm the rate limiter's weight accounting matches the exchange's documented weight table for at least the endpoints the bot actually calls, verified by comparing logged cumulative weight against the exchange's returned rate-limit-usage headers (most crypto exchanges echo current weight usage in response headers).
- Simulate a maintenance-window disconnect (or test against the exchange's testnet during an announced maintenance window) and confirm the bot's backoff does not escalate into rapid reconnect attempts.
- Confirm a self-trade scenario (deliberately construct a case where the bot's own buy and sell orders could cross) resolves according to the explicitly chosen STP mode, not a default the team never actually decided on.

## Related Skills

- `multi-broker-rate-limit-handling`
- `websocket-reconnect-without-duplicate-subscriptions`
- `crypto-wallet-key-custody-security`
- `multi-timezone-session-scheduling`
