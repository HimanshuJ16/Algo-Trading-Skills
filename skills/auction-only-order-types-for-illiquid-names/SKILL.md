---
name: auction-only-order-types-for-illiquid-names
description: Execution algorithm optimizing the use of Limit-on-Close (LOC) and Market-on-Close
  (MOC) orders to minimize market impact when trading illiquid equities.
domain: execution-algorithms
subdomain: liquidity-seeking
tags:
- execution
- illiquid
- closing-auction
- loc
- moc
brokers_frameworks:
- generic
version: "2.0.0"
author: System
license: MIT
---

## When to Use

Use this execution algorithm when attempting to buy or sell a large block of an illiquid equity (e.g., small-cap or micro-cap stocks). Trading illiquid names during the continuous market session often leads to catastrophic market impact (slippage) as the order "walks the order book."

This skill leverages the centralized liquidity event of the exchange's Closing Auction by routing the order as a Limit-on-Close (LOC) order, significantly reducing information leakage and price impact.

## When NOT to Use

**Do NOT use it** to provide contra-side liquidity against a published closing imbalance — that is `close-auction-participation-strategy`, which consumes the NOII/NYSE imbalance feed this skill deliberately does not read. It is also not an order gateway: it produces routing and pricing parameters, it does not submit, amend or cancel orders.

## Prerequisites

- Python 3.9+ (the module uses `zoneinfo`; on a bare Windows or slim container
  install the `tzdata` package so `America/New_York` resolves).
- Average Daily Volume (ADV) metrics for the target instrument.
- The **scheduled session close** for the trading date, not just the current
  time. Every deadline below is defined relative to it: NYSE's MOC/LOC entry
  deadline is the Closing Auction Imbalance Freeze Time, *ten minutes before the
  scheduled end of Core Trading Hours* (NYSE Rule 7.35(a)(8)), so on a 1:00 p.m.
  early-close day it is 12:50 p.m., not 3:50 p.m. Pass
  `market_close_et=EARLY_CLOSE_SESSION_CLOSE_ET` on half days.
- Which venue's rules apply. On a regular 4:00 p.m. ET close: NYSE accepts
  unconditional MOC/LOC entry until **3:50 p.m.**; Nasdaq rejects **MOC** at or
  after **3:55 p.m.** and **LOC** at or after **3:58 p.m.** Both freeze free
  cancel/modify at 3:50 p.m. `CLOSING_AUCTION_CUTOFF_ET` (3:50 p.m.) is the
  conservative, exchange-portable default; `entry_cutoff_for(venue, order_type,
  market_close_et)` gives the venue-specific deadline.
- A reference price (e.g. current mid-price), a slippage tolerance, and the
  instrument's **minimum price variation** to derive the LOC limit price. An LOC
  order is a *limit* order (NYSE Rule 7.31(c)(2)(A), Nasdaq Equity 4 Rule
  4702(b)(12)(A)) and **requires a limit price** at submission.

## Workflow

1. **Liquidity Assessment**: Evaluate the order size against the instrument's Average Daily Volume (ADV).
2. **Strategy Routing** via `IlliquidAuctionExecutionEngine.generate_routing_plan()`:
   - If the order is massive relative to ADV (>=5%), attempting to execute it in the continuous market is dangerous. The engine routes 100% of the order to the Closing Auction via LOC.
   - If the order is moderate (1%-5% of ADV), the engine uses a hybrid approach: slicing a portion into continuous trading (e.g., VWAP) and reserving the remainder for the Closing Auction.
   - If the order is small (<1% of ADV), route 100% to continuous VWAP/TWAP.
3. **Price Protection**: LOC is heavily preferred over MOC for illiquid names to prevent the auction imbalance from causing an extreme price dislocation against the trader. When `reference_price` and `slippage_tolerance_bps` are supplied, the engine populates `suggested_limit_price` (buy: `ref*(1+tol)` rounded **down** to `tick_size`; sell: `ref*(1-tol)` rounded **up**), so the tolerance is a hard bound and the price is a permissible minimum increment. Pass `tick_size=SUB_DOLLAR_TICK_SIZE` for names under $1.00. When no reference price is supplied, `suggested_limit_price` is `None` and the caller MUST set a limit price before submitting any LOC order.
4. **Cutoff Enforcement**: Before submitting, call `validate_submission_window(submission_time_et, market_close_et=<scheduled close>)`. Do not compare wall clocks yourself — pass a timezone-aware datetime in any zone and the module converts it to `America/New_York` first. For a venue-specific deadline, pass `cutoff=entry_cutoff_for(venue, order_type, market_close_et)`.
5. **Late-window handling**: Clearing the cutoff is necessary but not sufficient. Between the NYSE freeze time and the close, NYSE accepts only MOC/LOC orders *contra* to a published Significant Closing Imbalance and rejects all others; a Nasdaq LOC entered in its final three minutes is accepted only against a First or Second Reference Price and, if more aggressive than it, is rejected or re-priced to it. Unless you are consuming the imbalance feed (see `close-auction-participation-strategy`), treat the conservative cutoff as final.
6. **Commitment**: Auction quantity resting at the freeze time cannot be freely pulled — `cancel_modify_freeze_for(market_close_et)`. Size it as capital committed to trading at an unknown closing price.

## Common Pitfalls

- **Using MOC instead of LOC for Micro-Caps**: A massive MOC order on a micro-cap stock guarantees execution, but the resulting auction imbalance can cause the closing price to gap 10% away from the fair value. Always use LOC for illiquid names.
- **Assuming MOC and LOC share one deadline**: on Nasdaq they do not. MOC entry is rejected at or after 3:55 p.m. ET; LOC survives until 3:58 p.m. Hard-coding a single "on-close cutoff" either rejects legal LOC entries or sends MOC orders the venue will reject.
- **Hard-coding 3:50 p.m. as the cutoff**: NYSE deadlines are tied to the *scheduled* end of Core Trading Hours and move on early-close days. On a 1:00 p.m. half day the MOC/LOC deadline is 12:50 p.m.; an algorithm holding a fixed 15:50 constant will believe it has three more hours and miss the auction entirely. Always pass the session's scheduled close.
- **Comparing a non-Eastern wall clock to an ET cutoff**: 12:55 US/Pacific *is* 15:55 ET, five minutes past the NYSE deadline, but its raw time-of-day reads as safely early. `is_past_closing_auction_cutoff` and `validate_submission_window` convert to `America/New_York` first and reject naive datetimes outright.
- **Submitting an LOC without a limit price**: An LOC is a limit order and will be rejected by the exchange without one. If `reference_price` was not supplied to `generate_routing_plan`, `suggested_limit_price` will be `None` and the caller must assign a limit price before submission.
- **Sending a sub-penny limit price**: `20.01 * 1.005 = 20.11005`. Submitting `20.1101` on an NMS stock priced at or above $1.00 is not a permissible minimum increment under SEC Rule 612 and the venue rejects it. The engine rounds to `tick_size`, and rounds *away* from the aggressive side so the rounding can never breach the caller's slippage tolerance.
- **Treating the auction leg as cancellable**: after the freeze time (3:50 p.m. on a regular close) NYSE will not cancel or reduce an MOC/LOC order even to correct a legitimate error, and Nasdaq permits only legitimate-error corrections. Quantity sent to the auction is committed.

## Verification

Run `python -m unittest discover -s skills/auction-only-order-types-for-illiquid-names/scripts` to confirm that the engine allocates orders to LOC based on their size relative to the ADV, derives a tick-compliant suggested limit price that never breaches the slippage tolerance, validates its inputs, converts submission timestamps to US/Eastern before comparing them, and derives closing-auction cutoffs from the scheduled session close for both regular and early-close days.

Spot checks:
- A buy at `reference_price=20.01` with the default 50 bps tolerance yields `20.11`, not `20.1101`.
- `is_past_closing_auction_cutoff(datetime(2024, 3, 1, 12, 55, tzinfo=US/Pacific))` is `True` — that instant is 15:55 ET.
- `entry_cutoff_for(AuctionVenue.NASDAQ, OrderType.MARKET_ON_CLOSE)` is `15:55`, while the LOC cutoff is `15:58`.
- `entry_cutoff_for(AuctionVenue.NYSE, OrderType.LIMIT_ON_CLOSE, EARLY_CLOSE_SESSION_CLOSE_ET)` is `12:50`.

## Related Skills

- `close-auction-participation-strategy`
- `minimum-fill-size-and-lot-rounding-logic`
- `global-exchange-holiday-calendar-handling`
- `clock-synchronization-ptp-for-trading-hosts`
- `exchange-tick-size-regime-tracking`
