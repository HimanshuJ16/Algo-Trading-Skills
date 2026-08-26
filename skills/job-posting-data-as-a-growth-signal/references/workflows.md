# Workflows for Job Posting Growth Signal Analysis

## 0. Before the Engine Sees Anything

The engine takes two counts and has no time axis of its own. Everything that makes those
counts trustworthy happens upstream:

- Apply the vendor's **publication lag** so the snapshot reflects what was knowable on the
  decision date, not what was observable in the world
  (`backtesting-alt-data-strategies-with-realistic-availability-lag`).
- Confirm both counts come from the **same vendor panel** with no coverage change between
  them. A vendor onboarding a company's regional career sites mid-history looks exactly
  like hiring.
- For a **seasonal hirer**, pass the same quarter one year prior as
  `previous_active_postings_count` rather than the immediately preceding quarter. Do not
  mix YoY and QoQ bases inside one cross-section — the two are not on the same scale.
- Clear licensing and MNPI questions separately
  (`alternative-data-vendor-due-diligence-checklist`,
  `insider-trading-controls-for-alternative-data-usage`).

## 1. Job Posting Data Ingestion

Ingest current vs previous active job postings, role mix shares, and average posting
duration into `CompanyJobPostingSnapshot`. Role shares are fractions (0.5 == 50%) of the
same posting count, so they cannot sum above 1.0.

## 2. Validation and the Small-Base Gate

`calculate_growth_score` validates before it computes, and raises `JobPostingSignalError`
rather than degrading:

- NaN counts would clamp to `-1.0` and be reported as a confident `CONTRACTION_BEARISH`.
- A NaN duration passes `nan > 120` as `False` and would silently escape the haircut.
- A share supplied as `50` instead of `0.5` would previously be clamped to `1.0`, i.e. a
  wrong weight reported with full confidence.

Snapshots whose previous count is below `min_previous_postings` (default 10) return
`INSUFFICIENT_DATA` with a zero score. A previous count of zero has no defined growth
rate; the engine reports `0.0` rather than substituting a denominator. `2 -> 10` postings
is arithmetic, not signal.

## 3. QoQ Hiring Velocity

`growth_pct = (current - previous) / previous * 100`, rounded to 2dp. Always reported,
even when the base gate suppresses the score, so the audit trail records what was seen.

## 4. Role-Weighted Score

`role_factor = 1.0 + engineering_share * 0.5 + sales_share * 0.3` (so a fully engineering
book carries 1.5). The rationale is the growth-vs-replacement distinction in Gutiérrez et
al. (2020), where the posting–performance relation is stronger for growth hiring; the
particular weights are a calibration choice, not a published estimate.

## 5. Stale Ghost Listing Haircut

If `avg_posting_duration_days` is **strictly greater** than
`ghost_job_stale_days_threshold`, multiply the score by `(1 - stale_haircut_factor)`.

Three properties to hold in mind:

1. **Symmetric.** It shrinks toward neutral in both directions — `-0.54` becomes `-0.27`,
   and `-0.40` is demoted from bearish to neutral. Read a `STABLE_NEUTRAL` alongside
   `has_ghost_postings_penalty`.
2. **A cliff.** 119.9 and 120.1 days differ by 2× in the output. Expect ranking
   instability for firms near the threshold.
3. **Possibly backwards for senior roles.** Chen & Li (2023) find longer vacancy duration
   predicts *higher* profitability for high-skill roles. Set `stale_haircut_factor=0.0` to
   disable the haircut for engineering-heavy books, or calibrate it per role mix, rather
   than accepting the default on a book of hard-to-fill senior requisitions.

## 6. Classification and Saturation

Clamp into `[-1.0, +1.0]`, then classify against `classification_threshold` (default 0.25,
inclusive at the boundary): `EXPANSION_BULLISH` at or above `+0.25`,
`CONTRACTION_BEARISH` at or below `-0.25`, `STABLE_NEUTRAL` between.

The clamp saturates above roughly 75% QoQ growth. `raw_growth_score` retains the
unclamped value and the audit note carries `[SATURATED at clamp bound]`; use one of those
for cross-sectional ranking, never the clamped score.

## 7. Interpretation and Reporting

`JobPostingSignalReport` is a **fundamental expansion** record, not a trade instruction.
`EXPANSION_BULLISH` says the firm is hiring into growth roles; it does not say the stock
outperforms, and the realized-hiring-rate literature (Belo, Lin & Bazdresch, 2014) finds
the opposite sign for returns. Feed the score into a factor model alongside valuation and
risk controls, and persist `audit_notes` with the engine configuration that produced it.
