# Deep Workflow Reference — regime-detection-for-strategy-switching

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Calculate Microstructure Features:**
   - Compute ADX, +DI, and -DI over period $N=14$.
   - Compute ATR Z-score: $Z_{\text{vol}} = \frac{\text{ATR}_t - \mu_{\text{ATR}}}{\sigma_{\text{ATR}}}$.

2. **Classify Raw Market Regime Candidate:**
   - If $Z_{\text{vol}} \ge 2.0$: `HIGH_VOLATILITY_CRASH`.
   - Else if $\text{ADX} \ge 25.0$ and $+\text{DI} > -\text{DI}$: `BULL_TRENDING`.
   - Else if $\text{ADX} \ge 25.0$ and $-\text{DI} > +\text{DI}$: `BEAR_TRENDING`.
   - Else: `MEAN_REVERTING_RANGING`.

3. **Apply Hysteresis Transition Filter:**
   - Require $N=3$ consecutive bars of new candidate classification before updating `confirmed_regime`.

4. **Route Active Strategy Variant:**
   - Invoke `route_strategy_variant(confirmed_regime)`. Automatically initialize matching strategy parameters.

## Failure Modes Observed in Production

- **Strategy Flapping:** Switching strategies on single-bar noise spikes without a hysteresis confirmation window.
- **Ignoring Volatility Spikes:** Running directional trend strategies during market crash regimes.

## Production Implementation Reference

- Reference code: `scripts/regime_detector.py` (`MarketRegimeDetector`, `MarketRegime`, `RegimeAnalysis`).
- Automated unit tests: `scripts/test_regime_detector.py`.
