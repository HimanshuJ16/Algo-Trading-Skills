---
name: order-to-trade-ratio-fee-penalty-avoidance
description: >-
  Order-to-Trade Ratio (OTR) fee penalty avoidance engine monitoring count and volume OTR, calculating exchange surcharge penalties, and enforcing defensive order throttling.
domain: Market Microstructure & Regulatory Compliance
subdomain: Exchange Fee Optimization & OTR Throttling
tags: ["otr", "order-to-trade-ratio", "exchange-fees", "fee-penalty", "quote-stuffing", "hft-compliance", "microstructure"]
brokers_frameworks: ["MiFID II OTR Spec", "NSE / Deutsche Börse / LSE OTR Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when running automated execution or high-frequency market-making algorithms on exchanges enforcing Order-to-Trade Ratio (OTR) regulatory regimes (e.g. Deutsche Börse, LSE, NSE, B3, HKEX). High-frequency quote modifications and cancellations without matching execution volume trigger punitive exchange surcharges (e.g. $€0.01 - €0.05$ per excess order message) and potential session trading suspensions. This engine tracks live session OTR, calculates accrued penalty fees, and throttles order modifications when approaching regulatory thresholds.

## Prerequisites

- Live session activity metrics (`orders_count`, `cancels_count`, `modifies_count`, `trades_count`, `ordered_volume`, `traded_volume`).
- Exchange OTR policy configuration (`max_count_otr`, `max_volume_otr`, `warning_threshold_pct`, `penalty_fee_per_excess_order`).

## Workflow

1. **Session OTR Metrics Calculation**:
   - Compute total order messages: $M_{\text{total}} = \text{Orders} + \text{Cancels} + \text{Modifies}$.
   - Count OTR:
     $$\text{OTR}_{\text{count}} = \frac{M_{\text{total}}}{\max(1, \text{Trades})}$$
   - Volume OTR:
     $$\text{OTR}_{\text{vol}} = \frac{\text{OrderedVolume}}{\max(1.0, \text{TradedVolume})}$$
2. **Excess Messages & Penalty Surcharge Computation**:
   - Max allowable messages: $M_{\text{allowed}} = \text{Trades} \times \text{MaxCountOTR}$.
   - Excess Messages: $M_{\text{excess}} = \max(0, M_{\text{total}} - M_{\text{allowed}})$.
   - Accrued Surcharge Fee: $\text{PenaltyFee} = M_{\text{excess}} \times \text{FeePerExcessOrder}$.
3. **Defensive Order Throttling Guard**:
   - If $\text{OTR}_{\text{count}} \ge 0.80 \times \text{MaxCountOTR} \implies$ Trigger `THROTTLE_ORDER_MODIFICATIONS`.
   - If $\text{OTR}_{\text{count}} \ge \text{MaxCountOTR} \implies$ Trigger `FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL`.
4. **Audit Report Generation**: Output structured `OTRReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Modify Messages in OTR Count**: Counting only new orders while omitting rapid price modifications that contribute to exchange message limits.
- **Continuing Passive Quoting After Breach**: Allowing bots to submit thousands of un-filled passive quote updates while in breach state, accumulating thousands in exchange fines.
- **Failing to Execute Taker Fills to Reset OTR**: Not utilizing deliberate small taker orders to increase the trades denominator and lower OTR.

## Verification

- Instantiate `OrderToTradeRatioFeePenaltyEngine`. Input session with 1,000 order messages and 10 trades ($100:1$ count OTR vs $50:1$ max limit) $\implies$ verify $500$ excess messages, $\$25.00$ penalty fee (at $\$0.05$/msg), and status `OTR_BREACH_PENALTY_ACTIVE`. Input compliant session ($20:1$ count OTR) $\implies$ verify `OTR_COMPLIANT_SAFE`.
- Run `python scripts/test_order_to_trade_ratio_fee_penalty_avoidance.py`.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `execution-venue-fee-tier-optimization`
---
