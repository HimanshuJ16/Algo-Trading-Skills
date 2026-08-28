# Standards — regime-detection-for-strategy-switching

## Indicator definitions (verified against the primary source)

Source: J. Welles Wilder Jr., **New Concepts in Technical Trading Systems**, Trend
Research, 1978 — the original publication of ATR, +DI/-DI, DX and ADX. The
step-by-step arithmetic below is reproduced from StockCharts ChartSchool,
["Average Directional Index (ADX)"](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx),
which documents Wilder's calculation directly.

| Quantity | Definition |
|---|---|
| True Range | $TR_i = \max(H_i - L_i,\ \lvert H_i - C_{i-1} \rvert,\ \lvert L_i - C_{i-1} \rvert)$ |
| Plus Directional Movement | $+DM_i = H_i - H_{i-1}$ when that exceeds $L_{i-1} - L_i$ and is positive, else $0$ |
| Minus Directional Movement | $-DM_i = L_{i-1} - L_i$ when that exceeds $H_i - H_{i-1}$ and is positive, else $0$ |
| Wilder smoothing (accumulation form) | $S_p = \sum_{k=1}^{p} x_k$; $S_t = S_{t-1} - S_{t-1}/p + x_t$ |
| Average True Range | $ATR_t = S_t^{TR} / p$ |
| Directional Indicators | $+DI = 100 \cdot S^{+DM}/S^{TR}$; $-DI = 100 \cdot S^{-DM}/S^{TR}$ |
| Directional Movement Index | $DX = 100 \cdot \dfrac{\lvert +DI - {-DI} \rvert}{+DI + {-DI}}$ |
| Average Directional Index | First: mean of the first $p$ $DX$ values. Then: $ADX_t = \dfrac{ADX_{t-1}(p-1) + DX_t}{p}$ |

Three consequences of these definitions are load-bearing for this engine:

1. **ADX is the smoothed average of DX.** DX is a single-bar reading that reaches
   100 whenever either DI is zero, which happens on any quiet one-directional
   drift. Wilder's interpretation thresholds are defined on ADX; applying them to
   DX classifies ordinary ranges as trends.
2. **Bar 0 contributes nothing.** $TR$, $+DM$ and $-DM$ all need a prior bar, so
   the series start at bar 1. A first ADX therefore needs $2p$ bars: $p$ true-range
   values to seed the smoothing, then $p$ DX values to seed the ADX average.
3. **Wilder smoothing has infinite memory.** The seed decays as $((p-1)/p)^k$, so
   early ADX values retain a visible seed dependence. StockCharts notes that
   "because of Wilder's smoothing techniques, it can take around 150 periods of
   data to get true ADX values." Treat readings between $2p$ and ~150 bars as real
   but not yet comparable against a fixed threshold.

## Implementation verification

The shipped `_compute_adx` / `_compute_atr_series` were cross-checked against
**TA-Lib 0.6.8** (`talib.ADX`, `PLUS_DI`, `MINUS_DI`, `ATR`, period 14) on
pseudo-random OHLC series:

| History length | ADX relative difference | +DI relative difference |
|---|---|---|
| 60 bars | $4.1 \times 10^{-5}$ | $5.2 \times 10^{-4}$ |
| 120 bars | $2.1 \times 10^{-4}$ | $2.9 \times 10^{-6}$ |
| 300 bars | $1.1 \times 10^{-9}$ | $4.9 \times 10^{-12}$ |
| 600 bars | $3.9 \times 10^{-16}$ | $1.7 \times 10^{-16}$ |

ATR agrees bit-for-bit at every length. The residual on short histories is a
different seeding convention, and it decays geometrically exactly as Wilder's
smoothing predicts — which is the same reason the ~150-bar note above matters.
TA-Lib is a verification aid only; the shipped tests import nothing outside the
Python standard library, and the repository adds no dependency.

## Configuration defaults (calibrate before use)

| Parameter | Default | Provenance | What it actually does |
|---|---|---|---|
| `adx_trend_threshold` | $25.0$ | Wilder's published strong-trend level | ADX at or above which a directional regime may be **entered**. |
| `adx_ranging_threshold` | $20.0$ | Wilder's published no-trend level | ADX below which an in-force trend regime is **exited**. The 20–25 band is Wilder's grey zone: an already-confirmed trend is held there while the DI pair still agrees with it; a range never enters a trend from inside it. |
| `volatility_z_threshold` | $2.0$ | **This engine's choice** | ATR z-score at or above which the regime is `HIGH_VOLATILITY_CRASH`. Not a published or mandated level. |
| `hysteresis_bars` | $3$ | **This engine's choice** | Consecutive confirming bars before the confirmed regime switches. Must be $\ge 1$; $1$ means no filter. |
| `indicator_period` | $14$ | Wilder's period | Wilder period for ATR, DI and ADX. |
| `MIN_VOLATILITY_HISTORY` | $10$ | **This engine's choice** | Prior ATR observations required before a z-score is computed. |
| `MAX_VOLATILITY_ZSCORE` | $1000.0$ | **This engine's choice** | Finite stand-in reported when the ATR history has zero dispersion. |

**No regulator, exchange or standards body mandates any of these values.** Wilder's
25/20 levels are an author's interpretation guidance derived from 1970s daily
commodity charts; the hysteresis and z-score defaults are this library's. Calibrate
each against the instrument, the bar frequency and your own drawdown tolerance, and
record the rationale.

## Volatility z-score

$$z = \frac{ATR_{\text{latest}} - \operatorname{mean}(ATR_{\text{history}})}{\operatorname{stdev}(ATR_{\text{history}})}$$

with the sample standard deviation ($n-1$ divisor) and the scored observation
**excluded** from both moments. Self-inclusion bounds $z$ at $(n-1)/\sqrt{n}$ — about
$3.6$ with the 15 ATR readings available at the 28-bar minimum — so the score would
stop responding to the size of the shock, which is the opposite of what a shock
detector needs. A zero-dispersion history leaves $z$ undefined rather than zero; the
engine reports $\pm$`MAX_VOLATILITY_ZSCORE` in the direction of the move.

## Known limitations

- **The volatility leg is direction-agnostic.** `HIGH_VOLATILITY_CRASH` fires on a
  violent rally as readily as on a sell-off. It means "the volatility regime broke".
- **The volatility leg is smoothed.** A single violent bar moves a Wilder ATR by only
  about $1/p$ of its excess range. This detects a volatility *regime*, not a spike.
  Score raw True Range if single-bar shock detection is the requirement.
- **The engine lags by construction.** `hysteresis_bars` delays every switch,
  including the switch into risk-off. It is a router, not a circuit breaker; pair it
  with `kill-switch-and-drawdown-circuit-breakers`.
- **State is per-instrument.** The confirmed regime and pending candidate count live
  on the instance. One detector per instrument, per timeframe.
- **No look-ahead is possible from the inputs, but is trivial to introduce at the
  call site.** Every reading treats the last supplied bar as closed.

## Category

`financial-ml`
