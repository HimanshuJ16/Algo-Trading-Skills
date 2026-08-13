# Standards — backtest-outlier-and-bad-tick-filtering

## Detection Rule

The modified Z-score of Iglewicz and Hoaglin, as documented in the NIST/SEMATECH e-Handbook
of Statistical Methods §1.3.5.17 *Detection of Outliers*:

$$M_i = \frac{0.6745\,(x_i - \tilde{x})}{\text{MAD}}$$

- $\tilde{x}$ is the window median and MAD the median of absolute deviations from it.
- $0.6745$ is the 0.75 quantile of the standard normal, making MAD a consistent estimator of
  $\sigma$ under normality. It is fixed by the definition and is not a tunable.
- NIST states that Iglewicz and Hoaglin **recommend labelling $|M_i| > 3.5$ as potential
  outliers**. This skill defaults to $5.0$, deliberately looser, because intraday returns are
  fat-tailed and 3.5 deletes genuine volatility. The looser value is a choice, not a standard.

Implemented in price units with an additive floor, following the outlier rule of Brownlees and
Gallo (2006), who flag $p_i$ when $|p_i - \bar{p}_i(k)| \ge 3 s_i(k) + \gamma$:

$$|P_i - \tilde{x}| > \frac{Z_{\max}\cdot\text{MAD}}{0.6745} + \gamma$$

$\gamma$ (`min_deviation`) is that paper's minimum-price-variation term. Set it to the
instrument's tick size.

## Configuration Defaults — Not Recommended Limits

| Parameter | Default | Status |
|---|---|---|
| `z_threshold` | 5.0 | Implementation default. NIST/Iglewicz–Hoaglin recommend 3.5 for generic data. |
| `max_single_tick_jump_pct` | 20.0 | Implementation default with **no published basis**. See calibration below. |
| `window_size` | 21 | Implementation default. Must be $\ge 3$ for a median and MAD to carry scale. |
| `max_consecutive_drops` | 3 | Implementation default. Must be $\ge 2$; at 1 every outlier is instantly accepted. |
| `min_deviation` ($\gamma$) | 0.0 | Off by default. At 0 the MAD test is skipped whenever MAD is 0. |
| Non-positive price | $P \le 0$ | Always rejected. |
| Non-finite price | NaN, $\pm\infty$ | Always rejected. |

## Calibrating the Jump Threshold

The 20% default is the loosest tier in any published US equity guideline. Two authoritative
reference points, both US-specific:

**FINRA Rule 11893 — Clearly Erroneous Transactions in OTC Equity Securities.** A transaction
may be found clearly erroneous only if its price is away from the Reference Price (generally
"the prevailing market price just prior to the time of the trade") by at least:

| Reference Price | % Difference |
|---|---|
| $0.9999 and under | 20% |
| $1.0000 – $4.9999 | Low end of range minimum 20% – high end minimum 10% |
| $5.0000 – $74.9999 | 10% |
| $75.0000 – $199.9999 | Low end minimum 10% – high end minimum 5% |
| $200.0000 – $499.9999 | 5% |
| $500.0000 – $999.9999 | Low end minimum 5% – high end minimum 3% |
| $1,000.0000 and over | 3% |

**Limit Up-Limit Down (LULD) price bands for NMS stocks**, which bound how far a price may
legitimately move before a limit state:

| Reference Price | Band |
|---|---|
| Above $3.00, Tier 1 | 5% |
| Above $3.00, Tier 2 | 10% |
| $0.75 – $3.00 | 20% |
| Below $0.75 | Lesser of $0.15 or 75% |

Bands are doubled during the last 25 minutes of the regular trading day for all Tier 1
securities and for Tier 2 securities below $3.00.

Implications for calibration:

- A flat 20% jump threshold on a $250 Tier 1 stock is roughly four times the LULD band. A 15%
  erroneous print — one the venue itself would not have allowed to trade freely — passes.
- Setting the threshold *below* the applicable LULD band deletes genuine limit-state moves.
  The usable range sits between the venue band and the erroneous-trade guideline.
- Both tables are **US equity specific**. LULD applies to NMS stocks; FINRA 11893 applies to
  OTC Equity Securities, and individual exchanges publish their own equivalents. Neither is a
  requirement on a backtest data filter — they are calibration reference points. Do not apply
  either to futures, FX, or non-US venues.

## Design Deviation From the Canonical Method

Brownlees and Gallo evaluate each observation against the $k$ records **closest in time**, a
centred and therefore non-causal neighbourhood. This implementation uses a strictly **trailing**
window of already-accepted prices. Consequences, all of which must be managed by the caller:

- The first `window_size` accepted ticks receive no MAD screening, so an erroneous opening
  print can survive and anchor the jump test. Validate the warm-up externally or discard it.
  The same blind spot reopens for `window_size` ticks after every confirmed level shift, since
  the window restarts there; during it only the jump rule is active.
- The first tick after a genuine level shift always looks like an outlier. `max_consecutive_drops`
  exists to recover from this.
- Deviation is measured from a rolling *level*, not from an expected return, so a strong trend
  produces a low but non-zero false-positive rate. Measure it on a known-clean segment.

`restore_ticks_on_regime_change=True` (default) puts back the prints purged on the way into a
confirmed level shift, which makes those decisions depend on up to `max_consecutive_drops - 1`
later ticks. This is a batch data-preparation choice, not signal look-ahead: no price is moved
in time and no future price becomes visible at an earlier timestamp. Set it False when the same
filter must run on a live feed and produce an identical series.

## Audit Invariants

- `cleaned_ticks_count + purged_bad_ticks_count == total_input_ticks`.
- `kept_indices` and `purged_indices` partition `range(total_input_ticks)`; both are ascending.
- `cleaned[j] == prices[kept_indices[j]]` — the only safe way to realign timestamps.
- No NaN or infinite value ever appears in the cleaned series.
- A wrong *type* in the price array raises `OutlierFilterError`; a bad *value* is purged.

## Sources

- NIST/SEMATECH e-Handbook of Statistical Methods, §1.3.5.17 *Detection of Outliers* —
  <https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm>
- Iglewicz, B. and Hoaglin, D. (1993), *How to Detect and Handle Outliers*, ASQC Basic
  References in Quality Control, Vol. 16 (the source NIST attributes the rule and the 3.5
  threshold to).
- Brownlees, C. T. and Gallo, G. M. (2006), "Financial econometric analysis at ultra-high
  frequency: Data handling concerns", *Computational Statistics & Data Analysis* 51(4),
  2232–2245 — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=886204>
- FINRA Rule 11893, *Clearly Erroneous Transactions in OTC Equity Securities* —
  <https://www.finra.org/rules-guidance/rulebooks/finra-rules/11893>
- Limit Up-Limit Down plan, current price band table — <https://www.luldplan.com/>

## Category

`backtesting-methodology`
