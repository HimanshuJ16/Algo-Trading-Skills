# Workflows for Google Trends Signal Research

## 1. Keyword Construction and Screening

- Choose the query string deliberately. Da et al. (2011) use ticker symbols rather
  than company names because a company name is searched for non-investing reasons
  and is worse still when the name has multiple meanings ("Apple", "Amazon").
- Run the reverse check: a short ticker can be a common word or another entity
  (`GPS`, `CAR`, `ALL`, `KEY`). Where the Trends UI offers a disambiguated *topic*
  rather than a raw search term, prefer the topic and record which you used.
- Fix the geography and comparison set once, and keep them constant for the life of
  the series. Changing either re-bases the whole 0–100 index.
- Hold keywords out. Selecting keywords by backtest performance is the dominant
  bias in this literature (Challet & Bel Hadj Ayed, 2013).

## 2. Ingestion and Point-in-Time Stamping

- Record two distinct timestamps per observation: the **bucket timestamp** (the
  period the SVI value describes) and the **retrieval timestamp** (when your
  pipeline actually obtained it). `publication_lag_hours` is the measured gap.
- Stamp bucket timestamps with an explicit UTC offset. Windows of 30 days or more
  are UTC; windows of 7 days or less are in the requester's local timezone. Convert
  once, at ingestion, and never store a naive timestamp.
- Because Trends samples, pull each series more than once if reproducibility
  matters, and average the realizations (Preis et al. averaged three).
- Persist raw pulls immutably. A re-pull will not reproduce them.

## 3. Availability Filtering

- For every evaluation instant `as_of`, keep only observations satisfying
  `bucket_timestamp + publication_lag_hours <= as_of`.
- Do this before computing any statistic, not after. Filtering the signal instead
  of the input leaves the baseline contaminated by unpublished data.
- Record how many observations were dropped. A backtest whose drop count is always
  zero is not enforcing anything.

## 4. Trailing-Baseline Standardization

- Split the window into the baseline (the $N$ observations strictly preceding the
  observation) and the observation itself.
- Compute $\mu_N$ and the sample standard deviation $\sigma_N$ (ddof = 1) over the
  baseline only, then $Z_t = (\text{SVI}_t - \mu_N)/\sigma_N$.
- If $\sigma_N \le$ `min_baseline_std`, stop and emit `INSUFFICIENT_DATA`. Do not
  substitute a floor for $\sigma_N$.
- Consider a median/MAD baseline for spike-prone keywords: a mean/σ baseline is
  itself moved by earlier spikes, which is why Da et al. use a median baseline.

## 5. Signal Classification

Precedence, first match wins:

1. `INSUFFICIENT_DATA` — fewer than $N+1$ observable points, or a degenerate baseline.
2. `BULLISH_ATTENTION_SURGE` — $Z_t \ge$ threshold and momentum $> 0$.
3. `BEARISH_PANIC_SPIKE` — $Z_t \ge$ threshold and momentum $< 0$.
4. `NEUTRAL_ATTENTION` — no spike, or a spike with momentum exactly $0$
   (`is_attention_spike` remains `True`; the notes read `UNDIRECTED SPIKE`).

The labels describe how attention and price moved together in the observation
window. They are not return forecasts, and the direction of any forecast must be
estimated separately — Da et al. find short-horizon drift that reverses within the
year, and Preis et al. trade the relationship contrarian.

## 6. Signal Audit Generation

Emit `GoogleTrendsSignalReport` and retain, per evaluation:

- `as_of_timestamp`, `observation_timestamp`, `observable_at` — the point-in-time
  decision, reconstructible after the fact.
- `dropped_unobservable_points` — proof the lag filter ran.
- `rolling_mean_svi`, `rolling_std_dev_svi`, `baseline_periods` — the baseline as
  observed, never floored or substituted.
- `svi_z_score`, `is_attention_spike`, `signal_type`, `audit_notes`.

## 7. Calibration Before Live Use

- Re-estimate `lookback_window` and `z_score_threshold` out-of-sample, per keyword
  universe. Do not inherit the defaults.
- Estimate the sign and horizon of the relationship on held-out keywords and dates.
- Report the multiple-testing burden of the keyword screen alongside any result.
