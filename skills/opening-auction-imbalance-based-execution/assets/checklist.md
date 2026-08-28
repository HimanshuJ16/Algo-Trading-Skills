# Pre-Flight Checklist — Opening Auction Imbalance-Based Execution

## Feed and message screening

- [ ] Is the message an **opening** cross (ITCH Cross Type `O`)? Closing (`C`),
      IPO/halt (`H`) and Extended Trading Close (`A`) crosses are rejected.
- [ ] Are imbalance directions `P` (paused) and `O` (insufficient orders to
      calculate) handled as market states, not as failed thresholds?
- [ ] Is the observation fresher than `max_feed_age_seconds`? Nasdaq updates
      every 10s before 09:28 and every second after.
- [ ] Is `seconds_to_open` derived from a **timezone-aware**, PTP/NTP-synced
      clock, and validated as finite before any deadline comparison?

## Venue timing

- [ ] Is the entry cutoff the one for **this venue and this order type**?
      Nasdaq MOO 09:28, LOO 09:29:30, OIO until the cross; NYSE MOO/LOO until
      the DMM opens the security; NYSE has no OIO.
- [ ] Is the cutoff evaluated against the projected **arrival** time, with
      `entry_safety_buffer_seconds` set from a *measured* strategy-to-exchange
      latency?
- [ ] Has `report.is_cancellable` been read? On Nasdaq it is always `False` —
      the 09:25 cancel freeze coincides with the start of imbalance
      dissemination, so the order cannot be pulled once sent.
- [ ] For a Nasdaq LOO arriving after 09:28, is `late_loo_reprice_risk`
      acceptable? The venue may re-price against the 09:28 Current Reference
      Price or the prior day's NOCP and convert DAY to IOC.

## Pricing

- [ ] Is a non-positive far/near price treated as **absent** rather than as
      $0.00? Nasdaq publishes neither before 09:28.
- [ ] If a limit is needed before 09:28, is `price_basis` set to `REF` (the
      Current Reference Price is disseminated from 09:25)?
- [ ] Are `feed_warnings` empty? A populated near/far price before 09:28 means
      the feed parser is misreading the message.
- [ ] Are limits rounded **away from the aggressive side** at the correct tick,
      leaving prices already on a tick untouched?
- [ ] Is an unpriced MOO genuinely intended (`allow_unpriced_moo=True`), knowing
      it has no price protection at the cross?

## Sizing and risk

- [ ] Is the quantity the smallest of the `size`, `participation_pct` and
      `max_pct_of_auction_volume` caps, floored to `lot_size`?
- [ ] Does `min_order_qty` suppress a too-small order rather than rounding a
      capped quantity *up* past a cap?
- [ ] Is the quantity sized as capital you are committed to trading at an
      unknown cross price (Nasdaq), not as a cancellable working order?
- [ ] Is the order contra-side to the imbalance, never adding to it?

## Order lifecycle

- [ ] Does every generated order carry a deterministic `client_order_id`, so a
      republished imbalance returns `DUPLICATE_SUPPRESSED` instead of a second
      submission?
- [ ] Is `session_date` populated, so idempotency keys do not collide across
      sessions?
- [ ] Are post-cross fills reconciled against the official opening price, with
      unfilled quantity attributed as opportunity cost?

## Before going live

- [ ] Have the deadlines in `VENUE_RULES` been re-confirmed against the current
      Nasdaq and NYSE rulebooks? Auction rules change, and stale secondary
      sources still show the pre-2021 Nasdaq cancel/modify freeze.
- [ ] Are the strategy thresholds calibrated against your own execution data
      rather than left at the defaults?
