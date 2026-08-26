# Deep Workflow Reference — multi-region-active-active-tick-ingestion

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Establish the clock domain before anything else

Every arbitration statistic this component produces is a difference of two `receipt_time`
values. Decide which of the two topologies you are building, because they measure different
things:

| Topology | Where `receipt_time` is stamped | What `latency_delta_ms` actually measures |
|---|---|---|
| **Central arbiter** | The arbiter host stamps both regional copies on arrival | The difference in end-to-end delivery latency from each region *to the arbiter*, on one clock. Directly comparable. |
| **Distributed stampers** | Each regional ingest node stamps locally, then forwards to the arbiter | The regional delivery difference **plus** the two hosts' clock offset **plus** the difference in forwarding-hop latency. Not an inter-region feed latency. |

If you must use distributed stampers, discipline both hosts to a common time source and
validate the residual offset continuously (`cross-datacenter-clock-sync-validation`); a 2 ms
offset turns a 5 ms differential into 3 ms or 7 ms and can invert the ordering entirely.
Never mix the two conventions on one ingestor instance.

## Full Procedure

1. **Ingest redundant regional copies**:
   - Subscribe an ingest node in each region to the same venue feed.
   - Declare the expected regions up front (`expected_regions=[...]`). A node that never
     connects at all is otherwise indistinguishable from one that was never configured, and
     no amount of telemetry on the regions that *did* connect will reveal it.

2. **Compute the signature**:
   - `MD5(symbol:sequence_id:price:volume)`, with `symbol` upper-cased and trimmed, and with
     price and volume rendered via `float.hex()` so no precision is lost.
   - Reject non-finite price/volume at the boundary. `NaN` and `inf` render identically under
     every fixed format, so they would collapse unrelated ticks onto a single signature.
   - Do **not** include the timestamp — see `references/standards.md`.
   - Where the venue's sequence number is absent or constant, stop. The signature degenerates
     to symbol + price + volume, and two genuine prints of the same size at the same price
     inside the window will be indistinguishable from a duplicate.

3. **Arbitrate**:
   - Signature unseen → emit to the strategy engine, record `(receipt_time, region_id)`,
     credit the region with an arbitration win.
   - Signature seen, **different** region → `CROSS_REGION_DUPLICATE`. Drop it and record
     $\Delta t = t_{\text{second}} - t_{\text{first}}$ as a genuine latency observation.
   - Signature seen, **same** region → `SAME_REGION_DUPLICATE`. Drop it, but do not record a
     latency observation: this is a retransmission or a replay-on-reconnect, and folding those
     near-zero deltas into the differential statistics makes the partner region look slower
     than it is.
   - Later copy carries an *earlier* receipt time → set `arrival_order_inverted`. Ticks were
     handed to the arbiter out of arrival order, or the two timestamps are not from one clock
     domain. Do not publish the negative delta as a latency.

4. **Bound the window in time and in memory**:
   - Expire by front-eviction of an insertion-ordered cache: pop while the head entry is older
     than `ttl_seconds`. Per-tick cost stays flat as the cache grows.
   - Enforce `max_signatures` as a hard cap. **Saturation is a correctness event**: an evicted
     in-window entry means the duplicate copy re-enters as a fresh first arrival and reaches
     the strategy twice. Alarm on `get_dedup_cache_stats()["saturated"]`; do not treat it as a
     benign memory-pressure signal.
   - Size the cap from `ttl_seconds × peak messages/second × regions`, with headroom for burst.

5. **Report post-arbitration sequence continuity**:
   - `emitted_sequence_gap > 0` means that many messages were delivered by **no** region.
     Arbitration cannot recover them; escalate to the venue's retransmission or re-snapshot
     path.
   - `emitted_out_of_order` means the emitted sequence did not advance — either arrival order
     diverged from sequence order (normal for first-arrival arbitration) or the sequence space
     reset. Order-sensitive consumers must re-sequence downstream.

6. **Monitor liveness**:
   - `get_regional_health(now=...)` classifies each region `ACTIVE` / `SILENT` / `NEVER_SEEN`
     from message counts and last receipt time. Pass `now` from the same clock domain as
     `receipt_time`.
   - `get_regional_win_statistics()` reports both a lifetime and a rolling win percentage.
     Use the rolling figure for latency drift, and never either figure for liveness: a healthy
     region that is consistently 2 ms slower wins 0%, and a dead partner leaves the survivor at
     100% — indistinguishable from normal operation.

7. **Reset at sequence-space boundaries**:
   - Call `reset()` at the session boundary or whenever the venue recycles sequence numbers, so
     a recycled number is not matched against a cached signature and the continuity flags do
     not report a spurious regression.

## Failure-mode drill list

Exercise each of these against a staging arbiter before promoting:

| Scenario | Expected behaviour |
|---|---|
| One region's ingest node killed mid-session | Win rate unchanged; health flips to `SILENT` within the threshold; ticks keep flowing from the survivor |
| Both regions killed | No emissions; both regions `SILENT` — this is the case a single-region deployment cannot distinguish from a quiet market |
| Region reconnects and replays the last N messages | Replays classify as `SAME_REGION_DUPLICATE`; no spurious arbitration wins |
| Message dropped upstream of both regions | Emitted stream shows `emitted_sequence_gap > 0`; retransmission path triggered |
| Message rate spikes past the cache cap | `saturated` true and alarmed, rather than silent duplicate leakage |
| Clock on one regional stamper stepped by +2 ms | Differentials shift or invert; `arrival_order_inverted` appears — the signal that the clock domain assumption broke |

## Production Implementation Reference

- Reference code: `scripts/active_active_ingest.py`
  (`MultiRegionActiveActiveIngestor`, `RegionalTick`, `ActiveActiveIngestResult`,
  `ArbitrationOutcome`, `RegionHealth`, `RegionStatus`).
- Automated unit tests: `scripts/test_active_active_ingest.py`.
