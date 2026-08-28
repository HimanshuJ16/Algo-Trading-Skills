---
name: opening-auction-imbalance-based-execution
description: >-
  Quantitative execution strategy for parsing opening-auction imbalance feeds
  (Nasdaq Opening Cross NOII, NYSE Core Open Auction imbalance publication) and
  deriving venue-compliant contra-side on-open orders (OIO, LOO, MOO) that are
  legal to enter at the intended submission time.
domain: Execution Algorithms
subdomain: Auction Mechanics
tags: ["opening-auction", "noii", "imbalance-execution", "market-on-open", "limit-on-open", "opening-imbalance-only", "nyse-cross", "nasdaq-cross"]
brokers_frameworks: ["Nasdaq TotalView-ITCH 5.0 (NOII)", "Nasdaq Opening Cross (Equity 4 Rules 4702/4752)", "NYSE Core Open Auction (Rule 7.35A)", "Generic Execution"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when participating in a US equity opening auction (Nasdaq Opening Cross, NYSE Core Open Auction) to provide contra-side liquidity against a published order imbalance, or to execute a rebalance order at the official opening price with price protection. The strategy consumes opening-auction imbalance data (paired shares, imbalance shares, imbalance direction, Current Reference Price, near/far indicative clearing prices) and derives an on-open order that the listing venue will actually accept at the moment you intend to send it.

**Do NOT use it** for the closing cross (see `close-auction-participation-strategy`), for IPO/halt crosses or the Extended Trading Close (different order types and cutoffs — the strategy rejects those NOII cross types), or as a way to *take* liquidity in the same direction as an imbalance. It is not a substitute for a venue order-entry gateway: it produces order parameters, it does not submit, amend or cancel orders.

## Prerequisites

- An opening-auction imbalance feed: Nasdaq TotalView-ITCH NOII message (type `I`, Cross Type `O`) or the NYSE Core Open Auction imbalance publication.
- A synchronized clock (PTP/NTP). Every gate in this skill is a wall-clock deadline measured against the 09:30:00 ET cross — see `clock-synchronization-ptp-for-trading-hosts`. Use `seconds_to_open_from(now)` to convert a **timezone-aware** datetime; it rejects naive datetimes, because a UTC-stamped feed compared raw against an Eastern deadline is how orders get sent after the cutoff.
- Knowledge of which venue's rules apply. The listing venue's cutoffs govern acceptance, not your broker's.
- A measured estimate of your strategy-to-exchange latency, configured as `entry_safety_buffer_seconds`.

## Workflow

1. **Feed ingestion**: Parse the imbalance message into `AuctionImbalanceData`. Discard anything whose `cross_type` is not `O`; a closing (`C`), IPO/halt (`H`) or Extended Trading Close (`A`) NOII carries different semantics. Imbalance direction `P` means the security is paused and `O` means the venue has insufficient orders to calculate — neither is a tradable side, and the strategy returns `SECURITY_PAUSED` / `IMBALANCE_NOT_CALCULABLE` rather than folding them into a threshold decision.
2. **Imbalance analysis**: Compute `imbalance_ratio(paired_qty, imbalance_qty)` = imbalance / (paired + imbalance). It returns `0.0` on an empty book rather than dividing by zero. Screen with `min_imbalance_qty` and `imbalance_ratio_threshold` so noise imbalances do not trigger orders. Refuse observations older than `max_feed_age_seconds` — Nasdaq republishes every 10s before 09:28 and every second after.
3. **Entry-window gate** — this is venue- *and order-type*-specific, not a single "09:28 cutoff":
   - **Nasdaq**: MOO must be received before **09:28**. LOO may be entered until **09:29:30**, but an LOO entered after 09:28 is re-priced by the venue if its limit is more aggressive than the 09:28 Current Reference Price or the prior day's NOCP — the report flags this as `late_loo_reprice_risk`. OIO may be entered until the cross executes, which makes it the only order type still available in the last two minutes.
   - **NYSE**: MOO and LOO are accepted until the DMM opens the security. NYSE offers no OIO; requesting one returns `ORDER_TYPE_UNSUPPORTED_BY_VENUE`.
   - Gate on the *arrival* time, not the observation time: `entry_safety_buffer_seconds` is subtracted from `seconds_to_open` so feed lag plus your broker hop cannot push the order past the cutoff.
4. **Cancellability check** — read `report.is_cancellable` before sizing. **Nasdaq freezes cancel/modify of all on-open orders at 09:25, which is also when it starts publishing the imbalance.** There is therefore *no* moment at which a Nasdaq order derived from a published opening imbalance can still be pulled: it is committed capital from the instant it is sent. NYSE freezes cancel/replace at 09:29 and runs the Core Open Auction Imbalance Freeze from 09:29:55, so NYSE does leave a usable cancellation window.
5. **Pricing**: Derive the limit from `price_basis`. The Far price is the clearing price of the auction-only book; the Near price includes the continuous book; `REF` is the Current Reference Price. **Nasdaq publishes no Near or Far Indicative Clearing Price before 09:28** — only the Current Reference Price, paired shares, imbalance shares and imbalance direction. A non-positive far/near value means the venue has disseminated none, and the strategy returns `INDICATIVE_PRICE_UNAVAILABLE` rather than submitting a $0.00 limit. `price_offset_bps` prices the order away from the indicative clearing price, trading fill probability for a wider liquidity premium, and limits are rounded away from the aggressive side at `tick_size`.
6. **Sizing**: Quantity is the floor, to `lot_size`, of the smallest of `size`, `participation_pct × imbalance_qty`, and `max_pct_of_auction_volume × (paired + imbalance)`. A result below `min_order_qty` produces no order — the minimum never overrides a cap.
7. **Idempotency**: Each order carries a deterministic `client_order_id` derived from (strategy, venue, session date, symbol, side, order type). Re-processing the same imbalance on the next feed update returns `DUPLICATE_SUPPRESSED` with the original order rather than emitting a second one.
8. **Post-cross reconciliation**: Match execution reports after the cross against the official opening price (Nasdaq Official Opening Price / NYSE opening print) and attribute unfilled quantity as opportunity cost.

> Full procedure: see `references/workflows.md`.
> Standards and rule citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming one cutoff for both venues and all order types**: there is no universal 09:28 deadline. Nasdaq MOO 09:28 / LOO 09:29:30 / OIO until the cross; NYSE MOO and LOO until the DMM opens the security. A single hard-coded number either rejects legal orders or sends ones the venue will reject.
- **Pricing off a near/far price that was never disseminated**: Nasdaq sends no Near or Far Indicative Clearing Price for the opening cross before 09:28. A naive parser reads the unsigned ITCH price field as `0` and submits a limit of $0.00. Treat any non-positive indicative price as *absent*, and use the Current Reference Price if you need a basis before 09:28.
- **Believing a Nasdaq on-open order can be pulled**: the cancel/modify freeze is 09:25, the same minute the imbalance feed starts. Anything this strategy sends on Nasdaq is unpullable. Size it as capital you are committed to trading at an unknown cross price.
- **Providing contra-side liquidity with an unpriced MOO**: an MOO executes at whatever the cross prints, with no price protection, which is the opposite of what a liquidity-provision strategy wants when a large imbalance is moving the clearing price. The venue-designed instrument is the limit-priced OIO, which executes only in the cross and only against on-open interest. `allow_unpriced_moo` defaults to `False` so choosing MOO has to be deliberate.
- **Checking the cutoff against the feed timestamp**: the deadline applies to when the *exchange receives* the order. Budget for feed lag, strategy compute and broker hops with `entry_safety_buffer_seconds`.
- **Emitting one order per feed update**: Nasdaq republishes the NOII every second from 09:28. Without an idempotency key that is up to 120 duplicate submissions of a single trading intent.
- **Treating imbalance direction `O` or `P` as "no imbalance"**: `O` means the venue cannot calculate an imbalance and `P` means the security is paused. Reporting either as a failed threshold test hides a market-state problem in the audit trail.
- **Acting on a stalled feed**: before 09:28 the Nasdaq imbalance updates only every 10 seconds. If the last observation is a minute old the book has moved on, and sizing against it commits capital to an imbalance that may no longer exist.
- **Adding to the imbalance**: an on-open order on the same side as a large institutional imbalance takes the worst of the cross print. This strategy is deliberately contra-side.

## Verification

- Run `python -m unittest discover -s skills/opening-auction-imbalance-based-execution/scripts`.
- Feed a Nasdaq opening NOII with a 100,000-share buy imbalance and 300,000 paired shares at 60s to open (09:29): expect a `SELL` OIO priced at the far indicative price, `imbalance_ratio == 0.25`, and `is_cancellable is False`. The quantity is the binding cap: 5,000 at the default `size`, and 10,000 (10% of the imbalance) once `size` is raised to 25,000.
- Feed the same message at 180s to open (09:27) with no far/near price, as Nasdaq actually publishes it: `price_basis=PriceBasis.FAR` gives no order and status `INDICATIVE_PRICE_UNAVAILABLE`; switching to `PriceBasis.REF` produces the order at the Current Reference Price.
- Configure `order_type=OnOpenOrderType.MOO, allow_unpriced_moo=True` and feed the message at 124s to open: expect `CUTOFF_EXCEEDED`, because the projected arrival at 119s is past the 09:28 MOO cutoff. At 130s to open the same MOO is accepted, and an OIO stays available all the way to the cross.
- Feed the same imbalance ten times: expect one `ORDER_GENERATED` followed by nine `DUPLICATE_SUPPRESSED`, and `len(engine.orders) == 1`.
- Set `imbalance_side="P"`: expect `SECURITY_PAUSED`, not a threshold decision.

## Related Skills

- `close-auction-participation-strategy`
- `auction-only-order-types-for-illiquid-names`
- `clock-synchronization-ptp-for-trading-hosts`
- `nasdaq-totalview-itch-feed-parsing`
- `minimum-fill-size-and-lot-rounding-logic`
- `order-placement-idempotency`
