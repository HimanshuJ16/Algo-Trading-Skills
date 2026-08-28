---
name: post-only-and-maker-taker-fee-optimization
description: >-
  Use when submitting passive limit orders to a venue with a maker-taker fee schedule and the order must not execute as a taker. Builds the post-only payload in the form the named venue actually accepts (Binance LIMIT_MAKER / GTX, Bybit PostOnly, Coinbase post_only, Kraken oflags=post, FIX ExecInst 6), refuses to submit a price that would cross the spread, and reports the maker-vs-taker fee differential as an estimate conditional on the order filling.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- post-only
- maker-taker
- fee-optimization
- liquidity-provider
- execution-cost
- order-payload-construction
- spread-crossing
brokers_frameworks:
- Binance Spot (LIMIT_MAKER) & USD-M Futures (timeInForce GTX)
- Bybit v5 (timeInForce PostOnly)
- Coinbase Advanced Trade (limit_limit_gtc.post_only)
- Kraken Spot (oflags=post)
- FIX 4.4 ExecInst (tag 18) = 6
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a strategy submits limit orders that are *supposed* to rest on the book — market making, passive rebalancing, patient accumulation, the resting leg of a spread — and executing as a taker would be a defect rather than a cost. Post-only tells the matching engine to cancel or reject the order rather than let it trade on arrival, which converts an unnoticed taker fill into a visible non-fill you can act on.

The module does three separable things: it decides whether a proposed limit price is marketable against a top-of-book snapshot, it builds the post-only payload in the spelling the *named venue* accepts, and it accounts for the fee differential versus crossing — as an estimate conditional on a fill, with realized amounts accrued only from fills the venue actually reports.

## When NOT to Use

- **On any order that must execute.** Stop-losses, risk-liquidation, hedge legs, and margin-call unwinds are worth more than the fee differential. A post-only stop is a stop that silently does not exist. See `kill-switch-and-drawdown-circuit-breakers` and `execution-algorithm-kill-switch-integration`.
- **To decide whether posting beats crossing.** This module prices the *fee* term only. It has no model of fill probability, queue position, or adverse selection, and the passive order that fills often fills because the price moved against it. Pair with `adverse-selection-measurement-for-passive-orders` and `queue-position-modeling-for-passive-orders` before concluding that passive is cheaper.
- **Where maker and taker rates are equal.** Binance's published spot schedule charges the Regular (VIP 0) tier 0.100% maker and 0.100% taker, so on that venue and tier post-only changes the fee bill by exactly zero and only changes fill behaviour. Check the schedule before assuming a premium exists.
- **On per-contract fee schedules.** Venues that bill per contract by product and membership rather than by liquidity flag have no maker/taker differential to optimise.
- **As a repricing loop for fast markets.** This is a single pre-submission decision against one snapshot. Repeated reject-and-reprice churn under high-velocity moves is `post-only-limit-repricing-under-fast-markets`.
- **As the source of realized savings for a P&L or TCA report.** Use the venue's own billed fees and liquidity flags; see `market-maker-vs-taker-strategy-classification` and `transaction-cost-analysis-tca-integration`.

## Prerequisites

- **A named venue.** There is no portable post-only flag; `Venue` must be one of the supported members, and behaviour must be confirmed for anything else.
- **The maker and taker rates actually in force for this account at this tier**, as fractions of notional (`0.001` = 10 bps). Required — there is no default, because a plausible-looking default is exactly how a fabricated savings figure reaches a report.
- **Sign convention**: a rate or amount `> 0` is charged to the desk, `< 0` is credited (a rebate). A negative fee differential therefore means post-only is the *more* expensive side. This matches `exchange-fee-tier-and-rebate-structure-analysis`.
- **A top-of-book snapshot** (`best_bid`, `best_ask`, both finite and positive), optionally with `tick_size`.
- For Bybit v5, the mandatory `category` (`spot` / `linear` / `inverse` / `option`) passed through `venue_params`.

## Workflow

1. **Name the venue before building anything — the flag is not portable.**
   - Binance **spot** uses order `type="LIMIT_MAKER"` and has no post-only `timeInForce`; spot accepts only GTC/IOC/FOK. Binance **USD-M futures** uses `type="LIMIT"` with `timeInForce="GTX"`. Bybit v5 uses `timeInForce="PostOnly"`. Coinbase Advanced Trade nests `post_only` inside `order_configuration.limit_limit_gtc`. Kraken uses `oflags="post"`. FIX 4.4 uses `ExecInst` (tag 18) value `6`.
   - **Decision point:** do not send the union of every spelling as a fallback. Venues commonly ignore unknown fields, and an ignored post-only flag does not fail loudly — it submits a plain limit order that crosses and is billed at the taker rate. The engine emits one venue's payload and refuses `venue_params` keys that would overwrite the flag, the price, or the quantity.
   - **Decision point:** `ExecInst` value `6` is the wire value. Sending the human-readable label `"ParticipateDoNotInitiate"` in tag 18 is not a valid FIX message. Tag 18 is a MultipleValueString, so `6` may travel space-delimited alongside other instructions.

2. **Validate the side as an enum, not a string.**
   - **Decision point:** the crossing check is a two-branch comparison. A side that matches neither branch — `"B"`, `"BUY_TO_COVER"`, a config file's leading space in `" BUY"` — skips the check entirely and submits the marketable price unchanged. `OrderSide` is required for exactly this reason; a non-enum side raises rather than falling through.

3. **Test marketability against the snapshot, inclusively.**
   - A buy is marketable when `limit_price >= best_ask`; a sell when `limit_price <= best_bid`. Both bounds are **inclusive**: a limit exactly equal to the opposite touch trades against it.
   - A price strictly inside the spread is not marketable and is submitted unchanged — it rests ahead of the touch and is a better queue position, not a problem to fix.

4. **Choose a crossing policy — repricing is not free.**
   - `REPRICE_PASSIVE` moves the price to the near touch (bid for a buy, ask for a sell). **Decision point:** this is a *different order*. It no longer takes the liquidity the original price was reaching for, so the trade may simply not happen. Choose it when the fill is optional.
   - `REJECT` refuses to submit and hands the decision back. **Decision point:** the correct follow-up to a rejection is a classification, not a resubmission — either the trade is still worth doing at the touch (send an explicit taker order and book the taker fee), or it is not (drop it). Blind resubmission at a crossing price is the cancellation loop.
   - **Decision point:** on a locked or crossed book (`best_bid >= best_ask`) the near touch is itself marketable, so there is no passive price to reprice to. The engine returns `POST_ONLY_REJECTED_LOCKED_OR_CROSSED_BOOK` rather than emitting a payload the venue would cancel.

5. **Read the fee figures as counterfactuals, not as savings.**
   - Fee differential $= Q \times P_{\text{touch}} \times r_{\text{taker}} - Q \times P_{\text{submitted}} \times r_{\text{maker}}$. The taker leg is priced at the touch the order **would have crossed against** (a buy pays the ask), not at our own limit.
   - **Decision point:** every `_if_filled_usd` field is conditional on a fill that post-only cannot guarantee. Do not accumulate them. An unfilled post-only order saves nothing and forgoes the trade; the previous version of this skill accrued savings at submission time and reported $12,000 of savings for 100 orders that never filled.
   - Realized amounts come from `record_maker_fill(filled_quantity, fill_price, taker_reference_price, fill_id)`, driven by the venue's fill reports, on filled quantity only. **Decision point:** pass the venue's execution id — overlapping paginated fill fetches are the ordinary way one fill arrives twice, and a repeated id is rejected rather than double-counted.
   - **Decision point:** the differential is signed and never clamped at zero. A negative value means the schedule is inverted at your tier and post-only is costing the desk money; the result carries a warning saying so.

6. **Handle the venue's rejection semantics — they differ.**
   - Binance spot rejects the request synchronously. Binance USD-M futures accepts it and then emits an `EXPIRED` order update asynchronously. Bybit cancels the order.
   - **Decision point:** a successful submission response is therefore **not** evidence that an order is resting. Confirm from the order-state stream before treating the quote as live, and never re-send on an ambiguous submission result without an idempotency key — see `order-placement-idempotency`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sending every venue's post-only spelling at once.** `post_only`, `POC` and `execInst` in one payload is not a portable fallback. The venue that does not recognise a field usually ignores it, and an ignored post-only flag submits an ordinary limit order that crosses and pays the taker rate — the exact outcome the flag was added to prevent.
- **Putting `"ParticipateDoNotInitiate"` in FIX tag 18.** The wire value is `6`. The label is documentation, not a message field.
- **Using `timeInForce="GTX"` on Binance spot, or `LIMIT_MAKER` on futures.** They are different products with different post-only mechanics; the spot endpoint accepts only GTC/IOC/FOK.
- **Accepting a free-text side.** Anything that is not exactly `BUY`/`SELL` skips the crossing comparison and submits the marketable price with a post-only flag attached — which the venue then cancels, so the order silently never exists.
- **Repricing into a locked book.** With `best_bid == best_ask`, moving a buy "passively" to the bid leaves it at the ask. The payload looks correct and the venue cancels it on arrival.
- **Treating a repriced order as the same trade.** Joining the near touch forgoes the liquidity the original price was reaching for and joins the back of the queue. Fee savings on a fill that never happens are zero.
- **Accruing estimated savings at submission time.** Estimated differentials are counterfactuals. Summing them produces a savings figure with no fill behind it.
- **Recording the same fill twice.** Paginated fill fetches overlap by design. A double-counted fill inflates the realized differential and leaves nothing in the output to show it — key on the venue's execution id.
- **Clamping the differential at zero.** It hides the inverted-schedule case where post-only is the more expensive side.
- **Retrying a post-only rejection in a tight loop.** Classify first: still worth doing at the touch (send an explicit taker order) or not worth doing (drop it). Otherwise the order-to-trade ratio climbs and can attract venue message-rate penalties — see `order-to-trade-ratio-fee-penalty-avoidance`.
- **Assuming post-only protects a resting order.** It is evaluated at arrival only. An order the market later trades through is still a maker fill.

## Verification

- **Payload spelling**: `Venue.BINANCE_SPOT` must emit `type="LIMIT_MAKER"` and **no** `timeInForce`; `Venue.BINANCE_USDM_FUTURES` must emit `timeInForce="GTX"`; `Venue.BYBIT_V5` must emit `timeInForce="PostOnly"` with side `"Buy"` and string `qty`/`price`; `Venue.COINBASE_ADVANCED` must nest `post_only: True` inside `order_configuration.limit_limit_gtc`; `Venue.KRAKEN_SPOT` must emit `oflags="post"`; `Venue.FIX_4_4` must emit tag `18 = "6"`. No payload from any venue may carry `execInst` or a `POC` time-in-force.
- **Inclusive crossing bounds**: a buy at exactly `best_ask` and a sell at exactly `best_bid` must both be treated as marketable; a buy strictly inside the spread must be submitted unchanged.
- **Locked-book regression**: with `best_bid == best_ask == 60,010`, a buy at 60,020 under `REPRICE_PASSIVE` must return `POST_ONLY_REJECTED_LOCKED_OR_CROSSED_BOOK` with an empty payload — not a payload priced at 60,010.
- **Fee arithmetic**: 2.0 units posted at a bid of 60,000 against an ask of 60,010, with maker 5 bps and taker 25 bps, must report a maker fee of \$60.00, a counterfactual taker fee of \$300.05 (priced at the ask), and a differential of \$240.05.
- **Savings regression**: 100 prepared payloads that are never filled must leave `realized_fee_differential_usd` at exactly 0.0. Only `record_maker_fill` may move it, and a repeated `fill_id` must raise rather than accrue twice.
- **Inverted schedule**: maker 25 bps against taker 5 bps must produce a strictly negative differential and a warning; equal rates must warn that post-only changes the fee bill by zero.
- **Negative checks**: a free-text side, a non-positive or non-finite quantity or price, an empty symbol, a raw `dict` in place of a `TopOfBook`, an off-tick limit price, a Bybit order without `category`, `venue_params` overwriting the post-only flag/price/quantity, a quantity that underflows string serialisation, and a non-`FeeSchedule` or non-`Venue` constructor argument must each raise `PostOnlyOrderError` (a `ValueError`). `FeeSchedule` and `TopOfBook` are frozen.
- Run `python -m unittest discover -s skills/post-only-and-maker-taker-fee-optimization/scripts` and confirm 100% pass rate.

## Related Skills

- `post-only-limit-repricing-under-fast-markets`
- `market-maker-vs-taker-strategy-classification`
- `exchange-fee-tier-and-rebate-structure-analysis`
- `adverse-selection-measurement-for-passive-orders`
- `queue-position-modeling-for-passive-orders`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `order-placement-idempotency`
- `broker-order-type-capability-matrix`
- `execution-venue-fee-tier-optimization`
- `transaction-cost-analysis-tca-integration`
