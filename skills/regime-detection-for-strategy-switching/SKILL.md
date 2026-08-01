---
name: regime-detection-for-strategy-switching
description: >-
  Production-grade market regime classifier using ADX trend strength, ATR volatility z-scores, and hysteresis transition filters to route confirmed regimes to appropriate strategy variants.
domain: Alpha Research & Signal Generation
subdomain: Market Regime Classification & Strategy Routing
tags: ["regime-detection", "adx", "atr", "volatility-zscore", "hysteresis", "strategy-switching", "trend-detection"]
brokers_frameworks: ["ADX/DMI (Wilder)", "ATR (Wilder)", "Z-Score Statistics", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when running multiple strategy variants (trend-following, mean-reversion, risk-off) that each perform optimally under specific market conditions. Markets cycle through regimes — trending bull, trending bear, range-bound, and high-volatility crash — and deploying the wrong strategy in the wrong regime causes significant drawdowns. This engine classifies the current market regime using ADX for trend strength and ATR z-scores for volatility, applies a hysteresis confirmation filter to prevent whipsaw regime switches, and routes the confirmed regime to the appropriate strategy module.

## Prerequisites

- OHLC bar data (`highs`, `lows`, `closes`) with minimum 20 bars.
- Config options (`adx_trend_threshold`: default 25, `adx_ranging_threshold`: default 20, `volatility_z_threshold`: default 2.0, `hysteresis_bars`: default 3).

## Workflow

1. **ATR Volatility Z-Score Calculation**:
   - Compute 14-period ATR series; calculate z-score of latest ATR vs historical mean/stddev.
2. **ADX/DMI Trend Strength Classification**:
   - Compute ADX, +DI, -DI; classify trend direction and strength.
3. **Raw Regime Classification**:
   - If vol z-score $\ge 2.0$ → `HIGH_VOLATILITY_CRASH`. If ADX $\ge 25$ and +DI > -DI → `BULL_TRENDING`. If ADX $\ge 25$ and -DI > +DI → `BEAR_TRENDING`. Else → `MEAN_REVERTING_RANGING`.
4. **Hysteresis Transition Filter**:
   - Require $N$ consecutive bars ($N = 3$) confirming new regime before switching.
5. **Strategy Variant Routing**: Map confirmed regime to strategy module.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **No Hysteresis Filter**: Switching strategies on every bar causes whipsaw losses during regime transitions.
- **Static Thresholds Across Instruments**: Using the same ADX/ATR thresholds for highly liquid and illiquid instruments.
- **Ignoring Volatility Regime**: Treating high-volatility crash as just a "trending" market leads to outsized drawdowns.

## Verification

- Instantiate `MarketRegimeDetector`. Feed steady range-bound bars → verify `MEAN_REVERTING_RANGING`. Feed sharp volatility spike 3 times → verify hysteresis triggers `HIGH_VOLATILITY_CRASH`. Verify strategy variant routing maps correctly.
- Run `python scripts/test_regime_detector.py`.

## Related Skills

- `hidden-markov-model-regime-switching`
- `volatility-regime-adaptive-position-sizing`
---
