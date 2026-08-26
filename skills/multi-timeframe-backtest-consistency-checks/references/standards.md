# Standards — multi-timeframe-backtest-consistency-checks

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator, exchange, or
standards body publishes a mandatory timeframe-consistency threshold. What follows is a
rationale for each default and the conditions under which changing it is defensible.

| Parameter | Default | Rationale |
|---|---|---|
| `divergence_tolerance_pct` | $10^{-6}\%$ | Bar aggregation is exact arithmetic, not estimation, so a correctly resampled series reproduces its reference to floating-point noise. $10^{-6}\%$ is $10^{-8}$ relative: roughly six orders of magnitude above double-precision noise, and five orders below the ~$0.1\%$ divergence a genuine one-bar boundary error produces on a price near $100$. |
| `abs_tolerance` (integrity check) | $10^{-9}$ | Absolute tolerance for comparing aggregated OHLCV fields. Sums use `math.fsum`, so accumulated error stays far below this even over long buckets. |
| `min_comparisons` | $1$ | Minimum overlapping points before a verdict is returned. Below it the check raises. Raise this in CI to assert a floor on coverage. |
| `anchor` | `ANCHOR_EPOCH` | Matches pandas' default grid for UTC timestamps. **Verify against your venue's session start** — see the anchor arithmetic below. |
| `drop_incomplete_final` | `True` | A trailing bucket holding fewer than `factor` source bars may be a period still forming. Presenting one as a finished bar leaks information from an incomplete period. |

**When loosening `divergence_tolerance_pct` is defensible:** the reference series is known
to be constructed differently — different tick filtering, different trade-condition
exclusions, different session boundaries, or a vendor that reports volume in lots rather
than shares. Record which of these applies and the measured divergence it accounts for.
Widening the tolerance to make a failing check pass, without identifying the mechanism, is
how the version 1.0.0 defect below survived.

## Resampling conventions (verified against the primary source)

Source: **pandas `DataFrame.resample` API reference**
([pandas.pydata.org](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.resample.html)).

| Fact | Value |
|---|---|
| `closed` default for tick frequencies (minutes, hours, days) | `'left'` — the bucket covers $[\text{start}, \text{end})$ |
| `label` default for tick frequencies | `'left'` — the bucket is labelled with its opening edge |
| `closed`/`label` default for `'ME'`, `'YE'`, `'QE'`, `'BME'`, `'BA'`, `'BQE'`, `'W'` | `'right'` |
| `origin` default | `'start_day'` — the first day at midnight of the series |
| `origin='epoch'` | origin is 1970-01-01 |
| `origin='start'` | origin is the first value of the timeseries |
| `origin` applicability | "Only takes effect for Tick-frequencies (i.e. fixed frequencies like days, hours, and minutes, rather than months or quarters)." |

This module follows the left-labelled, left-closed tick-frequency convention. `ANCHOR_EPOCH`
corresponds to pandas `origin='epoch'`, which coincides with the default `'start_day'`
grid **when timestamps are UTC**, because the epoch is itself a UTC midnight and a day is a
whole number of seconds. `ANCHOR_SESSION` corresponds to `origin='start'`.

## Anchor arithmetic — worked example

A session start lies on the epoch grid only if its offset from UTC midnight divides evenly
by the bucket width. NSE's capital market segment opens at **09:15 IST**
([NSE market timings](https://www.nseindia.com/static/market-data/market-timings)), which is
03:45 UTC, i.e. $13500$ s after UTC midnight:

| Bucket | $13500 / \text{width}$ | Aligned? | Consequence under `ANCHOR_EPOCH` |
|---|---|---|---|
| 5-min ($300$ s) | $45$ | Yes | Buckets open at 09:15, 09:20, … |
| 15-min ($900$ s) | $15$ | Yes | Buckets open at 09:15, 09:30, … |
| 30-min ($1800$ s) | $7.5$ | **No** | First bucket opens 09:00 IST and holds only the 15 bars from 09:15 |
| 60-min ($3600$ s) | $3.75$ | **No** | First bucket opens 09:00 IST and holds 45 of 60 bars |

Run this division for your venue and interval before accepting the default anchor. A venue
opening on the hour (e.g. 09:30 ET, $1800$ s past the half-hour in UTC terms) aligns for
every bucket width that divides 30 minutes.

## Defect record — version 1.0.0

Version 1.0.0's `check_consistency` compared `SMA_P` on resampled bars against
`SMA_{P \times factor}` on the high-resolution bars, at matching timestamps. Two independent
errors made that comparison unfit for purpose. Both are now covered by regression tests in
`scripts/test_timeframe_consistency.py`.

**1. Estimator mismatch (irreducible).** $\text{SMA}_P$ of resampled closes averages $P$
closes sampled every `factor` bars; $\text{SMA}_{P \times factor}$ of high-resolution closes
averages all $P \times factor$ closes. These are different statistics of the same series and
coincide only when the series is flat. On closes $100 + 0.1i$ with $factor=5$, $P=3$ they
differ by a constant $0.2$ even after perfect timestamp alignment.

**2. Timestamp misalignment.** Resampled bars were left-labelled at the bucket's *opening*
time while carrying the *last* source bar's close, so the high-resolution trailing window at
that label ended $factor - 1$ bars early — adding a further $0.4$ to the same example.

Because divergence was reported as a percentage of price, the resulting verdict tracked the
price level rather than the data:

| Series | Resampling | Divergence reported | Verdict at 1.0% threshold |
|---|---|---|---|
| closes $100 + 0.1i$ | correct | $0.60\%$ | PASS |
| closes $10 + 0.1i$ (same shape) | correct | $5.56\%$ | FAIL |

**3. Threshold too loose to catch the target defect.** The published $\le 1.0\%$ threshold
could not detect a one-bar boundary error, which is the primary failure this skill exists to
catch: an error of $0.1$ in price against a level near $100$ is $\approx 0.099\%$, well
inside the threshold.

**4. Vacuous pass.** Empty input, or history shorter than $P \times factor$, produced zero
comparisons and reported `is_consistent=True` with the message "PASSED".

## Known limitations of the current implementation

- The check is only as good as the **independence of the two provenances**. If the reference
  series was itself produced by the same resampling code, agreement proves nothing.
- A **gap cannot be distinguished from a period still forming**. A trailing short bucket is
  dropped by default and an interior one is counted, but neither is diagnosed.
- **No timezone or session-calendar awareness.** DST transitions, half-days, and holidays
  must be handled upstream.
- `compute_sma` is a **reference indicator for the parity check**, not a general indicator
  library. Extending the check to other indicators means comparing them at the same
  resolution and period on both sides — never scaling the period by the resample factor.

## Category

`backtesting-methodology`
