# Deep Workflow Reference — sequence-number-gap-detection-for-feeds

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Track Monotonic Sequence IDs**:
   - Maintain $S_{\text{expected}} = S_{\text{last}} + 1$ for each feed channel.

2. **Evaluate Ingested Frame**:
   - In-order ($S = S_{\text{expected}}$): Process frame and drain contiguous out-of-order buffer.
   - Gap ($S > S_{\text{expected}}$): Buffer out-of-order frame, transition to `DIRTY_SYNC_PENDING`, and calculate missing sequence range $[S_{\text{expected}}, S - 1]$.
   - Stale ($S < S_{\text{expected}}$): Ignore duplicate/stale frame.

3. **Gap-Fill Re-Transmission**:
   - Issue historical API re-transmission query for missing sequence range.
   - Ingest missing frames and drain out-of-order buffer in sequence.

## Production Implementation Reference

- Reference code: `scripts/gap_detector.py` (`SequenceGapDetector`, `FeedSyncState`, `FeedFrame`, `GapDetectionResult`).
- Automated unit tests: `scripts/test_gap_detector.py`.
