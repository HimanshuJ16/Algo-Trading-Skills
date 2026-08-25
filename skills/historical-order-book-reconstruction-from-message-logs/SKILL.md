---
name: historical-order-book-reconstruction-from-message-logs
description: >-
  Replay raw Level 3 (market-by-order) message logs — Add, Cancel, Delete, Execute, Replace — into Level 2 aggregated price-level depth and BBO state, with explicit detection of the log gaps that silently corrupt a reconstructed book.
domain: Data Management Global
subdomain: Market Microstructure & Order Book Reconstruction
tags: ["order-book", "level-3-itch", "level-2-depth", "message-reconstruction", "market-microstructure", "bbo", "book-integrity"]
brokers_frameworks: ["Nasdaq TotalView-ITCH 5.0", "LOBSTER Academic Data", "CME MDP 3.0 Market by Order", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill for market microstructure research, high-frequency backtesting, and queue-position simulation, when the input is a market-by-order (L3) message log rather than pre-aggregated depth snapshots. Feeds such as Nasdaq TotalView-ITCH 5.0, LOBSTER message files, and CME MDP 3.0 Market by Order publish individual order lifecycle events; the aggregated L2 book that a strategy actually reasons about has to be rebuilt from them, event by event.

The engine maintains the L3 order map and the L2 price-level aggregation together, so taking a snapshot after every message — the normal way to avoid lookahead bias in an event-driven backtest — stays affordable.

The second job this skill does is **telling you when the reconstruction is wrong**. A message log with a gap in it still replays without error; it just produces a book that quietly disagrees with the real one. Every anomaly that implies divergence is counted and surfaced rather than absorbed.

## When NOT to Use

- **When your feed already publishes aggregated depth.** For L2 snapshot+delta feeds, reconcile rather than reconstruct — see `market-data-snapshot-plus-delta-reconciliation`.
- **As a gap-detection mechanism for a live feed.** Book-integrity violations here are a *consequence* of dropped messages, detected after the fact. Sequence-number gaps are detected at the transport layer (MoldUDP64/SoupBinTCP) before the book is touched — see `sequence-number-gap-detection-for-feeds`.
- **For hidden liquidity, cross/auction prints, or halts.** This reconstructs the *displayed* book only. LOBSTER event types 5 (hidden execution), 6 (cross trade) and 7 (halt), and Nasdaq Trade `P` / Cross Trade `Q` messages, do not decrement a resting displayed order. Feeding them in as `EXECUTE` corrupts the book.
- **For true queue-position simulation on its own.** This tracks displayed size and order count per level, not per-order queue rank. A `REPLACE` re-enters at the back of the queue; modelling that requires `queue-position-modeling-for-passive-orders`.
- **For multi-symbol replay in one instance.** One engine holds one symbol's book and does not filter by symbol; shard by symbol upstream.

## Prerequisites

- A **chronologically ordered** L3 message stream. Ordering is the correctness precondition — the engine flags a timestamp regression but cannot repair one.
- Per message: `order_id`, `msg_type` (`ADD`, `CANCEL`, `DELETE`, `EXECUTE`, `REPLACE`), `timestamp_nanos`, plus the fields that message type carries (see the table in `L3OrderMessage`).
- The feed's **price precision**. `price_scale` must match it: `10_000` for Nasdaq ITCH `Price (4)` and LOBSTER, `100` for a cent-quoted feed. Optionally set `max_price` to reject implausible prices caused by a scaling mistake.
- Target depth level count for snapshots (e.g. top 5 or 10).

## Workflow

1. **Map the source feed's message types onto the five canonical types.**
   - **Decision point — `CANCEL` and `DELETE` are not the same message.** Nasdaq ITCH `X` (Order Cancel) carries a Canceled Shares count and is a *partial* reduction; `D` (Order Delete) carries no share count and removes all remaining shares. LOBSTER splits them the same way (type 2 vs type 3). Mapping a total deletion onto `CANCEL` requires inventing a share count, which is exactly the fabrication this engine refuses.
   - Route hidden executions, cross prints and halts *away* from the engine.

2. **Replay messages in order, one at a time.**
   - `ADD` inserts a new resting order at a price level.
   - `CANCEL` / `EXECUTE` **deduct** shares; effects are cumulative and an order that reaches zero shares is removed.
   - `DELETE` removes the whole order.
   - `REPLACE` removes the original and inserts the replacement.
   - **Decision point — a `REPLACE` changes the order's identity.** ITCH `U` carries an *Original* and a *New* Order Reference Number, and "the NASDAQ system will use this new order reference number for all subsequent updates". Keying the replacement under the old id makes every later message for that order look like a gap.
   - **Decision point — a `REPLACE` does not carry the side.** It cannot change the side, so the message omits it; the side must be inherited from the original `ADD`. Taking it from the replace message lets a caller flip a bid onto the ask book.
   - **Decision point — `REPLACE` quantity is absolute, not a delta.** ITCH calls the field "the new total displayed quantity", unlike the deduction semantics of `CANCEL`/`EXECUTE`.

3. **Classify every anomaly before trusting the book.** Malformed input (unknown message type, non-positive quantity, non-finite price, unrecognised side) raises immediately. A well-formed message that cannot be applied to a consistent book is an *integrity violation*, counted in `violations_by_kind`:
   - `UNKNOWN_ORDER` — cancel/delete/execute/replace for an order never added.
   - `DUPLICATE_ORDER_ID` — an `ADD` for an already-live id (reference numbers are day-unique).
   - `OVER_CANCEL` / `OVER_EXECUTE` — more shares removed than were resting.
   - `TIMESTAMP_REGRESSION` — the log went backwards.
   - **Decision point — decide the policy before the run, not after.** `strict=True` raises on the first violation (right for a validated production pipeline); the default records and continues (right for exploratory replay of an imperfect archive). Either way, **a non-zero violation count invalidates any microstructure statistic computed from that replay** — do not report it as clean.

4. **Snapshot the L2 book.** Aggregate into bids (descending) and asks (ascending), take the top *N* levels, and compute BBO, mid-price and spread.
   - **Decision point — check `is_crossed_book` / `is_locked_book` before using the mid.** A crossed book yields a *negative* spread and a meaningless mid; both fields are still populated, so an unguarded consumer will silently ingest them.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silently absorbing a cancel for an order you never saw.** The natural implementation is `if order_id in book:` — which turns a dropped `ADD` into a no-op and leaves a book that is wrong for the rest of the session with nothing in the output saying so. Every unmatched message is a divergence and must be counted.
- **Fabricating an order out of a `REPLACE`.** Creating the replacement when the original is absent invents depth that never rested on the book — and the side cannot even be known, because the replace message does not carry it. A reported gap is strictly better than invented liquidity.
- **Treating `CANCEL` as a full delete.** Conflating ITCH `X` with `D` removes an order that still had displayed size, deleting real depth at the touch.
- **Getting the price scale wrong in either direction.** A price divided by the scale twice becomes sub-tick and is rejected; a raw wire integer passed straight through is *not* — `1000000` is the ITCH `Price (4)` encoding of $100.00 but reads as a perfectly plausible $1,000,000.00 book. Only an explicit `max_price` catches that direction.
- **Using floats as price-level keys.** `102.4 + 0.7` is `103.10000000000001`, not `103.1`. Two orders genuinely quoted at the same tick then land in different dictionary buckets and split one price level in two — in a direct test of the previous implementation this reported 2 bid levels and a best-bid size of 10 where the correct answer was 1 level of 15, a 33% understatement of depth at the BBO. Prices are integers on the wire (`Price (4)`, "dollar price times 10000"); keep them integers internally.
- **Accepting an unrecognised side.** `bid_map if side == "BUY" else ask_map` routes `"B"`, `"Buy "`, `""` and every typo onto the ask book, manufacturing a crossed book out of a single well-formed order.
- **Carrying an unused timestamp field.** L3 replay is order-dependent; a `timestamp_nanos` field that nothing ever reads gives a false impression that ordering is being checked. Note that *equal* timestamps are legal — multiple ITCH messages share a nanosecond — so only a strict regression is an error.
- **Rebuilding the whole book on every snapshot.** Re-aggregating all live orders per snapshot is O(N) per message, so tick-by-tick replay degrades to O(N·M) — the same complexity blow-up that an unindexed order lookup causes, just moved to the snapshot path. Measured on a 20,000-order book with a snapshot after every message: 52.7s rebuilding versus 0.68s maintaining the aggregation incrementally.
- **Reading `mid_price` without checking the crossed flag.** A crossed book produces a negative spread and a mid that sits between two prices that cannot both be real.
- **Treating a clean replay as a validated one.** No exception raised means only that no message was malformed. Check `integrity_violation_count`.

## Verification

- Instantiate `HistoricalOrderBookReconstructEngine(symbol="AAPL")`. Replay `ADD BUY ID_1 @ 100.0 x10`, `ADD BUY ID_2 @ 100.0 x5`, `ADD SELL ID_3 @ 101.0 x8`, `CANCEL ID_1 x4`. Verify Best Bid $= 100.0$ (Qty $11$, order count $2$), Best Ask $= 101.0$ (Qty $8$), Mid $= 100.50$, Spread $= 1.00$, and `integrity_violation_count == 0`.
- **Semantics**: a `REPLACE` from `OLD` to `NEW` must leave `NEW` on the book and `OLD` off it, must set the size to the replace message's absolute quantity, and must keep the *original* side even when the message carries the opposite one.
- **Integrity**: a cancel, delete, execute or replace for an unknown order must increment `UNKNOWN_ORDER` and add no depth; over-cancelling must increment `OVER_CANCEL`; a duplicate `ADD` must increment `DUPLICATE_ORDER_ID` and leave exactly one order; a backwards timestamp must increment `TIMESTAMP_REGRESSION` while equal timestamps must not. `strict=True` must raise `BookIntegrityError` on the first of these.
- **Numerics**: orders at `102.4 + 0.7` and `103.1` must aggregate into one level of size 15; a locked book (bid == ask) must report `is_locked_book` and not `is_crossed_book`.
- **Consistency**: after a mixed replay of adds, cancels, executes, deletes and replaces, the incrementally maintained level aggregation must equal a from-scratch rebuild off the order map.
- Run `python -m unittest discover -s skills/historical-order-book-reconstruction-from-message-logs/scripts` and confirm 43/43 pass.

## Related Skills

- `nasdaq-totalview-itch-feed-parsing`
- `sequence-number-gap-detection-for-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `queue-position-modeling-for-passive-orders`
- `order-book-microstructure-signal-research`
- `historical-tick-data-storage-and-compaction`
