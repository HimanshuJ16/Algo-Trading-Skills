---
name: post-only-limit-repricing-under-fast-markets
description: Use when submitting Post-Only (maker-only) limit orders in fast-moving
  books, to guarantee client-side that the price is strictly passive and exactly
  tick-aligned before it is sent, and to bound the reprice loop that would otherwise
  burn an order-rate budget.
domain: Execution Algorithms
subdomain: Market Microstructure & Order Type Management
tags:
- post-only
- limit-repricing
- fast-markets
- rejection-churn
- maker-taker
- microstructure
- execution-algo
- tick-alignment
brokers_frameworks:
- Nasdaq Post-Only (Equity 4, Rule 4702)
- Binance Spot LIMIT_MAKER
- Coinbase post_only
- Deribit post_only / reject_post_only
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a passive, liquidity-providing strategy submits Post-Only limit orders
(`LIMIT_MAKER`, `GTX`, `post_only`, `ParticipateDoNotInitiate`) into a book that can move
between the moment you read the BBO and the moment your order reaches the matching engine —
CPI and payroll prints, open and close auctions, crypto liquidation cascades.

The core problem is not that a crossing Post-Only order gets rejected. It is that
**"Post-Only" means three different things across venues**, and only one of them is a
harmless rejection:

- **Rejected** — Binance Spot `LIMIT_MAKER`, Coinbase `post_only`.
- **Executed as a taker at the resting order's price** — Nasdaq / BX / PSX. You pay taker
  fees, forfeit the maker rebate, and remove the liquidity you meant to provide.
- **Silently re-priced by the matching engine** — Deribit's default `post_only`, which
  rests your order at a price you never chose.

So a client that submits a crossing Post-Only order and assumes "the venue will just reject
it" is wrong on two of the three venue families, and expensively wrong on one. This engine
makes the passivity and tick-alignment decision locally, before the message leaves, which
is the only guarantee that holds across all three. Sourced venue table in
`references/standards.md`.

## When NOT to Use

- **As a substitute for reading your venue's rulebook.** The engine prevents *your* client
  from emitting a crossing price. It cannot stop a venue from filling, sliding, or rejecting
  an order for reasons it does not model (self-match prevention, price bands, credit,
  entitlements).
- **For cross-venue / NBBO compliance.** It sees one `best_bid`/`best_ask` pair. A price that
  is passive on the target venue can still lock or cross a protected quotation elsewhere —
  see `us-reg-nms-order-protection-rule-compliance`.
- **When you actually want the fill.** Repricing to the passive boundary is a decision to not
  trade now. If the objective is completion under a deadline, use an execution algorithm that
  is allowed to cross — see `implementation-shortfall-minimization`.
- **As a latency fix.** The decision is correct for the snapshot handed to it. If quotes
  routinely change while your order is in flight, the answer is colocation, a faster quote
  loop, or a wider `fast_market_offset_ticks` — not more reprice attempts.
- **For pegged orders.** A continuously re-evaluated reference price is a different mechanism;
  see `peg-order-types-for-passive-execution`.

## Prerequisites

- Top-of-book snapshot per symbol: `symbol`, `best_bid`, `best_ask`, and **`tick_size`
  sourced from venue reference data at runtime** — not a hard-coded constant. US NMS
  increments are assigned per symbol and are subject to regime change; crypto ticks vary by
  orders of magnitude across pairs.
- Optional `market_velocity_ticks_per_sec` for fast-market classification.
- Order spec: `order_id`, `side` (`'BUY'`/`'SELL'`), `quantity` (> 0), `desired_price` (> 0),
  and the `reprice_attempts` already spent on this order cycle.
- `Config(max_reprice_attempts=3, fast_market_velocity_threshold=20.0,
  fast_market_offset_ticks=0)` — all three are library defaults, not venue mandates.

## Workflow

1. **Validate the snapshot.** Non-finite, zero, or negative prices and ticks raise. A `NaN`
   best bid is a feed defect, not a market condition, and must not be quietly priced around.

2. **Check the attempt budget first.** If `reprice_attempts >= max_reprice_attempts`, return
   `REPRICE_ATTEMPTS_EXCEEDED` / `action='HOLD'` and send nothing.
   - **Decision point:** cancel or stand down — do **not** reset the counter and re-enter. An
     order that keeps crossing is telling you the level is moving faster than your quote loop.

3. **Detect a locked or crossed book.** If `best_bid >= best_ask` there is no passive price on
   *either* side (repricing a BUY to `best_bid` still sits at or above `best_ask`).
   - **Decision point:** this is a `HOLD`, not a reprice, and it consumes no attempt. A crossed
     book often means a stale or gapped feed — validate before trusting the next snapshot.

4. **Test crossing at the inclusive boundary.** A BUY takes at `desired_price >= best_ask`; a
   SELL takes at `desired_price <= best_bid`. An order resting strictly inside the spread is
   already passive and is left untouched — pulling it to the touch would surrender price
   improvement for nothing.

5. **Reprice to the passive boundary, then apply the fast-market offset.** BUY → `best_bid`,
   SELL → `best_ask`; then move `fast_market_offset_ticks` further from the touch if the book
   is classified fast.
   - **Decision point:** the offset is a trade-off, not free safety. Joining the touch
     maximises fill probability and is the price most likely to be crossed while in flight;
     backing off cuts both. Default `0`; raise only on measured evidence of in-flight
     re-crossing.

6. **Align to the tick, away from the touch.** Floor a BUY, ceil a SELL, quantized with
   `Decimal`.
   - **Decision point:** never round to nearest. Nearest-rounding moves a BUY *up* onto the
     ask and a SELL *down* onto the bid — it manufactures the exact order this skill prevents.

7. **Re-assert passivity after alignment.** A coarse tick, an off-tick quote, or a sub-tick
   bid can still yield a marketable or non-positive price. On violation return
   `NO_VALID_PASSIVE_PRICE` / `action='HOLD'`.

8. **Carry the attempt count forward.** The engine does not mutate the order. Rebuild it with
   `report.next_attempt(order)` before resubmitting, or the cap never engages.

9. **Submit only when `report.action == 'SUBMIT'`,** and persist the report as the audit trail.

> Full procedure: see `references/workflows.md`.
> Standards and sourced venue behaviour: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Post-Only cannot take liquidity.** On Nasdaq, BX and PSX a Post-Only order that
  would cross the book "will be executed at the price of the resting order." The protective
  flag you believed you had is, on that venue family, a price-improvement flag. Verify per
  venue before relying on it.
- **Rounding the repriced limit to the nearest tick.** With `best_bid = 100.00`,
  `best_ask = 100.10` and `tick = 0.05`, a passive BUY at `100.08` rounds to `100.10` — the
  best ask. The tick-alignment step, added to *prevent* rejections, becomes the thing that
  causes them. Floor BUYs, ceil SELLs.
- **Rounding prices to a fixed number of decimals.** `round(price, 4)` on a
  `0.00000001`-tick pair returns `0.0`. A price-formatting shortcut that works on
  dollar-denominated equities silently destroys crypto prices.
- **Float arithmetic for tick alignment.** `round(price / tick) * tick` reintroduces binary
  representation error and produces off-tick prices that fail venue validation outright —
  Binance's `PRICE_FILTER` requires `price % tickSize == 0`.
- **Repricing into a locked or crossed book.** When `best_bid >= best_ask`, "reprice the BUY
  to the bid" produces a price at or above the ask. The repricing logic confidently emits the
  crossing order it was written to prevent.
- **A churn cap that never engages.** Reading `reprice_attempts` without ever incrementing and
  carrying it forward gives an unbounded loop wearing a bounded loop's API. Verify the cap in
  a resubmission loop, not a single call.
- **Assuming rejected orders are free.** Binance documents that rejected orders "might or
  might not" update the `ORDERS` rate limit, and the rate-limit decrement is earned by
  *filling* — which a rejected Post-Only order never does. Breaches return `429` / `-1015` and
  escalate to IP bans lasting from 2 minutes to 3 days.
- **Ignoring the queue-position cost.** Every reprice is a new order at the back of the queue
  at that level. A loop that keeps its price passive can still have a terrible fill rate; see
  `queue-position-modeling-for-passive-orders`.
- **Treating a malformed `side` as passive.** Defaulting an unrecognised side to "accepted, no
  reprice" turns a typo into an unchecked live order. Unknown sides raise.

## Verification

- Instantiate `FastMarketPostOnlyRepricer()`. With `best_bid = 100.00`, `best_ask = 100.10`,
  `tick_size = 0.05`, submit a BUY at `100.08`: expect `final_limit_price == 100.05`
  (floored, strictly below the ask), **not** `100.10`. Mirror with a SELL at `100.02`:
  expect `100.05`, strictly above the bid.
- Crossing reprice: BUY at `60015.0` against `60000.0 / 60010.0` at 25 ticks/sec ⇒
  `POST_ONLY_PASSIVE_REPRICED`, `final_limit_price == 60000.0`, `is_fast_market` true.
- Precision: BUY at `0.00002460` against `0.00002451 / 0.00002455` with
  `tick_size = 0.00000001` ⇒ `final_limit_price == 0.00002451`, exactly on-tick.
- Churn cap: loop `report = engine.process_order(market, order); order =
  report.next_attempt(order)` five times against a permanently crossing price ⇒ three
  `POST_ONLY_PASSIVE_REPRICED` then `REPRICE_ATTEMPTS_EXCEEDED` with `action == 'HOLD'`.
- Locked book: `best_bid == best_ask == 60000.0` ⇒ `BOOK_LOCKED_OR_CROSSED`,
  `action == 'HOLD'`, and no attempt consumed.
- Negative checks: `tick_size <= 0`, `NaN`/`Inf` prices, `quantity <= 0`, `desired_price <= 0`,
  an unrecognised `side`, and a negative `reprice_attempts` must each raise `ValueError`.
- Run `python -m unittest discover -s skills/post-only-limit-repricing-under-fast-markets/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `post-only-and-maker-taker-fee-optimization`
- `exchange-tick-size-regime-tracking`
- `queue-position-modeling-for-passive-orders`
- `peg-order-types-for-passive-execution`
- `message-rate-limit-vs-latency-tradeoff-tuning`
- `us-reg-nms-order-protection-rule-compliance`
