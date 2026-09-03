---
name: multi-region-active-active-tick-ingestion
description: Use when the same market data stream is ingested simultaneously from two
  or more cloud regions for high availability, and the arbitration layer must forward
  the first-arriving copy of each tick, drop the redundant copies by signature within
  a bounded dedup window, distinguish a cross-region arbitration win from a same-region
  retransmission, and detect a silent region from message flow rather than win rate.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- active-active
- multi-region
- deduplication
- latency-arbitration
- high-availability
brokers_frameworks:
- CME MDP 3.0 (UDP Feed A / Feed B incremental feed arbitration)
- MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) Art. 14
- Multi-region cloud ingest nodes (AWS / GCP)
- Python threaded / asyncio feed handlers
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when **one logical market data stream** is delivered redundantly from two or more geographic regions (e.g. an ingest node in AWS `us-east-1` and another in `us-west-2`, each subscribed to the same venue feed) and something must decide, per tick, which copy reaches the strategy engine. Single-region ingestion is a single point of failure: a regional cloud outage, a fibre cut, or a failed feed-handler deployment silently starves the strategy of data.

The arbitration is the same one venues already prescribe for their own redundant lines. CME's MDP 3.0 guidance is that "UDP Feed A and UDP Feed B should be used for arbitration", with the copy that arrives first processed and the duplicate discarded by packet sequence number. This skill applies that pattern at the region boundary: compute a region-independent signature for each tick, forward the first copy, drop later copies inside a bounded window, and record which region won.

## When NOT to Use

- **For two genuinely independent vendors or venues.** This engine assumes every region carries byte-identical copies of one stream, so a disagreement is impossible by construction — it never compares prices. Two vendors quoting the same instrument disagree routinely and need consensus, divergence tolerance and stale-feed quarantine: `market-data-feed-arbitration-across-vendors`.
- **As a sequence-gap detector.** Arbitration removes duplicates; it cannot recover a message no region delivered. A gap that survives arbitration means the message was lost *everywhere* and needs a retransmission or re-snapshot path — `sequence-number-gap-detection-for-feeds`. The engine surfaces the signal (`emitted_sequence_gap`) but does not recover it.
- **In front of an order-sensitive consumer without re-sequencing.** Output is in *arrival* order, which is not sequence order: if region B wins sequence 6 while region A's copy of sequence 5 is still in flight, 6 is emitted before 5. Order book state machines must re-sequence downstream.
- **For order or execution message deduplication.** Duplicate-suppressing an order submission is a different problem with different failure costs — `order-placement-idempotency`.
- **When the feed carries no per-message unique identity.** If `sequence_id` is absent or constant, the signature degenerates to symbol + price + volume, and two genuine prints of the same size at the same price inside the dedup window are indistinguishable from a duplicate. The second real trade is then silently dropped. Confirm the venue's identity field before deploying.
- **As a failover mechanism for order routing.** Data-plane redundancy says nothing about where orders go — `multi-region-failover-for-broker-connectivity`.

## Prerequisites

- Ingest nodes in at least two distinct regions, each subscribed to the same venue feed and each producing the venue's own per-message identity field (`sequence_id`).
- **A single clock domain for `receipt_time`.** `latency_delta_ms` is the difference between two receipt timestamps. If each regional node stamps on its own host, that difference carries the two hosts' NTP/PTP offset *plus* the forwarding hop to the arbiter, and is not an inter-region feed latency at all. Either stamp both copies on the arbiter host, or discipline both hosts to a common source and validate the residual offset — see `cross-datacenter-clock-sync-validation`.
- A dedup window sized from measured worst-case inter-region arrival spread, and a cache capacity above `ttl_seconds × peak messages/second` summed across regions.
- Confirmation that the venue's market data licence permits a second simultaneous subscription. Running the same feed in two regions is usually two connections and can be two entitlements — see `market-data-entitlement-and-licensing-per-venue`.

## Workflow

1. **Ingest Redundant Regional Copies**:
   - Subscribe each regional node to the same venue feed. Stamp `receipt_time` from the agreed clock domain, not from whichever host happens to handle the message.

2. **Compute a Region-Independent Signature**:
   - Compute $K = \text{MD5}(\text{symbol} : \text{sequence\_id} : \text{price} : \text{volume})$ with price and volume rendered at **full binary precision**, never at a fixed number of decimals.
   - Do **not** include the arrival timestamp. It is a valid identity component only if every region receives it bit-identically; a vendor that re-stamps per region defeats dedup entirely while still passing a single-region test.

3. **Arbitrate and Emit First Arrival**:
   - If $K$ is not in the dedup window, emit the tick to the strategy engine and record $(K, t_{\text{arrival}}, \text{region})$.
   - If $K$ is already present, classify before recording telemetry. A copy from a **different** region is an arbitration win for the region that got there first, and the differential $\Delta t = t_{\text{second}} - t_{\text{first}}$ is meaningful. A copy from the **same** region is a retransmission or a replay-on-reconnect, and is not evidence that the other region is slow.
   - If the later copy carries an *earlier* receipt time than the copy already emitted, arbitration ran out of arrival order or the two timestamps are not from one clock domain. Treat $\Delta t$ as invalid rather than reporting a negative latency.

4. **Bound the Dedup Window in Both Time and Memory**:
   - Expire signatures older than `ttl_seconds` by evicting from the front of an insertion-ordered cache, not by rescanning the whole cache per tick.
   - Enforce a hard entry cap as well. Evicting an entry that is still inside its TTL is a **correctness** event, not just a memory event: the duplicate then re-enters as a fresh first arrival and reaches the strategy twice. Alert on saturation; do not silently absorb it.

5. **Monitor Regional Liveness by Message Flow, Not Win Rate**:
   - Track messages seen, first arrivals, duplicates and last receipt time per region. Flag a region `SILENT` when nothing has arrived within the silence threshold.
   - A win rate cannot do this job. A healthy region that is consistently 2 ms slower wins 0% of the time, and a region that has gone completely dark leaves the survivor at 100% — the same number it showed while both were healthy.
   - Declare the expected regions up front. A node that never connects at all is otherwise indistinguishable from one that was never configured.

6. **Reset at Sequence-Space Boundaries**:
   - Venue sequence spaces are per-channel and reset periodically. Clear arbitration state at the session boundary so recycled sequence numbers are not matched against cached signatures.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rounding Price or Volume Into the Signature**: Formatting the payload at fixed decimals (`f"{price:.4f}"`) merges distinct ticks onto one signature — every price below 0.00005 renders as `"0.0000"`, and 8-decimal crypto sizes collapse wholesale. The second, genuinely different tick is then dropped as a "duplicate" and never reaches the strategy. Silent data loss, invisible in tests using round dollar prices.
- **Treating a Falsy Timestamp as Absent**: `now = receipt_time or time.time()` silently substitutes the wall clock when a caller legitimately passes `receipt_time=0.0`, which epoch-relative replay harnesses do. The recorded arrival is then off by the entire Unix epoch and every latency differential is nonsense.
- **Rescanning the Whole Dedup Cache Per Tick**: Building a list of expired keys by iterating the full cache makes per-tick cost grow linearly with cache size — and under the concurrent ingest this pattern exists for, mutating the cache mid-iteration raises `RuntimeError: dictionary changed size during iteration` in the feed-handler thread.
- **Assuming the Arbiter Is Single-Threaded**: Active-active means one feed-handler thread per region calling the same arbiter. Without a lock, the check-then-insert on the signature cache races and *both* regions emit the same tick as a first arrival — the exact duplicate the component exists to prevent.
- **Unbounded Deduplication Windows**: Retaining signatures with no TTL and no hard entry cap exhausts feed-handler memory. But an entry cap set below `ttl_seconds × peak message rate` is not safe either — it evicts in-window entries and lets duplicates through. Both bounds must be sized, and saturation must alarm.
- **Reading Win Rate as a Health Metric**: A 100% win rate is what a dead partner region looks like *and* what a slightly faster primary looks like. Liveness must come from message counts and last-seen timestamps.
- **Comparing Timestamps Across Unsynchronised Hosts**: A 2 ms clock offset between regional ingest nodes turns a 5 ms differential into 3 ms or 7 ms, and can invert the ordering entirely. The measurement is only as good as the clock discipline between the two stamping hosts.
- **Mistaking a Same-Region Retransmission for an Arbitration Win**: A WebSocket reconnect that replays recent messages produces duplicates from the region that already won. Counting those as cross-region latency observations poisons the differential statistics with zeros and near-zeros.
- **Assuming Dedup Implies Completeness**: Two regions delivering every tick is not the same as every tick being delivered. A message dropped upstream of both regions passes arbitration cleanly — the emitted stream simply skips a sequence number, and nothing in a duplicate filter will tell you.

## Verification

- Submit twin ticks from Region A ($t=0.0\text{s}$) and Region B ($t=0.005\text{s}$); confirm the Region A copy is emitted, the Region B copy is dropped as `CROSS_REGION_DUPLICATE`, and $\Delta t = 5.0\text{ ms}$.
- Pass `receipt_time=0.0` explicitly and confirm it is honoured as a real timestamp rather than replaced by the wall clock.
- Ingest two ticks differing only below the fourth decimal (`0.00001234` vs `0.00004321`) and confirm **neither** is dropped as a duplicate.
- Submit a NaN or infinite price and confirm it is rejected at the boundary, leaving no cache or telemetry state behind.
- Re-deliver a tick from the region that already won and confirm it classifies as `SAME_REGION_DUPLICATE` with no second win credited.
- Hold a signature to exactly `ttl_seconds` and confirm it expires at the boundary; drive the cache past `max_signatures` and confirm the size is capped and saturation is reported.
- Emit sequences 1, 2, then 6 and confirm `emitted_sequence_gap == 3` is surfaced; emit 5 then 4 and confirm `emitted_out_of_order` is set.
- Let one region fall silent and confirm the win rate is *unchanged* while `get_regional_health()` reports `SILENT`; declare a region that never connects and confirm `NEVER_SEEN`.
- Drive two threads through the same 4,000 sequence numbers concurrently and confirm exactly 4,000 emissions, no duplicates, and no exception.
- Run `python -m unittest discover -s skills/multi-region-active-active-tick-ingestion/scripts` and confirm 100% pass rate.

## Related Skills

- `market-data-feed-arbitration-across-vendors`
- `sequence-number-gap-detection-for-feeds`
- `cross-datacenter-clock-sync-validation`
- `clock-skew-correction-for-tick-timestamps`
- `multi-region-failover-for-broker-connectivity`
- `market-data-entitlement-and-licensing-per-venue`
- `kafka-based-tick-distribution-at-scale`
