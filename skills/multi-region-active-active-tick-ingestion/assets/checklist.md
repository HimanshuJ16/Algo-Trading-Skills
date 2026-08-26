# Pre-Flight / Sign-off Checklist — multi-region-active-active-tick-ingestion

Use this before considering the skill's implementation complete.

- [ ] **Redundant, Not Independent:** Confirm every region carries copies of the **same** logical stream. Two independent vendors need `market-data-feed-arbitration-across-vendors`, not this.
- [ ] **Clock Domain Declared:** Confirm every `receipt_time` on one ingestor instance comes from one clock. If regional hosts stamp locally, confirm their residual offset is measured and bounded.
- [ ] **Signature Precision:** Confirm price and volume enter the signature at full binary precision — no fixed-decimal formatting that could merge two distinct ticks.
- [ ] **Timestamp Excluded:** Confirm the arrival/exchange timestamp is not part of the signature, or that every region is verified to deliver it bit-identically.
- [ ] **Identity Field Exists:** Confirm the venue supplies a per-message `sequence_id`. Without it, two genuine same-price/same-size prints inside the window are indistinguishable from a duplicate.
- [ ] **Malformed Input Rejected:** Confirm non-finite prices/volumes, empty symbols and non-integer sequence IDs raise before any cache or telemetry state is mutated.
- [ ] **Explicit Zero Timestamp:** Confirm `receipt_time=0.0` is honoured as a real timestamp rather than falling back to the wall clock.
- [ ] **First-Arrival Selection:** Confirm the earliest-arriving copy is forwarded to the trading engine and later copies are dropped.
- [ ] **Duplicate Classification:** Confirm cross-region duplicates and same-region retransmissions are distinguished, and that a region is never credited with beating itself.
- [ ] **Inverted Arrival Flagged:** Confirm a "duplicate" carrying an earlier receipt time is flagged rather than published as a negative latency.
- [ ] **TTL Bound:** Confirm signatures expire at `ttl_seconds`, and that the TTL was sized from measured worst-case inter-region spread.
- [ ] **Memory Bound:** Confirm a hard `max_signatures` cap exists **and** that it exceeds `ttl_seconds × peak messages/second × regions`. Confirm saturation raises an alarm — it means duplicates are leaking through as first arrivals.
- [ ] **Eviction Cost:** Confirm expiry does not rescan the whole cache per tick (per-tick cost must stay flat as the cache grows).
- [ ] **Concurrency:** Confirm concurrent ingest from two feed-handler threads emits each tick exactly once and raises no exception.
- [ ] **Gaps Escalated, Not Hidden:** Confirm `emitted_sequence_gap > 0` is routed to a retransmission / re-snapshot path. A gap surviving arbitration means the message was lost in every region.
- [ ] **Downstream Re-Sequencing:** Confirm order-sensitive consumers re-sequence, since first-arrival output is in arrival order, not sequence order.
- [ ] **Liveness Not Win Rate:** Confirm region-down alerting is driven by message counts / last-seen time, and that expected regions are declared so a never-connecting node is detectable.
- [ ] **Sequence-Space Reset:** Confirm `reset()` is called at session boundaries where the venue recycles sequence numbers.
- [ ] **Market Data Entitlement:** Confirm the venue's licence permits a simultaneous second-region subscription.
- [ ] **Failure Drills Run:** Confirm each row of the drill table in `references/workflows.md` was exercised against staging.
- [ ] **Automated Testing:** Run `python scripts/test_active_active_ingest.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
