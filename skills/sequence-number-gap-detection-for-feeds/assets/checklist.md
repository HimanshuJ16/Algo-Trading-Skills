# Pre-Flight / Sign-off Checklist — sequence-number-gap-detection-for-feeds

Use this before considering the skill's implementation complete.

- [ ] **Monotonic Sequence Tracking:** Confirm expected sequence IDs update per channel.
- [ ] **Gap Detection & Buffering:** Confirm missing sequence numbers trigger `DIRTY_SYNC_PENDING` and buffer out-of-order frames.
- [ ] **Re-transmission Integration:** Confirm missing sequence ranges are requested from gap-fill API.
- [ ] **Buffer Drain:** Confirm contiguous buffered frames are emitted in order after gap reconciliation.
- [ ] **Automated Testing:** Run `python scripts/test_gap_detector.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
