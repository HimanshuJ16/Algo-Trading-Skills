# Deep Workflow Reference — multi-region-active-active-tick-ingestion

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Ingest Dual-Region Tick Streams**:
   - Ingest tick streams simultaneously from active-active cloud regions (e.g. `us-east-1` and `us-west-2`).

2. **Compute Signature Hash**:
   - Hash tick identity: `MD5(symbol:seq:price:volume)`.

3. **Deduplicate & Emit Fastest Arrival**:
   - If signature is unseen: Emit tick immediately to strategy engine and record `(receipt_time, region_id)`.
   - If signature was seen previously: Discard duplicate tick and log latency delta $\Delta t$.

4. **Regional Win Telemetry**:
   - Compute rolling regional win rates and alert if one region degrades or stops emitting.

## Production Implementation Reference

- Reference code: `scripts/active_active_ingest.py` (`MultiRegionActiveActiveIngestor`, `RegionalTick`, `ActiveActiveIngestResult`).
- Automated unit tests: `scripts/test_active_active_ingest.py`.
