---
name: options-flow-unusual-activity-detection
description: >-
  Options flow unusual activity detection engine scanning real-time trade feeds for volume-to-open-interest spikes (V/OI > 1.5), institutional premium blocks, and aggressive sweep orders.
domain: Quant Research & Alt Data
subdomain: Options Order Flow Analytics & Smart Money Tracking
tags: ["options-flow", "unusual-activity", "vol-to-oi", "options-sweep", "block-trades", "smart-money", "quant-research"]
brokers_frameworks: ["OPRA / Polygon Options Feed", "Pandas", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building quantitative research signals, alt-data sentiment indicators, or real-time scanners that monitor options order flow for unusual institutional activity. Institutional "smart money" often opens large directional positions prior to major corporate events (earnings, M&A) or macro announcements. This engine filters options trade streams for volume-to-open-interest spikes ($V/OI \ge 1.5$), volume-to-ADV surges ($V/ADV \ge 2.0$), and high-dollar premium block/sweep transactions ($\ge \$100,000$).

## Prerequisites

- Options trade feed (`asset_id`, `option_symbol`, `volume`, `open_interest`, `adv`, `execution_price`, `bid`, `ask`, `option_type`).
- Anomaly thresholds (`min_v_oi_ratio`: 1.5, `min_v_adv_ratio`: 2.0, `min_premium_usd`: 100,000).

## Workflow

1. **Trade Metric Computation**:
   - Compute Volume-to-OI ratio ($V/OI = \text{Volume} / \text{OpenInterest}$).
   - Compute Volume-to-ADV ratio ($V/ADV = \text{Volume} / \text{ADV}$).
   - Compute Total Premium ($USD = \text{Volume} \times \text{ExecutionPrice} \times 100$).
2. **Aggressor Direction Identification**:
   - Compare execution price to bid/ask quote:
     - Price $\ge$ Ask $\implies$ Aggressive Buy (`BUY_AT_ASK`).
     - Price $\le$ Bid $\implies$ Aggressive Sell (`SELL_AT_BID`).
3. **Unusual Activity Classification**:
   - If $V/OI \ge 1.5$ AND $V/ADV \ge 2.0$ AND Premium $\ge \$100,000$:
     - Call Buy $\implies$ `UNUSUAL_BULLISH_SWEEP`.
     - Put Buy $\implies$ `UNUSUAL_BEARISH_SWEEP`.
   - Otherwise $\implies$ `ROUTINE_FLOW`.
4. **Audit Report Generation**: Output structured `OptionsFlowAnomalyReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Delta Hedges for Directional Speculation**: Misinterpreting institutional delta-hedging trades on market-maker option inventory as pure directional bets.
- **Ignoring Open Interest Baseline**: Flagging high volume in liquid 0DTE contracts where open interest is naturally low without checking $V/ADV$.
- **Failing to Identify Aggressor Side**: Assuming all large trades are buys without checking bid/ask price location.

## Verification

- Instantiate `OptionsFlowUnusualActivityDetectionEngine`. Input trade payload with 5,000 contracts on 1,000 OI ($V/OI = 5.0$), premium $\$250,000$, executed at Ask $\implies$ verify classification `UNUSUAL_BULLISH_SWEEP`. Input routine small trade $\implies$ verify `ROUTINE_FLOW`.
- Run `python scripts/test_options_flow_unusual_activity_detection.py`.

## Related Skills

- `options-chain-data-normalization-across-vendors`
- `options-backtesting-with-realistic-iv-surface`
---
