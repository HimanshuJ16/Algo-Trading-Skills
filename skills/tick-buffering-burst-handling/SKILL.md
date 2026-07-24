---
name: tick-buffering-burst-handling
description: >-
  Use when sizing and managing in-memory buffers for market data so volatility bursts don't cause unbounded memory growth or silent tick loss
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture"]
brokers_frameworks: []
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this alongside `producer-consumer-tick-pipeline` specifically to decide buffer sizing and burst-handling behavior. Tick rate for a given watchlist is not constant — it can spike 5-10x during index expiry, major news, or the first/last 15 minutes of the session — and a buffer sized for average-case load will either overflow (dropping data with no record of what was lost) or, if made unbounded "to be safe," grow until the process is OOM-killed exactly when the bot most needs to be running.

## Prerequisites

- Historical tick-rate data for the instruments being traded, ideally including at least one observed high-volatility session, to size buffers empirically rather than guessing
- A defined per-instrument or per-strategy priority (some instruments' ticks matter more to drop last than others)

## Workflow

1. Measure peak observed tick rate per instrument (ticks/second during known volatile windows — index expiry days, opening 15 minutes) rather than sizing buffers off average-day tick rate; average-case sizing is the single most common cause of buffer overflow during exactly the sessions that matter most.
2. Set an explicit bounded buffer size per instrument or per symbol group, with the bound based on (peak tick rate × acceptable processing lag tolerance), not an arbitrary round number.
3. Distinguish "buffer" from "backlog": a buffer holds data awaiting processing under normal short-term variance; a sustained backlog (buffer consistently near-full over multiple seconds) is a different condition requiring the escalation logic in `backpressure-drop-degrade-policy`, not just a bigger buffer — buying more buffer only delays the same problem and adds latency to every downstream decision.
4. When a buffer does fill, never silently drop the newest incoming tick without logging it — log a structured record (symbol, timestamp, buffer state) for every drop event so post-session analysis can quantify how much data was lost during a given burst and whether strategy logic that relied on that window is trustworthy.
5. Consider whether losing an intermediate tick is actually harmful for the strategy's logic — many strategies only need the latest price/OHLC bar, not every individual tick, in which case a bounded buffer that overwrites rather than queues (keep-latest-N or keep-latest-1 per symbol) is both simpler and correct; reserve full tick-sequence buffering for strategies that genuinely depend on tick-level microstructure.
6. Re-evaluate buffer sizing after any live session that hits a new peak tick rate — this is not a set-once parameter.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Sizing buffers based on a comfortable guess ("1000 ticks ought to be enough") instead of measured peak rates.
- Making buffers unbounded to "never lose data," which converts a bounded, recoverable backpressure problem into an unbounded memory-growth problem that surfaces as a crash instead of a decision.
- Dropping ticks silently with no logging, making it impossible to later explain a strategy's anomalous signal during a volatile session ("was the signal wrong, or was input data missing?").
- Buffering full tick sequences for a strategy that only actually consumes latest-price snapshots, wasting memory and adding unnecessary processing lag.

## Verification

- Replay a recorded high-volatility session (index expiry day is a good stress test) through the pipeline and confirm buffer occupancy stays within the sized bound without triggering OOM.
- Confirm every drop event (if any occur under the replay) is logged with enough detail to reconstruct exactly what was lost.
- Confirm memory usage under sustained peak-rate replay plateaus rather than growing linearly with time.

## Related Skills

- `producer-consumer-tick-pipeline`
- `backpressure-drop-degrade-policy`
