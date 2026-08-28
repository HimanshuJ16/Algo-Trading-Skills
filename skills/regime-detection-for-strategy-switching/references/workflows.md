# Workflows — regime-detection-for-strategy-switching

One `detect_regime` call per newly **closed** bar, per instrument, per timeframe.

## 0. Gate the input before computing anything

Reject rather than classify:

- `len(highs) != len(lows) != len(closes)` — a truncated series silently misaligns
  every prior-bar reference.
- `len(closes) < min_bars_required` (28 at period 14).
- Any non-finite or non-numeric value. This is the dangerous one: every comparison
  against a NaN is False, so an unvalidated NaN falls straight through the
  classification chain and is reported as `MEAN_REVERTING_RANGING` — a
  normal-looking regime manufactured from corrupt data.
- Any bar with `high < low`.

The last supplied bar is treated as closed. A partially formed bar leaks its
unfinished high, low and close into the regime that routes its own orders.

## 1. Wilder indicator legs

1. Build $TR$, $+DM$, $-DM$ for bars $1 \dots n-1$. Bar 0 has no prior bar.
2. Wilder-smooth each with $S_p = \sum_{k=1}^{p} x_k$, then
   $S_t = S_{t-1} - S_{t-1}/p + x_t$.
3. $ATR_t = S_t^{TR}/p$; $+DI = 100 \cdot S^{+DM}/S^{TR}$;
   $-DI = 100 \cdot S^{-DM}/S^{TR}$.
4. $DX = 100 \cdot \lvert +DI - {-DI} \rvert / (+DI + {-DI})$, guarded: a
   zero-range window carries no directional information, so $DI = DX = 0$ rather
   than a division by zero.
5. **ADX is the average of DX, not DX.** First ADX = mean of the first $p$ DX
   values; then $ADX_t = (ADX_{t-1}(p-1) + DX_t)/p$.

**Never substitute a neutral placeholder when history is short.** A fabricated ADX
is indistinguishable from a measured one once it leaves this function; the caller
routes capital on it either way. Raise instead.

## 2. Volatility z-score

$z = (ATR_{\text{latest}} - \operatorname{mean}(ATR_{\text{hist}})) / \operatorname{stdev}(ATR_{\text{hist}})$,
history **excluding** the scored observation.

- Self-inclusion caps $z$ at $(n-1)/\sqrt{n}$ (~3.6 at the 28-bar minimum), so the
  score would stop rising with the size of the shock.
- Zero-dispersion history: $z$ is undefined, not zero. Report a capped extreme in
  the direction of the move.
- Fewer than `MIN_VOLATILITY_HISTORY` prior observations: raise.

## 3. Raw classification — volatility leg first

```
if z >= volatility_z_threshold:            HIGH_VOLATILITY_CRASH
elif adx >= adx_trend_threshold:
        +DI > -DI                       -> BULL_TRENDING
        -DI > +DI                       -> BEAR_TRENDING
        tied                            -> MEAN_REVERTING_RANGING
elif confirmed regime is a trend
     and adx >= adx_ranging_threshold
     and the DI pair still agrees:         hold the confirmed trend   # grey zone
else:                                      MEAN_REVERTING_RANGING
```

The volatility leg outranks the trend legs because the trend variants are the ones
that size worst into a shock. The 20–25 band is an **exit** band: it holds a trend
that is weakening but not dead, and never lets a range enter one.

## 4. Hysteresis transition filter

| This bar's candidate | Effect |
|---|---|
| equals the confirmed regime | counter cleared to 0 |
| new, different from the pending candidate | pending candidate replaced, counter = 1 |
| equals the pending candidate | counter incremented |
| counter reaches `hysteresis_bars` | confirmed regime switches, counter cleared, `regime_changed` true for this bar |

The counter advances **per call, not per bar**. Pass `bar_key` — the bar's close
timestamp or a monotonic index — so a retry, a replayed bar or a duplicated tick
returns the cached analysis instead of manufacturing a confirmation. Without it,
three calls on one bar of evidence complete a three-bar confirmation.

## 5. Strategy variant routing

- Route on `confirmed_regime` only. `raw_candidate_regime` leads it by up to
  `hysteresis_bars` bars and exists for diagnostics and dashboards.
- `route_strategy_variant` returns a **label**. The caller owns label → strategy;
  the engine never imports or instantiates a strategy module.
- Switch on `regime_changed`, not on the regime merely *reading* as something.
  Unwinding and re-arming every bar is the transaction-cost form of the whipsaw
  this filter exists to prevent.

## 6. Operational notes

- **One detector per instrument per timeframe.** The instance carries the confirmed
  regime and the pending candidate count.
- **Persist the confirmed regime across restarts** and pass it as `initial_regime`.
  A fresh instance assumes `MEAN_REVERTING_RANGING` and spends `hysteresis_bars`
  bars in the wrong variant re-confirming what was already in force.
- **Log every confirmed shift** with the ADX, DI pair and z-score that caused it.
  The engine emits this at INFO; keep it, it is the audit trail for why capital
  moved between variants.
- **Warm-up window.** Between $2p$ bars and roughly 150, ADX is real but still
  carries Wilder's seed. Either wait, or widen the thresholds for that window
  deliberately — do not compare a warm-up ADX to a calibrated threshold.
- **Pass a bounded trailing window, not the whole history.** Each call is $O(n)$ in
  the bars supplied, so re-passing a growing multi-year series turns a session into
  $O(n^2)$ work. A fixed trailing window of ~300 bars or more is enough: past the
  ~150-bar mark Wilder's seed has decayed far enough that the reading no longer
  depends materially on where the window starts. A window shorter than that
  re-seeds visibly on every call and the ADX will drift with the window edge.
