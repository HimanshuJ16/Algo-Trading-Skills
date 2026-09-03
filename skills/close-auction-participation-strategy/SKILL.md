---
name: close-auction-participation-strategy
description: >-
  Use when providing contra-side liquidity into a US closing cross against a published
  imbalance feed, or executing a rebalance at the official closing price with price
  protection. The opening cross is opening-auction-imbalance-based-execution.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: closing-auction, noii, moc, loc, imbalance, execution-algo
  brokers_frameworks: "Nasdaq TotalView-ITCH 5.0 (NOII); NYSE Closing Auction (Rule 7.35B); Generic Execution"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when participating in a US equity closing auction (Nasdaq Closing Cross, NYSE Closing Auction) to provide contra-side liquidity against a published imbalance, or to execute a rebalance order at the official closing price with price protection. The strategy consumes closing-auction imbalance data (paired shares, imbalance shares, imbalance side, near/far indicative clearing prices) and derives a Limit-On-Close (LOC) order that is legal to enter at the intended submission time.

## When NOT to Use

**Do NOT use it** for the opening cross (see `opening-auction-imbalance-based-execution`), for halt/IPO crosses or the Extended Trading Close (different order types and cutoffs — the strategy rejects those NOII cross types), or as a way to *take* liquidity in the same direction as an imbalance. It is also not a substitute for a venue order-entry gateway: it produces order parameters, it does not submit or cancel orders.

## Prerequisites

- A closing-auction imbalance feed: Nasdaq TotalView-ITCH NOII message (type `I`, Cross Type `C`) or the NYSE closing imbalance publication.
- Timezone-aware timestamps. The module converts everything to `America/New_York` and **rejects naive datetimes** — a naive UTC timestamp compared against an ET cutoff is how orders get sent after the deadline.
- System clock synchronized (PTP/NTP), because every gate in this skill is a wall-clock deadline (see `clock-synchronization-ptp-for-trading-hosts`).
- Knowledge of which venue's rules apply — the listing venue's cutoffs, not your broker's, govern acceptance.

## Workflow

1. **Feed ingestion**: Parse the imbalance message into `NoiiMessage`. Discard anything whose `cross_type` is not `C`; an opening or halt-cross NOII carries different semantics.
2. **Imbalance analysis**: Compute the ratio with `imbalance_ratio(paired_shares, imbalance_shares)` = imbalance / (paired + imbalance). It returns `0.0` on an empty book rather than dividing by zero. Screen with `min_imbalance_shares` / `min_imbalance_ratio` so noise imbalances do not trigger orders.
3. **Entry-window gate** — this is venue-specific, not a single "3:55 cutoff":
   - **Nasdaq**: MOC entry ends at 15:55 ET, LOC entry at 15:58 ET. An LOC entered from 15:55 onward may be re-priced to the 15:50/15:55 Reference Price, and is rejected outright if no 15:55 Reference Price exists — the returned order flags this as `late_entry_reprice_risk`.
   - **NYSE**: unconditional MOC/LOC entry ends at 15:50 ET. From 15:50 to 16:00 only orders contra to a **published** MOC/LOC Significant Imbalance are accepted, so set `significant_imbalance_published=True` only when the venue has actually published one.
   - Both venues freeze cancel/modify of on-close orders at 15:50 ET. Check `can_cancel_or_modify(now)` before assuming an order can be pulled.
   - Gate on the *submission* time, not the feed timestamp: pass `submission_time` so feed lag plus `safety_buffer_seconds` cannot push the order past the cutoff. An observation older than `max_message_age_seconds` at submission is refused as stale.
4. **Pricing**: Derive the limit from the chosen `price_basis`. The Far price is the clearing price of the auction-only book; the Near price includes the continuous book. A **non-positive far/near price means the venue has not disseminated one** — Nasdaq publishes no indicative clearing price for the closing cross before 15:55 ET — and the strategy refuses to price an order rather than submitting a $0.00 limit. `price_concession_bps` trades price improvement for fill probability, and limits are rounded away from the aggressive side at `tick_size`.
5. **Sizing**: Quantity is the floor of the smallest of `max_participation_pct × imbalance_shares`, `max_auction_volume_pct × (paired + imbalance)`, and the caller's `target_qty`.
6. **Post-cross reconciliation**: Match execution reports after the 16:00 cross against the official closing price (NOCP / NYSE Official Closing Price) and attribute any unfilled LOC quantity as opportunity cost.

> Full procedure: see `references/workflows.md`.
> Standards and rule citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming one cutoff time for all venues**: there is no universal 3:55 PM deadline. Nasdaq MOC 15:55 / LOC 15:58, NYSE 15:50 (contra-side to a published imbalance until 16:00). Hard-coding one number either rejects legal orders or sends rejected ones.
- **Pricing off a far/near price that was never disseminated**: Nasdaq sends no Near/Far Indicative Clearing Price for the closing cross before 15:55 ET. A naive parser reads the unsigned ITCH price field as `0` and submits a limit of $0.00. Treat any non-positive indicative price as *absent*.
- **Comparing naive timestamps to an ET cutoff**: a UTC-stamped feed at 20:56Z is 15:56 ET, but compared raw it looks like it is well past every cutoff (or, in the other direction, safely before one). Always convert.
- **Checking the cutoff against the feed timestamp**: the deadline applies to when the exchange *receives* the order. Budget for feed lag, strategy latency and broker hops with `safety_buffer_seconds`.
- **Acting on a stalled feed**: closing-cross imbalance data updates every 1–10 seconds. If the last observation is minutes old the book has moved on, and sizing against it commits capital to an imbalance that may no longer exist — `max_message_age_seconds` blocks that.
- **Forgetting the 15:50 cancel/modify freeze**: after 15:50 the contra-side order cannot be pulled on either venue. Size it as capital you are committed to trading at an unknown cross price.
- **Trading an imbalance that isn't one**: ITCH imbalance direction `O` means "insufficient orders to calculate" and `P` means the security is paused; neither is a tradable side.
- **Adding to the imbalance**: an unpriced MOC on the same side as a large institutional imbalance takes the worst of the cross print. This strategy is deliberately contra-side and always limit-priced.

## Verification

- Run `python -m unittest discover -s skills/close-auction-participation-strategy/scripts`.
- Feed a Nasdaq NOII with a 100,000-share buy imbalance and 500,000 paired shares at 15:56 ET: expect a `SELL` LOC for 10,000 shares (10% of the imbalance) priced at the far indicative price, with `imbalance_ratio ≈ 0.1667`.
- Feed the same message at 15:52 ET: expect no order and reason `INDICATIVE_PRICE_UNAVAILABLE` (Nasdaq has not published a far price yet). At 15:58 ET expect `PAST_ENTRY_CUTOFF`.
- Feed it with `venue=AuctionVenue.NYSE` at 15:52 ET: expect `ENTRY_FROZEN_NO_PUBLISHED_IMBALANCE` unless `significant_imbalance_published=True`.

## Related Skills

- `auction-only-order-types-for-illiquid-names`
- `opening-auction-imbalance-based-execution`
- `clock-synchronization-ptp-for-trading-hosts`
- `minimum-fill-size-and-lot-rounding-logic`
