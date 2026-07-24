# Deep Workflow Reference — market-data-snapshot-plus-delta-reconciliation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Buffer Delta Stream:**
   - Subscribe to L2 WebSocket stream. Buffer incoming delta updates in `delta_buffer` before snapshot arrival.

2. **Fetch & Apply REST L2 Snapshot:**
   - Query REST endpoint for full L2 snapshot containing `last_update_id` ($S_{\text{snap}}$).
   - Populate local bid/ask dictionaries.

3. **Reconcile Delta Buffer:**
   - Discard buffered deltas with `final_update_id` $\le S_{\text{snap}}$.
   - Apply remaining deltas sequentially.

4. **Process Sequential Real-Time Deltas:**
   - Verify sequence continuity ($S_{\text{new\_first}} \le S_{\text{last}} + 1$).
   - Apply price level updates or remove price levels when quantity equals zero (`qty == 0.0`).

5. **Sequence Gap Recovery Protocol:**
   - On sequence gap ($S_{\text{new\_first}} > S_{\text{last}} + 1$), set `state = BookState.CORRUPT`, clear order book dictionaries, and trigger REST re-snapshot.

## Failure Modes Observed in Production

- **Un-Reconciled Snapshot Application:** Applying snapshot without discarding stale pre-snapshot deltas, corrupting order book price levels.
- **Ghost Level Retention:** Failing to remove price levels when delta quantity drops to zero (`qty == 0`), distorting top-of-book quotes.

## Production Implementation Reference

- Reference code: `scripts/order_book_reconciler.py` (`OrderBookReconciler`, `DeltaUpdate`).
- Automated unit tests: `scripts/test_order_book_reconciler.py`.
