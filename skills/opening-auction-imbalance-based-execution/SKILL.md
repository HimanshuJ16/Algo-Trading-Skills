---
name: opening-auction-imbalance-based-execution
description: >-
  Opening auction imbalance execution engine evaluating Nasdaq NOII and NYSE opening cross feeds, computing imbalance ratios, enforcing cutoff windows, and routing contra-side MOO/LOO orders.
domain: Market Microstructure & Execution Algorithms
subdomain: Opening Cross & Auction Imbalance Execution
tags: ["opening-auction", "noii", "imbalance-execution", "market-on-open", "limit-on-open", "nyse-cross", "nasdaq-cross"]
brokers_frameworks: ["Nasdaq NOII / NYSE Opening Cross Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when executing orders during exchange opening auctions (Nasdaq Opening Cross, NYSE Opening Auction) based on real-time net order imbalance indicators (NOII). During the opening process, large buy or sell imbalances indicate supply/demand skew and price impact. Providing contra-side liquidity (submitting Sell MOO against Buy Imbalances or Buy MOO against Sell Imbalances) allows trading desks to execute large volumes at the single opening clearing price while capturing favorable liquidity premiums.

## Prerequisites

- Real-time opening auction imbalance feed (`paired_qty`, `imbalance_qty`, `imbalance_side`, `near_price`, `far_price`, `ref_price`, `seconds_to_open`).
- Strategy parameters (`imbalance_ratio_threshold`: e.g. 0.20, `min_imbalance_qty`: 10,000, `cutoff_seconds_to_open`: 120.0).

## Workflow

1. **Imbalance Feed Data Ingestion & Ratio Calculation**:
   - Parse opening imbalance metrics. Compute Imbalance Ratio:
     $$\text{ImbalanceRatio} = \frac{\text{ImbalanceQty}}{\text{PairedQty} + \text{ImbalanceQty}}$$
2. **Cutoff Window Audit**:
   - Verify current time is before exchange order entry cutoff (e.g. `seconds_to_open >= 120.0` for 09:28 AM EST cutoff).
3. **Contra-Side Auction Order Generation**:
   - If Buy Imbalance (`'B'`) breaches ratio threshold $\implies$ Generate contra-side `SELL` Market-On-Open (MOO) or Limit-On-Open (LOO) order.
   - If Sell Imbalance (`'S'`) breaches ratio threshold $\implies$ Generate contra-side `BUY` MOO / LOO order.
4. **Audit Report Generation**: Output structured `AuctionExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing Exchange Order Cutoffs**: Attempting to submit or cancel MOO/LOO orders after the exchange lockout window (e.g. 09:28 AM EST on Nasdaq), causing exchange order rejections.
- **Executing Against Small Noise Imbalances**: Triggering orders on minor share imbalances without enforcing minimum threshold quantity (`min_imbalance_qty`) or ADV ratio limits.
- **Failing to Handle Zero Paired Volume**: Division-by-zero errors when paired quantity is zero before auction initialization.

## Verification

- Instantiate `OpeningAuctionImbalanceBasedExecutionEngine`. Input Buy Imbalance (100,000 imbalance, 300,000 paired $\implies 25\%$ ratio) at 180s to open $\implies$ verify `SELL` MOO order generation. Input imbalance past cutoff (60s to open) $\implies$ verify cutoff lockout (`CUTOFF_EXCEEDED_NO_ORDER`).
- Run `python scripts/test_opening_auction_imbalance_based_execution.py`.

## Related Skills

- `auction-only-order-types-for-illiquid-names`
- `order-book-microstructure-signal-research`
---
