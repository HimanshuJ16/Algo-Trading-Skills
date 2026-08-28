# Pre-Flight Checklist — regime-detection-for-strategy-switching

## Data

- [ ] Are all bars supplied **closed**? (A forming bar leaks its unfinished high/low/close into the regime that routes its own orders.)
- [ ] Are `highs`, `lows` and `closes` equal-length, oldest first, one consistent frequency?
- [ ] Is `len(closes) >= min_bars_required` (28 at period 14)?
- [ ] Has the instrument traded for ~150 bars, so ADX is past Wilder's seed dependence — or are the thresholds widened deliberately for the warm-up window?
- [ ] Are non-finite values, non-numeric values and `high < low` bars rejected upstream, not classified?

## Configuration

- [ ] Are `adx_trend_threshold` / `adx_ranging_threshold` calibrated for **this** instrument and bar frequency, rather than inherited from Wilder's 1970s daily commodity defaults?
- [ ] Is `adx_ranging_threshold <= adx_trend_threshold` (exit level at or below entry level)?
- [ ] Is `volatility_z_threshold` calibrated? (2.0 is this library's choice, not a published standard.)
- [ ] Is `hysteresis_bars >= 1`, and is the resulting switching delay acceptable for the strategy's holding period?
- [ ] Is a strategy-variant label mapped for all four regimes (bull, bear, range, high-volatility)?

## Call site

- [ ] Is `detect_regime` called exactly once per newly closed bar?
- [ ] Is `bar_key` supplied, so a retry, replay or duplicated tick cannot manufacture a confirmation?
- [ ] Is there one detector instance per instrument, per timeframe?
- [ ] Is the last confirmed regime persisted and passed as `initial_regime` on restart?

## Downstream

- [ ] Does routing act on `confirmed_regime`, never on `raw_candidate_regime`?
- [ ] Does the switch fire on `regime_changed`, rather than re-arming every bar?
- [ ] Is `HIGH_VOLATILITY_CRASH` understood as a direction-agnostic volatility break — it fires on a violent rally too?
- [ ] Is capital protection handled by an **independent** circuit breaker, given that this engine delays every switch (including the one into risk-off) by `hysteresis_bars`?
- [ ] Are confirmed regime shifts logged with the ADX, DI pair and z-score that caused them, for the capital-movement audit trail?
