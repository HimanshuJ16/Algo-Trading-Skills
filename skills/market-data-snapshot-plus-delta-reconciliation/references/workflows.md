# Deep Workflow Reference — market-data-snapshot-plus-delta-reconciliation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

The procedure below follows Binance spot's *How to manage a local order book correctly*.
Venue differences (futures `pu`, WebSocket-delivered snapshots on Bybit and Coinbase
Advanced Trade) are tabulated in `references/standards.md` — read it before porting this
procedure to another venue.

## Full Procedure

1. **Buffer Delta Stream:**
   - Subscribe to the L2 WebSocket stream. Buffer incoming delta updates in `delta_buffer`
     before the snapshot arrives, and note the `first_update_id` ($U$) of the first event.
   - Bound the buffer. A snapshot that never arrives must fail loudly rather than grow the
     queue until the feed-handler process dies.

2. **Fetch REST L2 Snapshot:**
   - Query the REST endpoint for a full L2 snapshot containing `last_update_id`
     ($S_{\text{snap}}$).
   - Populate local bid/ask dictionaries from the snapshot levels.

3. **Discard Stale Buffered Deltas:**
   - Drop buffered deltas with `final_update_id` $\le S_{\text{snap}}$; the snapshot already
     contains their effect.

4. **Validate Snapshot Freshness (decision point):**
   - Take the first *surviving* buffered delta. Require
     $S_{\text{first\_update}} \le S_{\text{snap}} + 1 \le S_{\text{final\_update}}$.
   - **If it fails**, the snapshot predates the buffered stream. Do not mark the book
     synchronized. Discard the snapshot, **keep the buffer**, and fetch a fresher snapshot.
     Re-fetching is the whole recovery — resubscribing would throw away the buffer that
     proves the next snapshot is fresh enough.
   - A delta that straddles the boundary ($U \le S_{\text{snap}} + 1 \le u$) is a valid first
     event and must be applied in full, not treated as stale.

5. **Apply Buffered Deltas With Continuity Checks:**
   - Apply the surviving buffer through the same continuity rule used for live deltas.
   - A gap *inside the buffer* ($S_{\text{new\_first}} > S_{\text{last}} + 1$) has a different
     cause and a different fix from a stale snapshot: the WebSocket stream itself dropped
     messages, so discard the buffer and restart the subscription.

6. **Process Sequential Real-Time Deltas:**
   - Verify sequence continuity ($S_{\text{new\_first}} \le S_{\text{last}} + 1$).
   - Skip fully superseded events ($S_{\text{final}} \le S_{\text{last}}$).
   - Apply price level updates, removing price levels when quantity equals zero
     (`qty == 0.0`). Quantities are absolute level sizes, not increments.

7. **Sequence Gap Recovery Protocol:**
   - On a gap, set `state = BookState.CORRUPT`, clear the order book dictionaries so no
     consumer can read stale depth, and trigger a re-snapshot.
   - Retain the offending delta as the new buffer head and keep buffering subsequent deltas
     throughout the re-sync, so step 4 can be run against the replacement snapshot.

## Failure Modes Observed in Production

- **Stale Snapshot Accepted Silently:** Applying a snapshot whose `last_update_id` precedes
  the first buffered `U`. Every subsequent continuity check passes, so no alarm ever fires
  and the book stays wrong until the process restarts. This is the failure mode step 4
  exists to prevent.
- **Un-Reconciled Snapshot Application:** Applying the snapshot without discarding stale
  pre-snapshot deltas, corrupting order book price levels.
- **Blindly Applied Buffer:** Skipping continuity checks on buffered deltas on the assumption
  that buffering preserved order. It preserves order, not completeness.
- **Deltas Dropped During Re-Sync:** Discarding messages while the book is `CORRUPT`, which
  leaves the replacement snapshot with nothing to be freshness-checked against.
- **Ghost Level Retention:** Failing to remove price levels when delta quantity drops to zero
  (`qty == 0`), distorting top-of-book quotes.
- **Crossed Local Book Traded As Signal:** Best bid $\ge$ best ask in the *local* book is not
  an arbitrage; venues do not disseminate crossed books, so it is evidence the book has
  desynchronized and must be rebuilt.
- **Non-Finite Levels Poisoning the Book:** A `NaN` price inserted as a dictionary key can
  never be removed by a later `pop` and silently corrupts `max()`/`min()` top-of-book
  selection. Validate levels at the parse boundary.

## Production Implementation Reference

- Reference code: `scripts/order_book_reconciler.py` (`OrderBookReconciler`, `DeltaUpdate`).
  Failure paths raise `OrderBookError`; malformed input raises `ValueError`.
- Automated unit tests: `scripts/test_order_book_reconciler.py`.
