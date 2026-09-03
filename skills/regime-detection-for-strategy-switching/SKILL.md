---
name: regime-detection-for-strategy-switching
description: Use when routing live capital between strategy variants (trend-following,
  mean-reversion, risk-off) by classifying each closed bar into a market regime with
  Wilder ADX/DMI and an ATR volatility z-score, then requiring N consecutive confirming
  bars before the active variant is allowed to change.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- regime-detection
- adx
- atr
- volatility-zscore
- hysteresis
- strategy-switching
- trend-detection
brokers_frameworks:
- Wilder ADX/DMI (1978)
- Wilder ATR (1978)
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when several strategy variants are live and only one should be armed at a time — a trend-follower that works in a directional market, a mean-reversion model that works in a range, a risk-off variant for a volatility break. Running the wrong one is a common route to an unplanned drawdown, and switching on every bar that disagrees is a second one. The engine classifies each **closed** bar with Wilder's ADX/DMI and an ATR volatility z-score, then requires `hysteresis_bars` consecutive confirming bars before the confirmed regime — and the routed variant — is allowed to change.

## When NOT to Use

- **As a risk control or kill switch.** The hysteresis filter that suppresses whipsaws also *delays* the risk-off regime by `hysteresis_bars`. On a gap the shock is realized before the third confirming bar closes. Capital protection needs an independent, un-hysteresed circuit breaker — see `kill-switch-and-drawdown-circuit-breakers`.
- **On the currently-forming bar.** Every reading treats the last supplied bar as closed. Feeding a partial bar leaks its unfinished high, low and close into the regime that routes that same bar's orders.
- **On fewer than `min_bars_required` bars, or on a freshly listed instrument.** One ADX value needs `2 × period` bars, and Wilder's smoothing carries a visible seed dependence for roughly 150 bars after that. Below the minimum the engine raises rather than returning a placeholder; between the minimum and ~150 bars the ADX is real but not yet comparable to a fixed threshold. See `cold-start-handling-for-newly-listed-instruments`.
- **For retrospective regime labelling of a backtest.** This engine deliberately lags. To audit whether a backtest covered enough distinct regimes, use `multi-year-regime-coverage-requirement`, which labels bars in hindsight.
- **To blend models rather than select one.** If the intent is to weight several signals simultaneously, use `ensemble-signal-combination-without-overfitting` or `multi-model-ensemble-weight-decay`.
- **With defaults, on an instrument you have not calibrated.** Wilder's ADX 25/20 levels come from 1970s daily commodity charts. On 1-minute crypto bars or an illiquid small cap they are a starting point, not a setting.

## Prerequisites

- OHLC bars of one consistent frequency, oldest first, **closed only**, at least `min_bars_required` long (28 at the default period 14; ~150 for a stable ADX).
- Equal-length `highs`, `lows`, `closes` with finite values and `high >= low` on every bar. The engine validates this and raises rather than classifying corrupt data.
- A bar identity (`bar_key`) — the bar's close timestamp or a monotonic bar index — if the caller can ever retry, replay or double-deliver a bar.
- Calibrated configuration: `adx_trend_threshold` (default 25.0, trend **entry**), `adx_ranging_threshold` (default 20.0, trend **exit**), `volatility_z_threshold` (default 2.0), `hysteresis_bars` (default 3), `indicator_period` (default 14).

## Workflow

1. **Compute the Wilder indicator legs from closed bars.**
   - True Range, +DM and -DM start at bar 1; bar 0 has no prior bar and contributes nothing.
   - Wilder-smooth each series with the accumulation form $S_t = S_{t-1} - S_{t-1}/p + x_t$, seeded by the sum of the first $p$ values. ATR is $S_t^{TR}/p$.
   - $+DI = 100 \cdot S^{+DM}/S^{TR}$, $-DI = 100 \cdot S^{-DM}/S^{TR}$, $DX = 100 \cdot |{+DI} - {-DI}| / ({+DI} + {-DI})$.
   - **Decision point — ADX is the smoothed average of DX, not DX.** The first ADX is the mean of the first $p$ DX values; each subsequent one is $((\text{prior} \cdot (p-1)) + DX_t)/p$. DX alone hits 100 on any quiet one-directional drift, so comparing DX to Wilder's ADX ≥ 25 level labels ordinary ranges as trends.
   - **Decision point — do not substitute a placeholder when history is short.** A fabricated ADX is indistinguishable downstream from a measured one. Raise.

2. **Score the volatility leg.**
   - $z = (ATR_{\text{latest}} - \text{mean}(ATR_{\text{history}})) / \text{stdev}(ATR_{\text{history}})$, where the history **excludes** the observation being scored. Including it caps $z$ at $(n-1)/\sqrt{n}$ — about 3.6 at the 28-bar minimum — so the score stops responding to the size of the shock.
   - **Decision point — a zero-dispersion history is not a zero z-score.** Constant ATR followed by any move makes $z$ undefined, not 0. Report a capped extreme in the direction of the move; never silently return 0.

3. **Classify the raw candidate regime, volatility leg first.**
   - $z \ge$ `volatility_z_threshold` → `HIGH_VOLATILITY_CRASH`, regardless of ADX. The trend variants are the ones that size worst into a shock, so the volatility leg outranks them.
   - $ADX \ge$ `adx_trend_threshold` and $+DI > -DI$ → `BULL_TRENDING`; $-DI > +DI$ → `BEAR_TRENDING`. A tied DI pair is strength without direction — not a tradeable trend.
   - **Decision point — Wilder's 20–25 grey zone is an exit band, not an entry band.** Below the entry threshold, an *already-confirmed* trend is held while $ADX \ge$ `adx_ranging_threshold` **and** the DI pair still points its way. A range never enters a trend from inside the grey zone.
   - Otherwise → `MEAN_REVERTING_RANGING`.

4. **Apply the hysteresis transition filter.**
   - A candidate that matches the confirmed regime clears the counter. A new candidate resets it to 1; a repeat increments it. At `hysteresis_bars` the confirmed regime switches and the counter clears.
   - **Decision point — the filter counts calls, not bars.** Supply `bar_key` so a retry, a replayed bar or a duplicated tick returns the cached analysis instead of counting as another confirming bar and switching the regime early.

5. **Route the confirmed regime.**
   - `route_strategy_variant` returns a **label**, not a module; the caller owns label → strategy. Act on `confirmed_regime` only; `raw_candidate_regime` leads it by up to `hysteresis_bars` bars and is for diagnostics.
   - **Decision point — `regime_changed` is the switch trigger.** Unwinding and re-arming on every bar because `confirmed_regime` merely *reads* as a trend is the transaction-cost version of the whipsaw this filter exists to prevent.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing DX to an ADX threshold.** DX is a single-bar reading that reaches 100 whenever one side of the DI pair is zero. Version 1.0.0 of this engine returned DX under the name `adx`; on a 44-bar range followed by a 7-bar drift it reported 65.7 where the true ADX is 17.2, well inside Wilder's no-trend zone.
- **Trusting an ADX printed during warm-up.** Wilder's smoothing has infinite memory. The first values after the `2 × period` minimum still carry the seed; roughly 150 bars are needed before an ADX reading can be compared against a fixed threshold or across instruments.
- **Letting a NaN reach the classifier.** Every comparison against NaN is False, so an unvalidated NaN falls through the whole classification chain and is reported as `MEAN_REVERTING_RANGING` — a normal-looking regime manufactured from corrupt data. Validate before classifying.
- **Calling `detect_regime` more than once per bar.** The counter advances per call. Three calls on the same history complete a three-bar confirmation from one bar of evidence. Pass `bar_key`.
- **Reading `HIGH_VOLATILITY_CRASH` as a direction.** ATR is direction-agnostic; the regime fires on a violent rally exactly as it does on a sell-off. It means "the volatility regime broke", and the risk-off variant is routed either way.
- **Expecting the z-score to react to one bar.** ATR is Wilder-smoothed, so a single violent bar moves it by about $1/p$ of the excess range. This leg detects a volatility *regime*, not a spike. If single-bar shock detection is the requirement, score raw True Range, not ATR.
- **Setting `hysteresis_bars` to 0 to "make it responsive".** That removes the whipsaw protection entirely and switches on every disagreeing bar. The engine rejects values below 1; 1 is the honest way to say "no filter".
- **Reusing one detector across instruments.** The instance carries the confirmed regime and the pending candidate count. One detector per instrument, per timeframe.
- **Restarting mid-session with the default `initial_regime`.** A fresh instance assumes `MEAN_REVERTING_RANGING` and re-confirms whatever is actually in force from scratch, spending `hysteresis_bars` bars in the wrong variant. Persist the last confirmed regime and pass it to `initial_regime`.
- **Porting Wilder's thresholds across asset classes.** ADX 25/20 comes from 1970s daily commodity charts and is not a regulatory or exchange standard. Calibrate per instrument and timeframe, and record what you used.

## Verification

- **Indicators against analytically known values.** A strictly monotone ramp has $-DM = 0$ on every bar, so $-DI = 0$, $DX = 100$ on every bar and $ADX = 100$ exactly; a monotone decline mirrors it with $+DI = 0$. Bars with a constant range and no gaps give $ATR = R$ exactly. A perfectly flat series returns $(0, 0, 0)$ with no division by zero.
- **ADX is not DX.** On `[100.0, 100.6] * 22` followed by `100.6 + 0.9i` for $i = 1..7$: $DX > 25$ while $ADX < 25$, and the raw classification is `MEAN_REVERTING_RANGING`.
- **Cross-check against a reference library.** ATR matches `talib.ATR` bit-for-bit; ADX and ±DI match `talib.ADX`/`PLUS_DI`/`MINUS_DI` to $<10^{-15}$ relative once Wilder's seed has decayed (600 bars) and to $<10^{-3}$ on short histories, where seeding conventions differ. TA-Lib is a verification aid — the shipped tests import nothing outside the standard library.
- **Hysteresis.** A confirmed regime must lag its candidate by exactly `hysteresis_bars`; one interrupting bar must reset the counter to 0; `regime_changed` must be true on the switching bar only; a repeated `bar_key` must not advance the counter.
- **Negative checks — each must raise.** Mismatched series lengths, fewer than `min_bars_required` bars, a NaN or ±Inf value, a non-numeric value, `high < low`, `hysteresis_bars < 1`, `adx_ranging_threshold > adx_trend_threshold`, an ADX threshold outside 0–100, `indicator_period < 2`, and a `strategy_variants` map missing a regime.
- Run `python -m unittest discover -s skills/regime-detection-for-strategy-switching/scripts` and confirm a 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `multi-year-regime-coverage-requirement`
- `ensemble-signal-combination-without-overfitting`
- `model-staleness-detection`
- `meta-strategy-signal-arbitration`
- `strategy-specific-vs-shared-risk-budget-allocation`
