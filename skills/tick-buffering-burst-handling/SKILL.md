---
name: tick-buffering-burst-handling
description: >-
  Use when sizing in-memory tick buffers so an expiry or news burst neither drops data
  silently nor grows unbounded into an OOM kill. Pairs with
  producer-consumer-tick-pipeline; sustained backlog belongs to
  backpressure-drop-degrade-policy.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, tick-buffering, burst-handling, thread-safety
  brokers_frameworks: "Zerodha Kite Connect v3 (WebSocket streaming); Binance Spot WebSocket Streams; Nasdaq UTP SIP (UQDF/UTDF)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this alongside `producer-consumer-tick-pipeline` specifically to decide buffer sizing and burst-handling behavior. Tick rate for a given watchlist is not constant — it can spike sharply during index expiry, major news, or the first/last 15 minutes of the session — and a buffer sized for average-case load will either overflow (dropping data with no record of what was lost) or, if made unbounded "to be safe," grow until the process is OOM-killed exactly when the bot most needs to be running.

## When NOT to Use

- **When the consumer is never behind the producer.** A buffer that never fills is pure latency and memory. Measure first; if occupancy never leaves single digits, a plain last-value dict is the correct data structure.
- **As a fix for a sustained backlog.** A buffer absorbs *short-term variance*. If occupancy sits near full for seconds at a time, the consumer is too slow and a bigger buffer only adds latency before losing the same data — that is `backpressure-drop-degrade-policy`.
- **For order, fill, or risk events.** Those are not droppable telemetry. A full buffer must never silently evict a fill; route them through a never-drop path (`producer-consumer-tick-pipeline`, `backpressure-drop-degrade-policy`).
- **For cross-process fan-out.** An in-process deque does not survive a process boundary — see `redis-streams-multi-consumer-tick-fanout` or `kafka-based-tick-distribution-at-scale`.
- **As a complete forensic record.** The retained drop-record ring is bounded by design; if a regime requires reconstructing every lost tick, persist records off-process.

## Prerequisites

- Historical tick-rate data for the instruments being traded, ideally including at least one observed high-volatility session, to size buffers empirically rather than guessing — measured on the feed you will actually consume, not a venue-wide consolidated figure (see `references/standards.md`)
- A defined per-instrument or per-strategy priority (some instruments' ticks matter more to drop last than others)
- Knowledge of which thread your broker SDK calls back on, since the buffer is written by the feed thread and read by the strategy thread

## Workflow

1. Measure peak observed tick rate per instrument (ticks/second during known volatile windows — index expiry days, opening 15 minutes) rather than sizing buffers off average-day tick rate; average-case sizing is the single most common cause of buffer overflow during exactly the sessions that matter most. Measure it on your own subscription: a published venue peak (the Nasdaq UTP quote feed peaked at 550,617 messages/sec in 2025 through Q2) and the rate reaching one retail WebSocket differ by orders of magnitude.
2. Set an explicit bounded buffer size per instrument or per symbol group, with the bound based on (peak tick rate × acceptable processing lag tolerance), not an arbitrary round number. Treat the lag tolerance as a tolerance, not a safety margin — doubling it doubles the staleness of the oldest tick a strategy may act on. Then multiply by the symbol count and check the product against host RAM: a per-symbol bound that looks modest across 3,000 subscribed instruments is not.
3. Validate the configured capacity instead of trusting it. A capacity of zero or a per-symbol override whose key does not match the symbol as pushed both fail *silently* — the first discards every tick while reporting 0% occupancy and zero drops, the second leaves the one instrument you deliberately sized for a burst running on the default. Reject bad configuration at construction; the reference implementation raises `BurstBufferConfigError`.
4. Distinguish "buffer" from "backlog": a buffer holds data awaiting processing under normal short-term variance; a sustained backlog (buffer consistently near-full over multiple seconds) is a different condition requiring the escalation logic in `backpressure-drop-degrade-policy`, not just a bigger buffer — buying more buffer only delays the same problem and adds latency to every downstream decision.
5. When a buffer does fill, never silently drop a tick — but bound the audit trail too. Keep exact integer drop counters per symbol (these never lose information) plus a *bounded* ring of recent structured records (symbol, timestamp, buffer state). An unbounded drop log is the same OOM bug one layer up: a saturated buffer drops on every push, so one record per drop grows without limit and pins each dropped tick object, precisely during the burst you were trying to survive. Rate-limit the warning log for the same reason and emit an aggregate count.
6. Make the loss visible in the occupancy report, not just in the log. Occupancy and high-water mark cannot distinguish a buffer that merely ran hot from one that overflowed; report offered, accepted, dropped, and drop-rate per symbol so post-session analysis can answer "was the signal wrong, or was the input data missing?" Take the loss rate against ticks *offered* (one count per push), not against `accepted + dropped`: under keep-latest-N every push is accepted and a drop is the later eviction of a tick already counted as accepted, so summing the two double-counts the burst and understates the loss (20 ticks into a 5-slot buffer is 75% loss, not 42.86%).
7. Guard the buffer with a lock if a feed thread writes while a strategy thread reads. `collections.deque` documents thread-safe *individual* appends and pops, which is not the same as an atomic compound operation: check-then-create a symbol's buffer, or check-full-then-evict, are two-step sequences that race. An unlocked check-then-create lets two threads each build a buffer for the same new symbol, the second assignment wins, and the first thread's ticks vanish while `push()` returns success.
8. Never create buffer state on a read path. `get_latest("UNKNOWN")` must not allocate a buffer, or a monitoring loop over a rotating universe grows manager state without bound and pads the occupancy report with symbols that never traded.
9. Consider whether losing an intermediate tick is actually harmful for the strategy's logic — many strategies only need the latest price/OHLC bar, not every individual tick, in which case a bounded buffer that overwrites rather than queues (keep-latest-N or keep-latest-1 per symbol) is both simpler and correct; reserve full tick-sequence buffering for strategies that genuinely depend on tick-level microstructure.
10. Re-evaluate buffer sizing after any live session that hits a new peak tick rate — this is not a set-once parameter.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Sizing buffers based on a comfortable guess ("1000 ticks ought to be enough") instead of measured peak rates — or off a published venue-wide peak that has no relationship to what one retail subscription actually delivers.
- Making buffers unbounded to "never lose data," which converts a bounded, recoverable backpressure problem into an unbounded memory-growth problem that surfaces as a crash instead of a decision.
- Bounding the buffer but leaving the drop log unbounded. The buffer plateaus, the audit list does not, and the process is still OOM-killed during the burst — with the added irony that the memory is being spent on records of the data you already lost.
- Reading a successful `push()` as "nothing was dropped." Under keep-latest-N the incoming tick is admitted *because* an older one was evicted; the return value describes the new tick, not the buffer's contents. Only the drop counters answer how much was lost.
- Logging every dropped tick. A saturated buffer drops on every push, so per-drop logging makes the log write the next bottleneck; rate-limit the warning and report an aggregate count.
- Dropping ticks with no operator-visible signal at all, making it impossible to later explain a strategy's anomalous signal during a volatile session ("was the signal wrong, or was input data missing?").
- Sharing a buffer between a feed thread and a strategy thread without a lock because "deque is thread-safe." Individual appends and pops are; the compound check-then-act sequences around them are not, and the resulting tick loss is silent and load-dependent — it appears only under the concurrency of a real burst, never in a single-threaded test.
- Buffering full tick sequences for a strategy that only actually consumes latest-price snapshots, wasting memory and adding unnecessary processing lag.

## Verification

- Replay a recorded high-volatility session (index expiry day is a good stress test) through the pipeline and confirm buffer occupancy stays within the sized bound without triggering OOM.
- Confirm memory usage under sustained peak-rate replay plateaus rather than growing linearly with time — and check the drop log and telemetry structures, not just the buffers, since a bounded buffer with an unbounded audit list still grows without limit.
- Push far more ticks than capacity and confirm the exact drop count is recoverable (counters) even though only the most recent records are retained.
- Confirm the occupancy report attributes the loss per symbol (accepted, dropped, drop rate), not merely a global count.
- Construct the manager with a zero/negative capacity and with a mis-cased per-symbol override, and confirm both are rejected rather than silently accepted.
- Run concurrent producers and consumers against the same manager and assert tick conservation: every pushed tick is either still buffered, consumed, or counted as dropped. Assert on totals, not on absence of exceptions — the failure mode is silent loss, not a crash.
- Call the read accessors for symbols that never traded and confirm no buffer state is created.
- Confirm overflow warnings are rate-limited under sustained saturation rather than one line per dropped tick.
- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/tick-buffering-burst-handling/scripts`.

## Related Skills

- `producer-consumer-tick-pipeline`
- `backpressure-drop-degrade-policy`
- `graceful-shutdown-draining-in-flight-ticks`
- `adaptive-sampling-under-extreme-tick-rates`
