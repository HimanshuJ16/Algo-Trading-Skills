---
name: tick-size-pilot-program-impact-assessment
description: "Institutional market microstructure analytics skill for assessing tick size regime changes (e.g. SEC Tick Size Pilot $0.05 widening, sub-penny regimes, MiFID II bands), measuring spread components, queue dynamics, and recalibrating algo execution strategies."
domain: Market Microstructure
subdomain: Order Book Dynamics
tags:
- market-microstructure
- tick-size-pilot
- spread-decomposition
- queue-dynamics
- algo-execution
- order-book
brokers_frameworks:
- direct-market-access
- qtg
- kdb-plus
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when analyzing the quantitative impact of tick size regime changes (e.g., SEC Regulation NMS Tick Size Pilot Program, MiFID II RTS 28 dynamic tick bands, sub-penny pricing, or exchange-wide tick increments) on asset liquidity and algorithm execution.

This engine provides institutional mechanisms to:
- Decompose bid-ask spreads into **Quoted Spread**, **Effective Spread**, and **Realized Spread (5-minute)**.
- Quantify changes in top-of-book (L1) depth, queue waiting times, and adverse selection for passive limit orders.
- Measure Order-to-Trade Ratios (OTR) and execution fill rates across different tick regimes.
- Provide automated parameter recalibration recommendations for Passive Market Making, TWAP/VWAP Slicing, and Momentum Taker execution algorithms.

## Prerequisites

- Python 3.9+
- High-frequency tick-by-tick (L1/L2) order book data with timestamp precision (milliseconds/nanoseconds).
- Historical trade records containing trade price, side (buy/sell aggressor), and post-trade 5-minute midpoints.

## Workflow

1. **Ingest Order Book & Trade Data**: Format market data snapshots into `TickSnapshot` objects containing bid/ask prices, top-of-book sizes, aggressor side, and 5-minute future midpoints.
2. **Evaluate Baseline Microstructure Metrics**: Invoke `evaluate_microstructure_metrics()` for the baseline tick regime (e.g., `$0.01` standard cent) to compute baseline quoted spread, effective spread, realized spread, L1 depth, and adverse selection in bps.
3. **Evaluate Test Microstructure Metrics**: Run `evaluate_microstructure_metrics()` for the test tick regime (e.g., `$0.05` widened pilot group or sub-penny regime).
4. **Execute Regime Comparison**: Call `compare_regimes()` to generate `RegimeComparisonResult`, calculating percentage shifts in spreads, depth, fill rates, and adverse selection.
5. **Recalibrate Algorithmic Execution**: Pass comparison outcomes to `recommend_strategy_tuning()` specifying the target algorithm type (`PASSIVE_MARKET_MAKING`, `TWAP_VWAP_SLICING`, `MOMENTUM_TAKER`) to receive quantitative parameter tuning recommendations.

## Common Pitfalls

- **Confusing Quoted vs. Effective Spread**: Quoted spread measures posted top-of-book width, whereas Effective Spread measures actual execution costs paid by crossing orders. Widened tick regimes artificially expand quoted spread while effective spread may vary.
- **Ignoring Adverse Selection**: Passive limit orders in widened tick regimes face longer queue times; orders filled at the queue tail are disproportionately exposed to toxic flow (adverse selection).
- **Neglecting Queue Priority Subordination**: In $0.05 tick regimes, queue depth expands dramatically. Algorithms using standard limit orders get stuck at the back of massive queues unless pegged order types with price offsets are used.
- **Miscalculating Realized Spread Window**: Using too short or too long a time horizon for realized spread distorts market maker profitability estimates. Standard benchmark is 5 minutes.

## Verification

Execute the unit test suite to validate spread decomposition, queue depth calculations, regime comparisons, and strategy recommendations:

```bash
python -m unittest discover -s skills/tick-size-pilot-program-impact-assessment/scripts
```

## Related Skills

- `order-book-depth-processing-l2-l3`
- `adverse-selection-measurement-for-passive-orders`
- `queue-position-modeling-for-passive-orders`
- `execution-slippage-attribution-timing-vs-sizing`

