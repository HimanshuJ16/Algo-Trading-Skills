# Standards — job-posting-data-as-a-growth-signal

## Status of the Numbers in This Module

There is **no industry, exchange, or regulatory standard** for scoring job-posting data.
The 120-day stale threshold, the 0.5 haircut, the 0.25 classification band, the
10-posting floor, and the 0.5 / 0.3 role weights are this module's configurable
defaults. Stating them as `MUST` requirements under an "Engineering Standard" heading
would assert an authority no source supports. Calibrate every one of them on your own
vendor panel and record the calibration.

## Evidence Base

| Claim | Source | What it actually supports |
|---|---|---|
| Job postings carry forward-looking information about firm performance | Gutiérrez, Lourie, Nekrasov & Shevlin, "Are Online Job Postings Informative to Investors?", *Management Science* 66(7):3133–3141 (2020) | Changes in posting counts are positively associated with one-year-ahead growth in headcount, sales and earnings, and investors react positively. **Stronger where postings represent growth rather than replacement hiring** — the empirical basis for weighting role mix at all. |
| Expansion is not the same as outperformance | Belo, Lin & Bazdresch, "Labor Hiring, Investment, and Stock Return Predictability in the Cross Section", *Journal of Political Economy* 122(1) (2014) | Firms with high hiring rates earn **lower** subsequent returns; roughly a 1.5pp lower annual risk premium per 10pp higher hiring rate, after controlling for investment, size, book-to-market and momentum. The measure there is realized employment growth, not postings — but it is why `EXPANSION_BULLISH` must not be read as a return forecast. |
| Long vacancy duration is not automatically a ghost listing | Chen & Li, "Is hiring fast a good sign? The informativeness of job vacancy duration for future firm profitability", *Review of Accounting Studies* 28(3):1316–1353 (2023) | The sign is skill-dependent: **fast fills** predict higher profitability for **low-skill** roles, while **longer duration** predicts higher profitability for **high-skill** roles. A flat duration haircut is therefore miscalibrated for engineering-heavy books — the exact firms the role weighting up-weights. |
| Ghost listings are prevalent enough to need a filter | Greenhouse, *2024 State of Job Hunting Report* (10 December 2024), survey of 2,500 workers across US/UK/Germany plus internal platform data | Greenhouse defines ghost jobs as "positions advertised with no intent to hire" and reports **18–22% of postings on its platform in any given quarter** fall into that category; 60% of candidates suspect they have encountered one. Vendor self-reported data on one platform — it justifies having a stale filter, it does **not** establish any particular threshold. |

No source located during this review prescribes 120 days, or any other duration, as the
boundary between an active requisition and a ghost listing.

## Engine Constraints Actually Enforced

| Constraint | Rule | Enforced |
|---|---|---|
| Posting counts | Finite, real, `>= 0`; `bool` rejected | Raises `JobPostingSignalError` on scoring |
| Role shares | Finite, in `[0.0, 1.0]`, summing to `<= 1.0` | Raises on scoring |
| Average duration | Finite, `>= 0` (NaN rejected, not silently un-penalised) | Raises on scoring |
| Ticker | Non-empty string, so no signal is unattributable in an audit log | Raises on scoring |
| `stale_haircut_factor` | In `[0.0, 1.0]` | Raises on construction |
| `ghost_job_stale_days_threshold`, `min_previous_postings` | Finite, `>= 0` | Raises on construction |
| `classification_threshold` | Finite, in `(0.0, 1.0]` — above the clamp bound nothing could ever classify | Raises on construction |
| Small base | `previous < min_previous_postings`, **or any zero previous base regardless of the floor**, returns `INSUFFICIENT_DATA`, score 0.0 | Always |
| Score range | Clamped to `[-1.0, +1.0]`; unclamped value retained in `raw_growth_score` | Always |

Validation **rejects** rather than clamps. A caller passing `50` for a 50% share is
expressing a bug; clamping it to `1.0` would return a confident wrong weight instead of
an error.

## Semantics Worth Stating Explicitly

**The haircut is symmetric.** It multiplies the score by `(1 - stale_haircut_factor)`
regardless of sign, so it shrinks a contraction toward neutral exactly as much as an
expansion: `-0.54` becomes `-0.27`, and a `-0.40` reading is demoted from
`CONTRACTION_BEARISH` to `STABLE_NEUTRAL`. This is intended — stale postings make the
count *less informative*, they do not make the firm *more bearish* — but it means a
neutral classification should always be read alongside `has_ghost_postings_penalty`.

**The threshold is a cliff, and the input is a mean.** 119.9 days and 120.1 days differ
by a factor of two in the final score, and a mean masks the tail: 90% fresh postings plus
10% two-year-old ghosts averages about 100 days and never trips. Where the vendor exposes
per-posting creation and deletion dates — the measure Chen & Li construct duration from —
compute a stale *share* upstream and set the threshold against that instead.

**The score saturates.** Above roughly 75% QoQ growth (exactly: where
`growth_pct/100 × role_factor × haircut > 1`) every firm reports `+1.0`. Cross-sectional
sorts must use `raw_growth_score` or `qoq_postings_growth_pct`, or the whole top of the
distribution ties.

## Data Provenance Constraints

Posting counts are vendor artifacts before they are firm fundamentals:

- **Source method changes the count.** Feeds crawled directly from employer career sites
  and feeds assembled from job-board aggregators do not produce the same number for the
  same company, because aggregators carry cross-posted duplicates. Counts are comparable
  within a vendor panel, not across vendors.
- **Panel coverage moves.** A vendor adding a company's regional career sites mid-history
  produces a step change indistinguishable from hiring.
- **Delivery lags.** Scoring on the observation date rather than the delivery date is
  look-ahead bias. The knowledge-time filter belongs upstream — see
  `backtesting-alt-data-strategies-with-realistic-availability-lag`.
- **Permission is a separate question.** Scraped posting data raises vendor contract,
  scraping-terms and MNPI questions this module does not address. See
  `alternative-data-vendor-due-diligence-checklist` and
  `insider-trading-controls-for-alternative-data-usage`.

## Scope Boundary

This engine scores one company for one period from two counts, two role shares and one
duration average. It does not source, license, deduplicate or lag the data; it does not
de-seasonalize; it does not detect vendor panel drift; and it does not forecast returns.

## Sources

- Gutiérrez, E., Lourie, B., Nekrasov, A. & Shevlin, T. (2020), "Are Online Job Postings
  Informative to Investors?", *Management Science* 66(7):3133–3141 —
  <https://pubsonline.informs.org/doi/10.1287/mnsc.2019.3450>
- Belo, F., Lin, X. & Bazdresch, S. (2014), "Labor Hiring, Investment, and Stock Return
  Predictability in the Cross Section", *Journal of Political Economy* 122(1) —
  <https://www.journals.uchicago.edu/doi/abs/10.1086/674549>
- Chen, C.-W. & Li, L. Y. (2023), "Is hiring fast a good sign? The informativeness of job
  vacancy duration for future firm profitability", *Review of Accounting Studies*
  28(3):1316–1353 — <https://doi.org/10.1007/s11142-023-09797-2>
- Greenhouse, *2024 State of Job Hunting Report* (10 December 2024) —
  <https://www.greenhouse.com/blog/greenhouse-2024-state-of-job-hunting-report>
  (vendor-reported platform data; secondary, cited only for ghost-listing prevalence and
  the definition used)
