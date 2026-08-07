---
name: websocket-reconnect-without-duplicate-subscriptions
description: Use when implementing reconnection logic for a broker market-data WebSocket,
  to avoid duplicate ticks, duplicate subscriptions, or silent gaps in coverage after
  a network blip
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- "websocket-streaming-apis-\u2014-fyers"
- kite
- ibkr
brokers_frameworks:
- "WebSocket streaming APIs \u2014 Fyers"
- Kite
- IBKR
- Alpaca market data streams
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this any time a bot's WebSocket client includes auto-reconnect logic (which any production bot must have, since network blips and broker-side disconnects are routine, not exceptional). Naive reconnect implementations commonly either resubscribe to instruments already subscribed (causing duplicate tick delivery downstream) or fail to resubscribe to everything (causing silent coverage gaps that only surface later as "why did the strategy miss that move").

## Prerequisites

- A single authoritative in-memory (or persisted) list of "instruments the bot intends to be subscribed to" that is independent of the WebSocket connection object itself
- Detection of both clean disconnects (broker closes cleanly) and unclean disconnects (timeout, network drop with no close frame)

## Workflow

1. Maintain the subscription list as its own piece of state, decoupled from the live connection — the connection is disposable and gets recreated on reconnect, but the subscription list persists across reconnects and is the single source of truth for what should be subscribed.
2. On reconnect, do not replay subscription calls incrementally from a log of "subscribe" events issued over the connection's lifetime (this double-counts anything already resubscribed in a previous reconnect cycle) — instead, on every reconnect, subscribe fresh from the authoritative current-state list, and only from that list.
3. Before resubscribing, ensure any state from the previous connection is fully torn down — some broker SDKs retain internal subscription bookkeeping keyed to the old socket object; check the specific SDK's behavior rather than assuming a new connection object starts with clean subscription state.
4. Implement reconnect with exponential backoff and a jitter component to avoid a thundering-herd reconnect pattern if the broker's infrastructure has a broader outage affecting many clients simultaneously.
5. On reconnect, explicitly account for the gap window — ticks that occurred between disconnect and successful resubscription are lost by definition (WebSocket feeds generally don't replay). If the strategy's logic depends on continuous tick coverage, backfill the gap via a REST historical/quote call immediately after reconnect rather than silently proceeding as if no gap occurred.
6. Log every reconnect event with: disconnect timestamp, reconnect timestamp, gap duration, and whether a backfill was performed — this is essential for explaining any strategy anomaly that coincides with a connectivity blip.
7. Deduplicate at the consumer level as a second line of defense (e.g., by tick sequence number or timestamp+symbol) in case a broker's own reconnect handling occasionally double-delivers ticks around the reconnect boundary, independent of subscription-list correctness.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Treating "reconnected successfully" as equivalent to "no data was missed" — the gap window is real and must be explicitly handled or at least explicitly acknowledged in logs, not silently ignored.
- Resubscribing by replaying an append-only log of subscribe calls rather than from current desired state, causing subscription count to grow with every reconnect over a long-running session.
- Not tearing down old connection-object state before reusing SDK objects across reconnects, leading to some brokers' clients silently ignoring new subscribe calls because internal state thinks they're already subscribed.
- Reconnecting instantly with no backoff, which during a genuine broker-side outage affecting many clients can contribute to (and be penalized by) a thundering-herd pattern.

## Verification

- Force a disconnect (kill the network interface or the broker sandbox connection) mid-session and confirm the bot resubscribes to exactly the pre-disconnect instrument set — no more, no fewer — verified by comparing subscription counts before and after.
- Confirm no duplicate ticks appear in downstream processing after a reconnect (verified via sequence number or timestamp+symbol dedup checks in logs).
- Confirm gap-window backfill (if implemented) produces data consistent with what a continuously-connected session would have seen, checked against a reference feed or the broker's own historical data for that window.

## Related Skills

- `producer-consumer-tick-pipeline`
- `token-lifecycle-live-probing`
