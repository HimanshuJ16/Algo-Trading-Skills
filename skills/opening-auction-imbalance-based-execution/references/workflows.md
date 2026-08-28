# Workflows for Opening Auction Imbalance-Based Execution

Reference procedure behind `OpeningAuctionImbalanceBasedExecutionEngine`. All
times US/Eastern; "seconds to open" is measured against the 09:30:00 cross.

## 1. Feed ingestion and message screening

1. Parse the venue message into `AuctionImbalanceData`. For Nasdaq that is the
   TotalView-ITCH NOII message (type `I`); for NYSE the Core Open Auction
   imbalance publication.
2. Reject any message whose `cross_type` is not `O`. A closing (`C`), IPO/halt
   (`H`) or Extended Trading Close (`A`) cross has different order types,
   different cutoffs and different price protections; processing one as an
   opening cross silently applies the wrong rulebook.
3. Branch on imbalance direction before any threshold test:
   - `P` — the security is paused or halted. Stop. Do not fall through to a
     ratio comparison, which would report a market-state problem as a failed
     threshold.
   - `O` — the venue has insufficient orders to calculate an imbalance. The
     published quantities are not a tradable imbalance. Stop.
   - `N` — no imbalance. No contra-side to provide.
   - `B` / `S` — actionable.
4. Validate before comparing. A NaN or infinite `seconds_to_open` defeats every
   subsequent deadline test, because every ordering comparison against NaN is
   false; the engine rejects it as `INVALID_INPUT` rather than letting it open
   the cutoff gate.

## 2. Imbalance analysis

1. `imbalance_ratio = imbalance_qty / (paired_qty + imbalance_qty)`, returning
   `0.0` for an empty book.
2. Reject stale observations. Nasdaq republishes every 10 seconds before 09:28
   and every second after; NYSE every second when changed. An observation older
   than `max_feed_age_seconds` describes a book that has moved on.
3. Require both `imbalance_ratio >= imbalance_ratio_threshold` and
   `imbalance_qty >= min_imbalance_qty`. The ratio alone will fire on a tiny
   book (9,000 unpaired against 1,000 paired is a 90% ratio worth nothing).

## 3. Entry-window gate

1. Look up the entry cutoff for `(venue, order_type)` in `VENUE_RULES`. A
   `None` cutoff means the venue does not offer that order type — NYSE has no
   OIO.
2. Project the arrival time: `arrival = seconds_to_open - entry_safety_buffer_seconds`.
   The deadline applies when the exchange receives the order, so the buffer must
   cover feed lag, strategy compute and every broker hop. Measure it; do not
   guess it.
3. Reject when `arrival <= cutoff`. Nasdaq's MOO rule is "must be received
   *before* 09:28", so arrival exactly at 09:28:00 is late.
4. For a Nasdaq LOO, flag `late_loo_reprice_risk` when the projected arrival
   falls in the 09:28 – 09:29:30 window. The order will still be accepted, but
   the venue may re-price it against the 09:28 Current Reference Price or the
   prior day's NOCP, and a DAY order becomes IOC.

## 4. Cancellability

Read `is_cancellable` before deciding the size, not after.

- **Nasdaq**: cancel/modify of on-open orders is frozen at 09:25 — the same
  minute the imbalance feed starts. Any order this strategy derives from a
  published Nasdaq opening imbalance is therefore un-cancellable from the moment
  it is sent. Treat the quantity as capital committed to trading at a cross
  price you will not know until 09:30.
- **NYSE**: cancel/cancel-and-replace of MOO and LOO is rejected from 09:29, and
  the Core Open Auction Imbalance Freeze runs 09:29:55 – 09:30. Before 09:29
  there is a genuine cancellation window; plan whether you intend to use it.

## 5. Order type selection

| Situation | Order type |
|---|---|
| Nasdaq, contra-side liquidity provision, any time up to the cross | OIO — limit-priced, executes only in the cross against on-open interest |
| Nasdaq, before 09:28, want a limit that participates against the whole book | LOO |
| Nasdaq, between 09:28 and 09:29:30 | LOO, accepting the re-pricing risk, or OIO |
| NYSE | LOO (or MOO with explicit opt-in) |
| Any venue, willing to trade at any cross price | MOO — requires `allow_unpriced_moo=True` |

An MOO has no price protection at the cross. Providing contra-side liquidity
into a large imbalance with an unpriced order takes the worst of the print,
which is why `allow_unpriced_moo` defaults to `False`.

## 6. Pricing

1. Select the basis:
   - `FAR` — clearing price of the auction-only book.
   - `NEAR` — clearing price including the continuous book.
   - `REF` — Current Reference Price, the only price Nasdaq publishes before
     09:28.
2. Treat a non-positive far/near value as *absent*, not as zero. Nasdaq
   disseminates neither before 09:28; an unsigned ITCH price field read
   naively yields `0` and a $0.00 limit. The engine additionally refuses to use
   a far/near value that arrives before the venue publishes one, and records a
   feed warning, because that combination indicates a parser fault.
3. Apply `price_offset_bps` away from the indicative clearing price — buy lower,
   sell higher — trading fill probability for a wider liquidity premium.
4. Round to `tick_size` away from the aggressive side: buy limits down, sell
   limits up. Snap a price that is already on a tick rather than moving it, or
   binary float error pushes it a full tick.

## 7. Sizing

`qty = floor_to_lot(min(size, participation_pct × imbalance_qty,
max_pct_of_auction_volume × (paired_qty + imbalance_qty)))`

If the result is below `min_order_qty`, send nothing. The minimum is a floor on
what is worth sending, never an override of a cap — raising a capped quantity up
to a lot size breaches the cap the operator configured.

## 8. Idempotency

The trading intent is `(strategy_id, venue, session_date, symbol, side,
order_type)`, hashed into a deterministic `client_order_id`. The imbalance feed
republishes continuously; without this key the strategy emits one order per
update. Re-processing a known intent returns `DUPLICATE_SUPPRESSED` with the
original order attached, so the caller can reconcile rather than resubmit. See
`order-placement-idempotency` for the broker-side half of this contract.

## 9. Post-cross reconciliation

1. Match execution reports after the cross against the official opening price
   (Nasdaq Official Opening Price, NYSE opening print).
2. Attribute unfilled quantity as opportunity cost, and filled quantity against
   the arrival price to measure whether the liquidity premium was actually
   captured.
3. Feed realised slippage back into `price_offset_bps` and
   `participation_pct` — see `transaction-cost-analysis-tca-integration`.
