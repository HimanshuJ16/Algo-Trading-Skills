---
name: regime-detection-for-strategy-switching
description: Use when building adaptive quantitative trading systems to detect market
  regime shifts (trending, ranging, high-volatility) using ADX, ATR z-scores, and
  hysteresis filters to dynamically route active strategy variants
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- regime-detection
- adx-indicator
- strategy-switching
- hysteresis-filter
brokers_frameworks:
- scikit-learn
- hmmlearn
- TA-Lib
- Custom Python Regimes
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a quantitative trading bot operates across changing market regimes. Trend-following strategies experience severe drawdowns in choppy sideways markets, while mean-reversion strategies suffer catastrophic losses during strong directional trend breakouts. Running a static strategy regardless of market conditions causes structural underperformance. Implementing real-time regime classification (`BULL_TRENDING`, `BEAR_TRENDING`, `MEAN_REVERTING_RANGING`, `HIGH_VOLATILITY_CRASH`), multi-feature ADX/ATR z-score indicators, and hysteresis confirmation filters to dynamically switch active strategy modules is mandatory.

## Prerequisites

- Historical or streaming price bars (OHLCV) with minimum window length (e.g. 50 bars).
- Defined strategy variants (`TrendStrategy`, `MeanReversionStrategy`, `HaltStrategy`).
- Configured regime thresholds (ADX trend threshold $= 25.0$, ATR z-score threshold $= 2.0$).

## Workflow

1. **Calculate Microstructure Regime Features**:
   - Calculate Average Directional Index (ADX) and Directional Movement (+DI, -DI).
   - Calculate Normalized Volatility Z-score:
     $$Z_{\text{vol}} = \frac{\text{ATR}_t - \mu_{\text{ATR}}}{\sigma_{\text{ATR}}}$$
   - Calculate Price Trend Slope relative to SMA.

2. **Classify Raw Market Regime**:
   - If $Z_{\text{vol}} \ge 2.0$: Classify as `HIGH_VOLATILITY_CRASH` (Route: Risk-off / De-leverage).
   - Else if $\text{ADX} \ge 25.0$ and $+\text{DI} > -\text{DI}$: Classify as `BULL_TRENDING` (Route: Trend Long).
   - Else if $\text{ADX} \ge 25.0$ and $-\text{DI} > +\text{DI}$: Classify as `BEAR_TRENDING` (Route: Trend Short).
   - Else ($\text{ADX} < 20.0$): Classify as `MEAN_REVERTING_RANGING` (Route: Mean Reversion).

3. **Apply Hysteresis Transition Filter**:
   - Require $N=3$ consecutive bars of new regime classification before switching active strategy mode to prevent rapid strategy flapping.

4. **Route Active Strategy Variant**:
   - Execute `route_strategy_variant(regime)`. Deactivate incompatible strategies and initialize target strategy parameters.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Strategy Flapping**: Switching strategies on single-bar noise spikes without a hysteresis confirmation window ($N \ge 3$).
- **Running Trend Strategies in Ranging Markets**: Leaving trend-following logic active during low-ADX range-bound consolidations.
- **Ignoring Volatility Spikes**: Failing to detect high-volatility crash regimes where all directional signals become unreliable.

## Verification

- Submit trending bar series ($\text{ADX} = 35.0, +\text{DI} > -\text{DI}$) and verify classification is `BULL_TRENDING`.
- Submit high-volatility spike bar series ($Z_{\text{vol}} = 2.5$) and verify classification is `HIGH_VOLATILITY_CRASH`.
- Verify hysteresis filter requires 3 consecutive bars before changing active strategy routing.
- Run unit test suite `python scripts/test_regime_detector.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-optimization-window-management`
- `kill-switch-and-drawdown-circuit-breakers`
- `ensemble-signal-combination-without-overfitting`
---
