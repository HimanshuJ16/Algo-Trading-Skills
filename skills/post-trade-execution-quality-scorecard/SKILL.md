---
name: post-trade-execution-quality-scorecard
description: >-
  Post-trade execution quality scorecard engine evaluating Implementation Shortfall (IS), VWAP slippage, Effective vs Quoted Ratio (EQR), fill rates, and SEC Rule 605 metrics.
domain: Execution Algorithms
subdomain: Transaction Cost Analysis & Execution Quality
tags: ["tca", "execution-quality", "implementation-shortfall", "vwap-slippage", "sec-rule-605", "effective-spread", "scorecard"]
brokers_frameworks: ["SEC Rule 605/606 TCA Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating broker and algo wheel execution performance post-trade. Institutional investors and regulatory guidelines (SEC Rule 605, MiFID II RTS 27/28) demand systematic measurement of execution quality beyond basic price fills. This engine computes Implementation Shortfall ($IS$ in bps), VWAP slippage, Effective vs Quoted Ratio ($EQR$), and Fill Rates, assigning a composite execution quality grade ($A$ through $F$) to brokers and execution venues.

## Prerequisites

- Executed order records (`order_id`, `venue`, `symbol`, `side`: `'BUY'`/`'SELL'`, `parent_qty`, `executed_qty`, `avg_fill_price`, `arrival_price`, `market_vwap`, `arrival_midquote`, `arrival_quoted_spread`).
- Scorecard config (`benchmark_target_is_bps`: default 10.0 bps).

## Workflow

1. **Implementation Shortfall & VWAP Calculation**:
   - Compute Implementation Shortfall:
     $$IS_{\text{bps}} = \text{SideSign} \cdot \frac{\text{AvgFillPrice} - \text{ArrivalPrice}}{\text{ArrivalPrice}} \cdot 10000$$
   - Compute VWAP Slippage:
     $$\text{Slippage}_{\text{VWAP, bps}} = \text{SideSign} \cdot \frac{\text{AvgFillPrice} - \text{MarketVWAP}}{\text{MarketVWAP}} \cdot 10000$$
2. **Effective Spread & EQR Calculation**:
   - Compute Effective Spread $= 2 \cdot \text{SideSign} \cdot (\text{AvgFillPrice} - \text{ArrivalMidquote})$.
   - Compute Effective vs Quoted Ratio:
     $$EQR = \frac{\text{EffectiveSpread}}{\text{ArrivalQuotedSpread}}$$
3. **Fill Rate & Composite Grading**:
   - Compute Fill Rate $= \frac{\text{ExecutedQty}}{\text{ParentQty}} \cdot 100\%$.
   - Assign Composite Grade: $A \ge 90$, $B \ge 80$, $C \ge 70$, $D \ge 60$, $F < 60$.
4. **Audit Report Generation**: Output structured `ExecutionQualityScorecardReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying Solely on VWAP**: Using VWAP as the sole metric, which can be gamed by trading slowly in high-volume windows while incurring severe Implementation Shortfall.
- **Ignoring Fill Rates**: Rewarding brokers with low slippage on partial fills while ignoring high cancellation/unfilled rates.
- **Uncorrected Side Sign**: Failing to invert slippage math for SELL orders (where higher fill price represents positive alpha).

## Verification

- Instantiate `PostTradeExecutionQualityScorecard`. Input BUY order ($1,000$ shares @ $\$100.05$ fill vs $\$100.00$ arrival price, $\$100.10$ VWAP, $\$100.00$ midquote, $\$0.10$ quoted spread) $\implies$ verify $IS = +5.0$ bps, VWAP slippage $= -4.99$ bps, $EQR = 1.0$, Fill Rate $= 100\%$, and Composite Grade $A$.
- Run `python scripts/test_post_trade_execution_quality_scorecard.py`.

## Related Skills

- `execution-cost-model-recalibration-cadence`
- `algo-wheel-broker-execution-quality-comparison`
---
