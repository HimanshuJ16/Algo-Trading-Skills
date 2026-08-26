# Pre-Flight / Sign-off Checklist — market-data-snapshot-plus-delta-reconciliation

Use this before considering the skill's implementation complete.

- [ ] **Delta Buffering:** Confirm deltas received prior to snapshot application are buffered.
- [ ] **Bounded Buffer:** Confirm the pre-snapshot buffer has a size limit and fails loudly on overflow rather than growing without bound.
- [ ] **Stale Delta Discard:** Confirm deltas with `final_update_id <= last_update_id` are discarded.
- [ ] **Snapshot Freshness Check:** Confirm a snapshot whose `last_update_id` precedes the first surviving buffered `first_update_id` is **rejected** and a fresher snapshot fetched — not silently accepted.
- [ ] **Buffered Delta Continuity:** Confirm buffered deltas are continuity-checked on the way in, not applied blindly.
- [ ] **Zero-Qty Deletions:** Confirm price levels are deleted when volume equals zero, and that negative quantities are rejected as malformed rather than treated as deletions.
- [ ] **Level Validation:** Confirm non-finite (`NaN`/`inf`) and non-positive prices are rejected before reaching the book.
- [ ] **Sequence Gap Fault State:** Confirm `BookState.CORRUPT` is set, book levels are cleared, and a re-snapshot is triggered on sequence gaps.
- [ ] **Re-Sync Buffering:** Confirm deltas arriving while the book is `CORRUPT` are buffered, not dropped, so the replacement snapshot can be freshness-checked.
- [ ] **Crossed Book Guard:** Confirm best bid $\ge$ best ask is surfaced as a desynchronization signal and does not reach trading logic as a tradable quote.
- [ ] **Venue Semantics Confirmed:** Confirm the sequence field, snapshot source (REST vs WebSocket) and continuity rule match the target venue's own documentation — see `references/standards.md`.
- [ ] **Automated Testing:** Run `python scripts/test_order_book_reconciler.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
