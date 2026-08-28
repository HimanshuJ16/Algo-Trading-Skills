---
name: order-book-depth-processing-l2-l3
description: Use when maintaining a local Level 2 (price-aggregated) or Level 3 (order-by-order)
  order book from a live depth feed and deriving top-of-book, weighted mid-price and depth
  imbalance from it under thread-safe mutation, with crossed/locked book detection and
  explicit book-integrity accounting
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- order-book-l2-l3
- book-imbalance
- weighted-midprice
- thread-safety
brokers_frameworks:
- Nasdaq TotalView-ITCH 5.0
- CME MDP 3.0 (SBE)
- Coinbase Exchange level3 / full
- Binance Spot Diff. Depth Stream
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a bot holds a local copy of an order book and reads microstructure
signals off it. It covers the layer between a decoded feed message and a strategy
decision: applying depth mutations atomically, deciding whether the resulting book is
usable, and computing the weighted mid-price and depth imbalance from a consistent view
of both sides.

It accepts either shape of feed. **Level 2** updates *set* a price level's absolute
quantity. **Level 3** events (add / execute / modify / cancel) are tracked per order and
aggregated into the same price levels, so both feed types produce one book and one
metrics path.

Its second job is **telling you when the book is wrong**. Depth feeds fail quietly: a
dropped message, a NaN price, a repeated order reference or a side token the parser did
not expect all produce a book that looks healthy and disagrees with the venue. Every
update that cannot be applied consistently is counted in `violations_by_kind` rather than
absorbed, and a non-zero count invalidates every metric derived from that session.

## When NOT to Use

- **As the transport, sequencing or snapshot layer.** This applies updates that have
  already been de-framed and sequence-checked. Buffering deltas against a REST snapshot,
  aligning update IDs and detecting gaps are
  `market-data-snapshot-plus-delta-reconciliation` and
  `sequence-number-gap-detection-for-feeds`. This skill's `reset()` is the *recovery
  hook* those skills call, not a resynchronisation procedure of its own.
- **As a binary feed decoder.** Turning ITCH or SBE bytes into typed messages is
  `nasdaq-totalview-itch-feed-parsing` and `binary-protocol-parsing-for-low-latency-feeds`.
- **For a consolidated multi-venue book.** A crossed condition across venues is a real
  market state, not corruption — this processor's crossed guard assumes one venue's book,
  where a resting bid at or above a resting ask cannot survive a continuous match.
- **During an auction call phase.** Order books legitimately cross before an uncross: on
  Xetra the auction executes all executable orders "so as to prevent a 'crossed' order
  book", which means the book *is* crossed during the call and the venue publishes an
  indicative price and imbalance instead. Auction-phase depth belongs to
  `opening-auction-imbalance-based-execution`; running this guard through a call phase
  produces a continuous stream of false corruption alarms.
- **On a latency-critical CPython hot path.** `compute_metrics` sorts the whole book on
  each call. That is the right trade for a reference implementation and the wrong one at
  sustained full-depth rates — see `order-book-imbalance-signal-pipeline` for the
  fast-path design and `memory-mapped-ring-buffer-for-ultra-low-latency` for the transport.
- **To drive one instance from both an L2 and an L3 feed.** L2 sets a level's volume and
  L3 accumulates into it; interleaving them makes the aggregate meaningless.

## Prerequisites

- A **decoded, sequence-checked** depth stream: L2 `(price, absolute_quantity)` pairs, or
  L3 order events carrying a session-unique order identifier.
- The venue's **side token vocabulary**. `B`/`S` on Nasdaq ITCH, `buy`/`sell` on Coinbase
  Exchange level3, separate bid/ask arrays with no side field on Binance depth. The
  processor normalises `BUY`/`B`/`BID` and `SELL`/`S`/`ASK` and rejects everything else.
- A **metrics depth level count** (e.g. top 5 / top 10). This bounds the imbalance
  aggregation only; it does not truncate the stored book, and it must not — a level
  dropped from storage cannot have a later delete applied to it.
- A **snapshot source** to rebuild from after `reset()`.

## Workflow

1. **Validate every field before it reaches the book.**
   - **Decision point — reject, do not coerce.** A non-finite price is the dangerous
     case: `float('nan') >= x` evaluates to `False`, so one NaN price silently disables
     the crossed-book guard and then propagates through every metric as NaN. Prices must
     be finite and positive, sizes finite and non-negative.
   - **Decision point — a negative quantity is not a delete.** Venues signal removal with
     an explicit zero. Treating `qty <= 0` as a removal turns a corrupt negative
     quantity into a silent liquidity deletion.

2. **Apply the mutation under one lock.**
   - Wrap every bid and ask mutation in the same mutex. A book mutated on two threads
     without mutual exclusion produces transient crossed states that are indistinguishable
     from real feed corruption.
   - Validate the whole batch before applying any of it, so a rejected update leaves the
     book untouched rather than half-applied.

3. **Apply L2 updates as absolute level quantities.**
   - $Q(P) \leftarrow V$. When $V = 0$, remove the level: Binance spot's own procedure is
     "If the quantity is zero, remove the price level from the order book." Quantities are
     never increments.

4. **Apply L3 events per order, then re-aggregate.**
   - **Decision point — a duplicate order id is a missed message, not a re-add.** Order
     references are session-unique on these feeds. Adding the size a second time strands
     the surplus at that level permanently, because the eventual cancel can only deduct
     one order's worth. Reject it and count `DUPLICATE_ORDER_ID`.
   - **Decision point — an unknown order id is a divergence, not a no-op.** The natural
     `if order_id in self._orders:` turns a dropped add into silence and leaves the book
     wrong for the rest of the session. Count `UNKNOWN_ORDER`.
   - **Decision point — executions are deductions, modifies are absolute.** An execution
     (`execute_l3_order`) subtracts filled size — the path for ITCH `E`/`C`, ITCH `X`
     partial cancels and Coinbase `match`. A modify (`modify_l3_order`) *sets* the new
     displayed size — the Coinbase `change` message, whose "`size` property is the updated
     size at the price level, not a delta". Mixing them up double-counts every fill.
   - **Decision point — an over-execution must be flagged, not clamped.** Removing more
     shares than were resting means the book had already diverged. `max(remaining, 0)`
     hides exactly the evidence you need.

5. **Classify a crossed or locked book before reacting to it.**
   - Best bid $\ge$ best ask covers both *locked* (equal) and *crossed* (bid above ask).
     On one venue's continuous book neither can survive the matching engine, so both mean
     the local copy is wrong.
   - **Decision point — do not drop the offending tick.** A crossed book usually means an
     earlier message was lost; discarding the update that *exposed* the gap leaves the
     book wrong in a way nothing downstream can detect. Keep the state, flag it, and
     recover: `reset()` then rebuild from a fresh snapshot.
   - **Decision point — confirm the guard applies at all.** A consolidated multi-venue
     book and an auction call phase both cross legitimately (see *When NOT to Use*).
     US equity venues restrict *displaying* locking or crossing protected quotations under
     Reg NMS Rule 610(e), but that constrains venue quote display — it does not make a
     crossed consolidated quote impossible, and it creates no obligation on your local book.

6. **Compute metrics from one consistent view, and know which volumes feed which.**
   - Volume-weighted mid-price, from **top-of-book** volumes, with the bid price carrying
     the *ask* volume:
     $$P_{\text{wmid}} = \frac{V^{\text{top}}_{\text{ask}} \cdot P_{\text{bid}} + V^{\text{top}}_{\text{bid}} \cdot P_{\text{ask}}}{V^{\text{top}}_{\text{bid}} + V^{\text{top}}_{\text{ask}}}$$
   - Depth imbalance ratio ($I \in [-1, 1]$), from volume **aggregated over the top $N$
     levels** per side:
     $$I = \frac{V^{(N)}_{\text{bid}} - V^{(N)}_{\text{ask}}}{V^{(N)}_{\text{bid}} + V^{(N)}_{\text{ask}}}$$
   - The two aggregations differ deliberately — the weighted mid is defined at the touch,
     depth imbalance is a deeper signal — so `DepthMetrics` reports `bid_levels`,
     `ask_levels`, `total_bid_volume` and `total_ask_volume` alongside the ratio. A thin
     side contributes fewer levels than requested, and an imbalance mixing two bid levels
     with five ask levels is not the same statistic as one over five and five.
   - **Decision point — guard the denominator by validating inputs, never by clamping it.**
     A `max(volume, 1e-5)` floor looks like a division-by-zero guard and is silently a
     rescaling: a crypto book quoted in fractions of a coin returns a weighted mid nowhere
     near the touch, with no error raised. Volumes validated positive at ingress make the
     denominator provably positive; an empty book raises instead.
   - **Decision point — gate on `is_crossed`.** Metrics are still returned for a crossed
     book, with real values rather than a neutral placeholder, precisely so that a caller
     that skips the flag does not mistake a fabricated `0.0` imbalance for a balanced book.

7. **Hand the strategy an immutable snapshot, not the live book.**
   - `get_snapshot(depth_levels)` copies both sides under the lock, so the bid and ask
     halves come from the same instant. Two separate reads of the live views can straddle
     a mutation. The `bids` / `asks` / `l3_orders` views are read-only for the same reason:
     a mutable public dict makes the mutex decorative.

8. **Check the integrity counters before trusting anything derived from the session.**
   - A clean run raises nothing *and* has `integrity_violation_count == 0`. No exception
     means only that no update was malformed.

> Full step-by-step procedure with venue-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Clamping a volume denominator with an epsilon floor.** `max(V_bid + V_ask, 1e-5)`
  reads as a division-by-zero guard. For a book quoted in fractions of a unit — routine on
  crypto venues — it silently rescales the result instead: a $100/\$101$ book with
  $1\text{e-}6$ and $3\text{e-}6$ resting returns a weighted mid of $40.10$ and an
  imbalance of $-0.20$ where the true values are $100.25$ and $-0.50$. No exception, no
  NaN, just a price outside the spread.
- **Letting a NaN into the book.** Every comparison against NaN is `False`, so
  `best_bid >= best_ask` reports *not crossed* while the metrics all come back NaN. The
  guard fails open in the one case it exists for.
- **Defaulting an unrecognised side to one book.** `bids if side == "BUY" else asks`
  routes an ITCH `B` and a Coinbase `buy` — the two feeds most likely to be plugged in —
  straight onto the offer side. The bid is now invisible and phantom offer liquidity sits
  in front of your quotes.
- **Negative slicing on a depth level count.** `sorted(...)[:depth_levels]` with
  `depth_levels = 0` raises deep in the metrics path, and with `-1` it silently drops the
  *last* level and returns a plausible, wrong imbalance. Validate that it is a positive int.
- **Re-adding an order id that is already resting.** The size is counted twice and the
  cancel deducts once, leaving phantom volume at that level for the rest of the session.
- **Clamping an over-execution to zero.** `shares -= filled; if shares <= 0: remove` looks
  like it handles over-execution; it hides it. More shares filled than were resting means
  the book diverged before the message arrived.
- **Dropping the tick that exposed a crossed book.** The crossing update is usually the
  *symptom*; the missing message came earlier. Discarding it restores a book that looks
  fine and is still wrong. Flag, reset, re-snapshot.
- **Exposing the book as a mutable public dict.** Any caller can then write to it without
  the lock, and any multi-level read can straddle a mutation. The thread-safety guarantee
  has to hold at the API boundary, not just inside the methods.
- **Keying price levels on raw floats.** Two decimal strings that should be the same tick
  can parse to adjacent doubles and split into two levels that never merge. Where a venue
  publishes integer ticks (ITCH `Price (4)`, MDP 3.0 scaled prices), key on the integer and
  scale only for display.
- **Treating "no exception raised" as a validated replay.** Check
  `integrity_violation_count`.

## Verification

- **Weighted mid-price**: bids $(100.0, 10)$, asks $(101.0, 5)$ →
  $P_{\text{wmid}} = (100 \cdot 5 + 101 \cdot 10)/15 = 100.6\overline{6}$, strictly inside
  the spread and above the $100.5$ arithmetic mid because the bid is heavier.
- **Imbalance aggregation**: with bids $(100.0, 10), (99.5, 20)$ and asks
  $(101.0, 5), (101.5, 15)$, `depth_levels=1` gives $5/15$ and `depth_levels=2` gives
  $10/50 = 0.20$, while the weighted mid is identical for both.
- **Small-volume regression**: a $1\text{e-}6$ / $3\text{e-}6$ book must return exactly
  $100.25$ and $-0.5$, not $40.1$ and $-0.2$.
- **Input rejection**: NaN, $\pm\infty$, zero and negative prices; NaN and negative sizes;
  a zero, negative, float, bool or string `depth_levels`; an empty symbol — all raise
  `DepthProcessorError`, and a rejected batch leaves the book byte-for-byte unchanged.
- **Crossed and locked**: bid $102.0$ against ask $101.0$ returns `False` from
  `update_l2_depth`, counts `CROSSED_BOOK`, reports `spread == -1.0` and a *real*
  imbalance of $5/15$; bid $101.0$ against ask $101.0$ is also flagged, with
  `spread == 0.0`. `reset()` clears the book and keeps the violation history.
- **L3 lifecycle**: `BUY`/`buy`/`B`/`b`/`Bid` all rest on the bid side and
  `SELL`/`sell`/`S`/`s`/`Ask` on the ask side; `LONG`, `X`, `""`, `1` and `None` raise.
  A duplicate id is refused and counted, leaving the level at its original size. A partial
  execution reduces both the order and the level; the final one removes both. An
  over-execution counts `OVER_EXECUTE`. A modify sets an absolute size. Cancel, execute and
  modify against an unknown id each count `UNKNOWN_ORDER`.
- **State exposure**: `bids`, `asks` and `l3_orders` raise `TypeError` on assignment;
  `DepthMetrics` and `BookSnapshot` raise `AttributeError` on assignment; a snapshot does
  not track later mutations.
- **Concurrency**: eight threads each adding, snapshotting and cancelling 200 orders leave
  an empty book, an empty order map and zero violations.
- Run `python -m unittest discover -s skills/order-book-depth-processing-l2-l3/scripts`
  and confirm 42/42 pass.

## Related Skills

- `market-data-snapshot-plus-delta-reconciliation`
- `sequence-number-gap-detection-for-feeds`
- `nasdaq-totalview-itch-feed-parsing`
- `historical-order-book-reconstruction-from-message-logs`
- `order-book-imbalance-signal-pipeline`
- `queue-position-modeling-for-passive-orders`
- `producer-consumer-tick-pipeline`
- `opening-auction-imbalance-based-execution`
