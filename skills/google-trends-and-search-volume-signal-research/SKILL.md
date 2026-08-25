---
name: google-trends-and-search-volume-signal-research
description: >-
  Alternative data research engine for Google Search Volume Index (SVI) data: standardizes SVI against a strictly trailing baseline window, enforces point-in-time publication lag with an explicit as-of filter, and classifies retail attention spikes against price momentum.
domain: Quantitative Research & Alternative Data
subdomain: Web Search Volume & Retail Attention Signal Research
tags: ["google-trends", "svi", "alt-data", "z-score", "retail-attention", "attention-spikes", "point-in-time"]
brokers_frameworks: ["Google Trends (web UI)", "Google Trends API (alpha)", "pytrends (unofficial)", "Python Standard Library", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in alternative data research and retail-attention signal construction, when you need an auditable, point-in-time-correct abnormal-search-volume feature.

The empirical basis is Da, Engelberg & Gao (2011, *Journal of Finance* 66(5), 1461–1499), who propose Google search frequency as "a new and direct measure of investor attention" and find "strong evidence that SVI captures the attention of individual/retail investors." Preis, Moat & Stanley (2013, *Scientific Reports* 3, 1684) showed the same class of signal applied to macro search terms.

The module standardizes the latest SVI observation against a **strictly trailing** baseline window, enforces an explicit availability lag through an `as_of` filter, and splits spikes by contemporaneous price momentum.

## When NOT to Use

- **As a standalone trading signal.** This is a research feature generator — no return model, no holding period, no position sizing, no calibration. The `lookback_window=30` and `z_score_threshold=2.0` defaults are illustrative; no published study establishes them for this construction.
- **Assuming a spike is directionally bullish.** Da et al. find an ASVI increase predicts "an outperformance of more than 30 basis points … during the subsequent two weeks" but that this "initial positive price pressure is almost completely reversed by the end of the year." Preis et al.'s own strategy is *contrarian*: they **sell** the DJIA when search volume for `debt` rises. The sign of the relationship is keyword-, horizon- and universe-specific and must be estimated, never assumed from this module's `BULLISH_`/`BEARISH_` labels, which describe only the co-movement of attention and price.
- **Without a point-in-time `as_of`.** Calling `generate_trends_signal` with `as_of=None` records the publication lag but does not enforce it. That is acceptable live, where the caller controls arrival; in a backtest it reintroduces exactly the look-ahead this module exists to prevent.
- **On a keyword universe you selected by backtest performance.** Preis et al. screened 98 search terms and reported the best. Challet & Bel Hadj Ayed (2013/2014) find that "random finance-related keywords do not … contain more exploitable predictive information than random keywords related to illnesses, classic cars and arcade games." Keyword selection is the dominant source of bias here.
- **Across SVI series pulled in separate requests or over different date ranges.** Trends rescales 0–100 per request, so two pulls are not on the same scale (see Prerequisites).

## Prerequisites

- An SVI time series per keyword with `timestamp`, `keyword`, `svi_score` and a **measured** `publication_lag_hours`.
- **Timezone-aware timestamps.** Google Trends uses different time bases by window length: for ranges of 30 days or longer "the data shown in the graph uses Coordinated Universal Time (UTC)", while for ranges of 7 days or less it "uses your own local time zone as set in your browser or device". The engine rejects timezone-naive timestamps for this reason.
- A price momentum series for the same instrument, aligned to the same clock.
- Understanding of the scale you are on. The Trends UI and pytrends return a per-request 0–100 index; the official Trends API (alpha) returns "consistently scaled data" that is explicitly "not scaled from 0 to 100". Set `svi_scale_max=None` for the latter.
- Python 3.8+ standard library only. No third-party dependency is required — and note that **pytrends is not an official Google API** ("This is not an official or supported API"), was last released in April 2023, and is openly seeking maintainers.

## Workflow

1. **Keyword Selection**: Prefer unambiguous identifiers. Da et al. deliberately use ticker symbols rather than company names, because "investors may search the company name for reasons unrelated to investing … This problem is more severe if the company name has multiple meanings (e.g., 'Apple' or 'Amazon')". Note the counter-trap: short tickers are themselves ambiguous (`GPS`, `CAR`, `ALL`), so screen both directions and pin the Trends category where possible.
2. **Point-in-Time Availability Filtering**: Pass `as_of`. Every observation whose `timestamp + publication_lag_hours` falls after `as_of` is dropped *before any statistic is computed*. Set `publication_lag_hours` from your own pipeline's measured lag — Google does not publish a fixed guarantee, so the built-in `24.0` is a placeholder, not a fact.
3. **Trailing-Baseline Standardization**: Compute the mean $\mu_N$ and sample standard deviation $\sigma_N$ over the $N$ observations **strictly preceding** the observation under test, then $Z_t = \frac{\text{SVI}_t - \mu_N}{\sigma_N}$. A series of $N$ points is not enough; the engine requires $N + 1$. Preis et al. define the baseline as $N(t-1, \Delta t) = (n(t-1) + n(t-2) + \dots + n(t-\Delta t))/\Delta t$ and Da et al. as the median over "the previous eight weeks" — both exclude the tested period.
4. **Degenerate-Baseline Gate**: If $\sigma_N \le$ `min_baseline_std` (default $0.0$, i.e. a perfectly flat baseline), emit `INSUFFICIENT_DATA`. Do not substitute a floor value for $\sigma_N$ — a flat baseline is the normal appearance of a low-volume keyword, which "Trends only shows data for popular terms, so search terms with low volume appear as '0'".
5. **Signal Classification** (precedence, first match wins):
   - `INSUFFICIENT_DATA` — fewer than $N+1$ observable points, or a degenerate baseline.
   - `BULLISH_ATTENTION_SURGE` — $Z_t \ge$ threshold **and** momentum $> 0$.
   - `BEARISH_PANIC_SPIKE` — $Z_t \ge$ threshold **and** momentum $< 0$.
   - `NEUTRAL_ATTENTION` — no spike, **or** a spike with momentum exactly $0$. In that last case `is_attention_spike` stays `True` and the notes say `UNDIRECTED SPIKE`; read the flag, not just the label.
6. **Threshold Calibration**: Re-estimate $N$ and $Z_{\text{threshold}}$ out-of-sample per keyword universe before any live use.
7. **Audit Report Generation**: Output `GoogleTrendsSignalReport`, which carries `as_of_timestamp`, `observation_timestamp`, `observable_at` and `dropped_unobservable_points` so the point-in-time decision is reconstructible.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Standardizing an Observation Against a Window That Contains It**: Including the tested point in its own $\mu$ and $\sigma$ inflates $\sigma$ and pulls $\mu$ toward the point, and the distortion is largest exactly when a spike occurs. On this skill's own 30-period fixture (baseline mean $40.0$, $\sigma = 2.03$), a spike to $90.0$ scores $Z = +24.6$ against a trailing baseline but only $Z = +5.2$ against a window that swallows it. Both the published constructions exclude the tested period.
- **Flooring $\sigma$ to Avoid a Zero Denominator**: A floor such as `sigma = max(1.0, sigma)` silently rescales every keyword whose baseline is quieter than the floor. With $\sigma = 0.20$, an SVI move from $40.0$ to $40.5$ is a genuine $Z = +2.46$ spike; the floor reports $Z = +0.48$ and the spike disappears. It also writes a false $\sigma$ into the audit record. Gate on a degenerate baseline instead of fabricating a denominator.
- **Documented-But-Unenforced Availability Lag**: Carrying a `publication_lag_hours` field while feeding the whole series into the statistic is worse than having no lag field — the backtest looks point-in-time correct and is not. Enforce the lag by filtering on `as_of`.
- **Assuming a Fixed Google Publication Lag**: Google's current Trends FAQ does not publish a guaranteed availability lag. Measure your own vendor's or scraper's lag and set it per point; do not hard-code someone else's number.
- **Keyword Ambiguity**: Tracking "Apple" (fruit vs AAPL) or "Amazon" (river vs AMZN) measures shopping and homework, not investor attention — Da et al.'s stated reason for using tickers.
- **Comparing 0–100 SVI Across Requests or Date Ranges**: Trends divides "each data point … by the total searches of the geography and time range it represents" then scales "on a range of 0 to 100". Change the window, the geography, or the comparison terms and the whole series is re-based. Never splice two pulls into one history without re-normalizing on an overlap.
- **Treating Trends Output as Deterministic**: "only a sample of Google searches are used in Google Trends", so the same query re-run returns slightly different numbers. Preis et al. handled this by averaging "over three realizations of its search volume time series, based on three independent data requests". A backtest that cannot be reproduced from a re-pull is not a bug in your code.
- **Mixing Time Bases**: A 7-day pull is stamped in the requester's browser timezone and a 30-day+ pull in UTC. Joining those two to a market calendar without normalizing shifts observations across session boundaries.
- **Contaminated Baselines**: A mean/σ baseline is itself moved by prior spikes, so a second spike in a noisy window scores lower than the first. Da et al. use a **median** baseline for exactly this robustness reason; consider a median/MAD baseline for spike-prone keywords.
- **Keyword Selection Bias**: Screening many keywords and keeping the best one produces an in-sample winner, not a signal. Apply a multiple-testing correction and hold out keywords, not just dates.

## Verification

- Instantiate `GoogleTrendsSignalEngine(lookback_window=30, z_score_threshold=2.0)`. Feed a 30-period baseline of fifteen $38.0$ and fifteen $42.0$ values — mean exactly $40.0$, sample $\sigma = \sqrt{120/29} \approx 2.0344$ — followed by an observation of $90.0$. The engine must report `rolling_mean_svi == 40.0`, `rolling_std_dev_svi ≈ 2.0344` and $Z \approx +24.58$; with momentum $+5.2\%$ it emits `BULLISH_ATTENTION_SURGE`, with $-8.5\%$ it emits `BEARISH_PANIC_SPIKE`.
- Confirm the trailing baseline: those statistics must be unchanged by the value of the final observation.
- Confirm the σ-floor regression: a baseline of fifteen $39.8$ and fifteen $40.2$ values ($\sigma \approx 0.2034$) with an observation of $40.5$ must yield $Z \approx +2.46$ and a spike — not the $Z \approx +0.48$ a floored σ produces.
- Confirm point-in-time enforcement: with a 24-hour lag, an `as_of` one second before the lag elapses returns `INSUFFICIENT_DATA` with `dropped_unobservable_points == 1`; at exactly the lag boundary the same call returns the surge.
- Confirm a perfectly flat baseline returns `INSUFFICIENT_DATA` with `svi_z_score` NaN, not a trade.
- Confirm a timezone-naive timestamp and a duplicate timestamp each raise `ValueError`.
- Run `python -m unittest discover -s skills/google-trends-and-search-volume-signal-research/scripts`.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `social-media-sentiment-signal-with-bot-filtering`
- `lookahead-bias-elimination`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `alternative-data-vendor-due-diligence-checklist`
