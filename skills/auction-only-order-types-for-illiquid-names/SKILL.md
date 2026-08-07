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
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this execution algorithm when attempting to buy or sell a large block of an illiquid equity (e.g., small-cap or micro-cap stocks). Trading illiquid names during the continuous market session often leads to catastrophic market impact (slippage) as the order "walks the order book." 

This skill leverages the centralized liquidity event of the exchange's Closing Auction by routing the order as a Limit-on-Close (LOC) or Market-on-Close (MOC) order, significantly reducing information leakage and price impact.

## Prerequisites

- Python 3.9+
- Average Daily Volume (ADV) metrics for the target instrument.
- Exchange cutoff times for MOC/LOC order submission (e.g., 3:50 PM ET for NYSE/Nasdaq).

## Workflow

1. **Liquidity Assessment**: Evaluate the order size against the instrument's Average Daily Volume (ADV).
2. **Strategy Routing**:
   - If the order is massive relative to ADV (>5%), attempting to execute it in the continuous market is dangerous. The engine routes 100% of the order to the Closing Auction via LOC.
   - If the order is moderate (1%-5% of ADV), the engine uses a hybrid approach: slicing a portion into continuous trading (e.g., VWAP) and reserving the remainder for the Closing Auction.
3. **Price Protection**: LOC (Limit on Close) is heavily preferred over MOC (Market on Close) for illiquid names to prevent the auction imbalance from causing an extreme price dislocation against the trader.

## Common Pitfalls

- **Using MOC instead of LOC for Micro-Caps**: A massive MOC order on a micro-cap stock guarantees execution, but the resulting auction imbalance can cause the closing price to gap 10% away from the fair value. Always use LOC for illiquid names.
- **Missing the Cutoff Time**: Exchanges prohibit the submission or cancellation of MOC/LOC orders after a specific time (e.g., 3:50 PM). Algorithms must account for this rigid deadline.

## Verification

Run `python scripts/test_auction_only_order_types_for_illiquid_names.py` to confirm that the engine properly allocates orders to LOC based on their size relative to the ADV.

## Related Skills

- `close-auction-participation-strategy`
- `minimum-fill-size-and-lot-rounding-logic`
