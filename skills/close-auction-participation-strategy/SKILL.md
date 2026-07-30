---
name: close-auction-participation-strategy
description: Quantitative execution strategy for parsing Net Order Imbalance Indicator
  (NOII) feed data and placing contra-side Limit-On-Close (LOC) / Imbalance-Only (IO)
  orders before exchange cutoff times.
domain: Execution Algorithms
subdomain: Auction Mechanics
tags:
- closing-auction
- noii
- moc
- loc
- imbalance
- execution-algo
brokers_frameworks:
- Nasdaq NOII
- NYSE Closing Auction
- Generic Execution
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when participating in exchange closing auctions (e.g., Nasdaq Closing Cross, NYSE Closing Auction) to capture liquidity or execute large portfolio rebalance orders with minimal market impact. The strategy processes the Net Order Imbalance Indicator (NOII) data stream (paired shares, imbalance shares, imbalance direction, far/near indicative prices) to calculate optimal Limit-On-Close (LOC) or Imbalance-Only (IO) order parameters before hard exchange regulatory cutoffs (e.g., 3:50 PM / 3:55 PM ET).

## Prerequisites

- Access to exchange NOII (Net Order Imbalance Indicator) or equivalent auction feed data.
- System clock synchronized via PTP to prevent submitting orders after exchange cutoff times.

## Workflow

1. **Feed Ingestion**: Receive real-time NOII updates (paired shares, imbalance shares, imbalance side, far price, near price).
2. **Imbalance Analysis**: Calculate net imbalance ratio: $\text{Imbalance Ratio} = \frac{\text{Imbalance Shares}}{\text{Paired Shares} + \text{Imbalance Shares}}$.
3. **Cutoff Guard**: Check time relative to the regulatory cutoff (e.g. 3:55 PM ET for Nasdaq MOC/LOC entry).
4. **Order Sizing & Pricing**:
   - If providing liquidity (contra-side), place a Limit-On-Close (LOC) order opposing the imbalance direction.
   - Price the LOC order conservatively at or inside the Far Indicative Price to ensure execution while providing price improvement.
5. **Execution Verification**: Verify order receipt and lock-in prior to final cross execution at 4:00 PM ET.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing the Regulatory Cutoff**: Submitting or modifying MOC/LOC orders after the strict exchange cutoff (e.g., 3:50 PM ET for NYSE or 3:55 PM ET for Nasdaq). Late orders are automatically rejected by the exchange.
- **Ignoring Far vs. Near Price**: Using the continuous market price instead of the Near/Far Indicative Clearing Price. Near price includes continuous market orders; Far price includes auction-only orders. Misinterpreting these leads to bad pricing.
- **Sweeping Toxic Flow**: Placing unpriced MOC orders on the same side as a massive institutional imbalance, incurring severe adverse selection at the close.

## Verification

- Feed a mock NOII stream with a 100,000 share BUY imbalance. Verify that `CloseAuctionParticipationStrategy` places a contra-side (SELL) LOC order priced at the Far Indicative Price, and rejects any order attempt after the 3:55 PM cutoff.
- Run `python scripts/test_close_auction_participation_strategy.py`.

## Related Skills

- `auction-only-order-types-for-illiquid-names`
- `clock-synchronization-ptp-for-trading-hosts`
