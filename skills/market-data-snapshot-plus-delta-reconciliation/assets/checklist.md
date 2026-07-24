# Pre-Flight / Sign-off Checklist — market-data-snapshot-plus-delta-reconciliation

Use this before considering the skill's implementation complete.

- [ ] **Delta Buffering:** Confirm deltas received prior to snapshot application are buffered.
- [ ] **Stale Delta Discard:** Confirm deltas with `final_update_id <= last_update_id` are discarded.
- [ ] **Zero-Qty Deletions:** Confirm price levels are deleted when volume equals zero.
- [ ] **Sequence Gap Fault State:** Confirm `BookState.CORRUPT` is set and re-snapshot is triggered on sequence gaps.
- [ ] **Automated Testing:** Run `python scripts/test_order_book_reconciler.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
