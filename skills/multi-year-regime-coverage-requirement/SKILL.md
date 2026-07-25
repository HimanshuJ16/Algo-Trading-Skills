---
name: multi-year-regime-coverage-requirement
description: >-
  Use when validating strategy backtests to segment historical price data into distinct market regimes (Bull Trend, Bear Market, High Volatility Crash, Low Volatility Range), enforce multi-regime coverage rules (>=3 regimes), and de-average performance metrics.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "regime-classification", "market-regimes", "multi-year-backtest", "robustness-testing", "de-averaged-performance"]
brokers_frameworks: ["Market Regime Coverage Engine", "Python Pandas", "NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when performing strategy validation prior to live trading promotion. A strategy that shows stellar backtest metrics over 1-2 years of a trending bull market often experiences severe catastrophic drawdown during high-volatility crashes or sideways ranging periods. This skill classifies historical data into distinct market regimes, mandates minimum multi-regime coverage (e.g., at least 3 distinct regimes across $\ge 3$ years), and breaks down performance by regime.

## Prerequisites

- Multi-year daily or intraday price series (Open, High, Low, Close, Volume).
- Minimum history span requirement (e.g. 3 years / 750 trading days).

## Workflow

1. **Segment Historical Data into Market Regimes**:
   - Compute rolling annualized volatility $\sigma_{20d}$ and 50-day moving average slope $\mu_{50d}$.
   - Classify windows into `BULL_TREND`, `BEAR_MARKET`, `HIGH_VOLATILITY_CRASH`, and `LOW_VOLATILITY_RANGE`.

2. **Audit Multi-Year Regime Coverage**:
   - Verify total backtest duration $\ge T_{\text{min}}$ (e.g. 3 years) and unique regimes covered $\ge 3$.

3. **De-average Performance Across Regimes**:
   - Calculate regime-specific Sharpe ratios, win rates, and max drawdowns ($R_{\text{bull}}, R_{\text{bear}}, R_{\text{crash}}, R_{\text{range}}$).

4. **Enforce Promotion Thresholds**:
   - Veto promotion if strategy experiences catastrophic drawdown ($> 25\%$) in any individual regime, even if aggregate multi-year Sharpe is high.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing Only in Bull Regimes**: Backtesting momentum strategies exclusively during 2020–2021 liquidity expansion, masking severe 2022 bear market drawdowns.
- **Reporting Single Aggregate Sharpe**: Masking a $-40\%$ crash drawdown behind an overall $2.5$ Sharpe ratio driven by one strong regime.
- **Arbitrary Window Slicing**: Manually picking start/end dates to exclude crash periods instead of using algorithmic regime classification.

## Verification

- Submit 4-year dataset spanning Bull, Bear, and High Volatility regimes, verifying regime segmentation.
- Verify veto trigger when strategy suffers catastrophic drawdown in Bear regime.
- Run `python scripts/test_regime_coverage.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `monte-carlo-strategy-robustness-testing`
- `paper-to-live-promotion-checklist`
---
