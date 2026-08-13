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
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this execution algorithm when attempting to buy or sell a large block of an illiquid equity (e.g., small-cap or micro-cap stocks). Trading illiquid names during the continuous market session often leads to catastrophic market impact (slippage) as the order "walks the order book." 

This skill leverages the centralized liquidity event of the exchange's Closing Auction by routing the order as a Limit-on-Close (LOC) or Market-on-Close (MOC) order, significantly reducing information leakage and price impact.

## Prerequisites

- Python 3.9+
- Average Daily Volume (ADV) metrics for the target instrument.
- Exchange cutoff times for MOC/LOC order submission. The conservative,
  exchange-portable cutoff is **3:50 PM ET**: NYSE prohibits new MOC/LOC entry
  after 3:50 PM (except contra-side offsetting orders), and both NYSE and Nasdaq
  freeze modification/cancellation of MOC/LOC orders at 3:50 PM. Nasdaq continues
  accepting new MOC/LOC entries until 3:58 PM ET (see `CLOSING_AUCTION_CUTOFF_ET`
  and `NASDAQ_LOC_ENTRY_CUTOFF_ET` constants).
- A reference price (e.g. current mid-price) and a slippage tolerance to derive
  the LOC limit price. An LOC order is a *limit* order (NYSE Rule 7.35(B),
  Nasdaq Equity Rule 4) and **requires a limit price** at submission.

## Workflow

1. **Liquidity Assessment**: Evaluate the order size against the instrument's Average Daily Volume (ADV).
2. **Strategy Routing** via `IlliquidAuctionExecutionEngine.generate_routing_plan()`:
   - If the order is massive relative to ADV (>=5%), attempting to execute it in the continuous market is dangerous. The engine routes 100% of the order to the Closing Auction via LOC.
   - If the order is moderate (1%-5% of ADV), the engine uses a hybrid approach: slicing a portion into continuous trading (e.g., VWAP) and reserving the remainder for the Closing Auction.
   - If the order is small (<1% of ADV), route 100% to continuous VWAP/TWAP.
3. **Price Protection**: LOC (Limit on Close) is heavily preferred over MOC (Market on Close) for illiquid names to prevent the auction imbalance from causing an extreme price dislocation against the trader. When `reference_price` and `slippage_tolerance_bps` are supplied, the engine populates `suggested_limit_price` (buy: `ref*(1+tol)`, sell: `ref*(1-tol)`); otherwise it is `None` and the caller MUST set a limit price before submitting any LOC order.
4. **Cutoff Enforcement**: Before submitting, call `validate_submission_window(submission_time_et)` to reject submissions at or past 3:50 PM ET (NYSE Rule 7.35B / Nasdaq Equity Rule 4).

## Common Pitfalls

- **Using MOC instead of LOC for Micro-Caps**: A massive MOC order on a micro-cap stock guarantees execution, but the resulting auction imbalance can cause the closing price to gap 10% away from the fair value. Always use LOC for illiquid names.
- **Missing the Cutoff Time**: Exchanges prohibit the submission or cancellation of MOC/LOC orders after a specific time (3:50 PM ET for NYSE entry and for cancel/modify on both NYSE and Nasdaq; 3:58 PM ET for Nasdaq new entry). Algorithms must account for this rigid deadline via `validate_submission_window`.
- **Submitting an LOC without a limit price**: An LOC is a limit order and will be rejected by the exchange without one. If `reference_price` was not supplied to `generate_routing_plan`, `suggested_limit_price` will be `None` and the caller must assign a limit price before submission.
- **Timezone errors in cutoff checks**: `is_past_closing_auction_cutoff` and `validate_submission_window` require a timezone-aware datetime expressed in US/Eastern time; naive datetimes raise `ValueError` to prevent silent UTC/ET misinterpretation.

## Verification

Run `python -m unittest discover -s skills/auction-only-order-types-for-illiquid-names/scripts` to confirm that the engine properly allocates orders to LOC based on their size relative to the ADV, derives a suggested limit price when a reference price is supplied, validates order inputs, and enforces the closing-auction cutoff.

## Related Skills

- `close-auction-participation-strategy`
- `minimum-fill-size-and-lot-rounding-logic`
