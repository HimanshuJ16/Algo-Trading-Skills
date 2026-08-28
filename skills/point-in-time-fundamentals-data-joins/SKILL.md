---
name: point-in-time-fundamentals-data-joins
description: >-
  Use when joining SEC-filed fundamental metrics (EPS, revenue, debt/equity, free
  cash flow) to price bars for backtesting or factor research. Resolves the value
  that was publicly available at the as-of date using filing date plus an explicit
  availability lag, isolates later restatements from historical as-reported values,
  and quantifies what a naive period-end join would have leaked.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- point-in-time
- fundamentals
- sec-edgar
- filing-date
- restatement
- lookahead-bias
- as-of-join
brokers_frameworks:
- SEC EDGAR Public Database
- Point-In-Time Fundamentals Join Engine
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when joining fundamental financial metrics from 10-K and 10-Q filings to market price data for backtesting or factor research. A join keyed on `period_end_date` asserts that a company's Q4 numbers were known on 31 December. They were not: under Exchange Act reporting, a Form 10-K is due 60 days after fiscal year end for a large accelerated filer, 75 days for an accelerated filer and 90 days for everyone else; a Form 10-Q is due 40 days after quarter end for accelerated and large accelerated filers and 45 days for everyone else. The gap between the period end and the filing is weeks to months of pure lookahead, and it is the most common single defect in fundamental factor backtests.

This engine executes an as-of join on **availability date** — filing date plus an explicit lag — returns the value as reported at that time rather than as later restated, and reports what a naive period-end join would have used so the bias is measured rather than assumed away.

Use it as the data layer under any fundamental signal, and as an audit: run both joins over a historical universe and inspect `leakage_type` to see how much of a backtest's edge is bookkeeping.

## When NOT to Use

- **For intraday trading on filings.** Availability here is a *date*. A strategy reacting to a 10-Q inside the session needs EDGAR acceptance timestamps (`ACCEPTANCE-DATETIME` in the submission header), not this engine.
- **With `availability_lag_days=0` against raw EDGAR filing dates.** EDGAR assigns filing date `D` to any submission accepted before 5:30 p.m. ET (17 CFR 232.13(a)(2)), which is 90 minutes *after* the 4:00 p.m. ET regular-session close. Zero lag lets a backtest trade the `D` close on a document filed at 4:45 p.m. Set 0 only when the caller has already resolved intraday availability itself.
- **As a business-day or settlement calculator.** The lag is calendar days. That is conservative for `<=` date comparison but it is not a trading-day calendar; see `global-exchange-holiday-calendar-handling`.
- **To detect a vendor's bad filing dates.** The engine rejects the impossible (`filing_date < period_end_date`) but cannot detect a plausible-but-wrong back-stamp. Cross-check against EDGAR full-index data — `data-vendor-cross-validation-for-backtests`.
- **For non-US reporting regimes without re-deriving the lag.** The 5:30 p.m. cutoff and the 10-K/10-Q deadlines are US Exchange Act rules. The join logic is jurisdiction-agnostic; the default lag rationale is not.
- **As a universe constructor.** Tickers are matched literally. Delistings and ticker reuse belong to `survivorship-bias-free-universe-construction` and `reference-data-symbol-mapping-across-vendors`.

## Prerequisites

- Filing records carrying `ticker`, `metric_name`, `value`, `period_end_date`, `filing_date`, and `revision_number` — where a **restatement is a new record**, not an edit: same `period_end_date`, later `filing_date`, higher `revision_number`. A vendor table that overwrites in place cannot support a PIT join at all and must be rebuilt from filing history first.
- All dates as strict zero-padded ISO-8601 `YYYY-MM-DD`. The engine rejects anything else rather than comparing it.
- An `availability_lag_days` you are willing to defend. The default is 1 calendar day; see **When NOT to Use** for why 0 is unsafe against EDGAR filing dates.
- Optionally, `non_reliance_date` per record: the date an Item 4.02 Form 8-K disclosed publicly that the figure should no longer be relied upon.

## Workflow

1. **Compute Availability, Not Filing Date**:
   - Availability date $= \text{filing\_date} + \text{availability\_lag\_days}$ (calendar days).
   - **Decision point — the lag exists because a filing date is not a market-close timestamp.** A filing accepted at 4:45 p.m. ET carries that day's filing date but became public after the close. The default 1-day lag closes that window. Reduce it only against acceptance timestamps, never against raw filing dates.

2. **Filter to What Was Public**:
   - Keep records where $\text{period\_end\_date} \le T$ **and** $\text{availability\_date} \le T$.
   - **Decision point — both conditions, not either.** The period-end condition alone is the naive join. The availability condition alone would admit a filing whose fiscal period has not yet closed — impossible in real data, but exactly what a corrupt back-stamped filing date produces, which is why the engine rejects `filing_date < period_end_date` at ingest instead of quietly filtering it.

3. **Select by Fiscal Period First**:
   - Order surviving records by `period_end_date`, then `filing_date`, then `revision_number`; take the maximum.
   - **Decision point — period end is the primary key, not filing date.** A 10-K/A restating FY-2022 filed in August 2023 has a *later filing date* than the original Q1-2023 10-Q filed in April 2023. Sorting by filing date first therefore answers "latest known EPS as of September" with the restated FY-2022 figure — a stale fiscal period dressed as fresh data. Ties are broken deterministically so results do not depend on insertion order; conflicting duplicates are counted in `ambiguous_candidate_count` rather than silently resolved.

4. **Audit the Naive Alternative**:
   - Recompute the same selection ignoring availability, and classify the difference as `NONE`, `UNRELEASED_FILING`, `RESTATEMENT`, or `UNRELEASED_AND_RESTATEMENT`.
   - **Decision point — an unreleased filing is not a restatement.** These are different defects with different remedies: one is a timing error in the join, the other is a data-versioning error in the store. `restatement_leakage_blocked` is set only when a later revision *of the matched period* was withheld; a plain not-yet-filed report sets `unreleased_filing_blocked` instead. Reporting the second as the first makes the audit useless.
   - `naive_join_value` carries the figure the naive join would have used, so the leakage has a magnitude and not just a boolean.

5. **Flag Non-Reliance Without Deleting History**:
   - If the matched record carries a `non_reliance_date` on or before $T$, set `is_non_reliance_flagged`.
   - **Decision point — flag, do not drop.** Between an Item 4.02 8-K and the corrected filing, the market knows the number is wrong but has no replacement. Returning nothing would put a value in the backtest that nobody had either; returning the as-reported figure silently would trade a discredited input. The caller decides — typically by suppressing the signal for that name until the amendment lands.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Joining on period end date**: treating 31 December as the availability date for FY numbers that a large accelerated filer may lawfully file as late as 1 March, and a non-accelerated filer as late as 31 March. The resulting "edge" is knowledge of the future.
- **Trusting `filing_date <= as_of_date` at daily granularity**: EDGAR stamps filing date `D` on anything accepted before 5:30 p.m. ET, and the regular session closes at 4:00 p.m. ET. Same-day use is a real, systematic lookahead window, not a rounding detail.
- **Sorting candidates by filing date first**: a late amendment to an old period outranks the original filing of a newer one, so the "most recent" fundamental is a stale fiscal quarter. This is silent — the value is real, the period is wrong, and nothing in a naive report shows it.
- **Overwriting as-reported values with restatements**: a fundamentals table that edits rows in place cannot answer any historical question. Restatements are new rows with a later filing date.
- **Ignoring the non-reliance interval**: a store that only tracks amended filings keeps serving the discredited figure for the weeks or months between the Item 4.02 8-K and the amendment, precisely when the market has repriced the name.
- **Comparing dates as strings without validating the format**: lexicographic comparison is correct *only* for zero-padded ISO-8601. `'2/15/2023' < '2022-12-31'` is `True`, so a mis-formatted feed does not error — it silently returns no data, or the wrong period, for every query.
- **Accepting `filing_date` earlier than `period_end_date`**: physically impossible and a common vendor corruption. Left in, it reintroduces exactly the bias the join exists to remove.
- **Letting a NaN into the store**: a non-finite fundamental propagates through every downstream ratio without raising, and the factor silently becomes "whichever names had clean data".
- **Assuming a passing PIT join makes the backtest clean**: it removes fundamentals lookahead only. Universe selection (`backtest-look-ahead-in-universe-selection`), survivorship, and price adjustment (`adjusted-vs-unadjusted-price-series-pitfalls`) are separate leaks.

## Verification

- Insert FY-2022 EPS $= \$1.50$ filed 2023-02-15 and a restatement to $\$1.20$ filed 2023-08-10. With the default 1-day lag, confirm: a query at 2023-02-15 returns `NO_DATA_AVAILABLE_AS_OF_DATE`; at 2023-02-16 returns $\$1.50$ with `matched_available_from = '2023-02-16'`; at 2023-08-10 still returns $\$1.50$; at 2023-08-11 returns $\$1.20$.
- Confirm the mid-window query (2023-05-01) sets `restatement_leakage_blocked = True`, `leakage_type = RESTATEMENT`, and `naive_join_value = 1.20`.
- **Ordering regression**: add Q1-2023 EPS $= \$1.80$ filed 2023-04-20 to the pair above. A query at 2023-09-01 must return $\$1.80$ for period `2023-03-31` — not the restated $\$1.20$ for `2022-12-31`. A `period_end_date='2022-12-31'` query at the same date must return $\$1.20$.
- **Leakage-classification regression**: with a single unrestated record filed 2023-02-15, a query at 2023-01-15 must report `restatement_leakage_blocked = False`, `unreleased_filing_blocked = True`, `leakage_type = UNRELEASED_FILING`. Reporting a blocked restatement here, with zero restatements in the data, is the defect.
- Confirm that at 2023-04-01 the combined case reports `UNRELEASED_AND_RESTATEMENT` with `naive_join_value = 1.80`.
- Negative checks: `'2023-2-15'`, `'02/15/2023'`, `'20230215'`, `'2023-02-30'`, a `filing_date` before `period_end_date`, a `NaN` or infinite value, a negative `revision_number`, a blank ticker, a `non_reliance_date` before the filing, and a negative `availability_lag_days` must each raise `ValueError`. A batch containing one bad record must store none of it.
- Confirm selection is insertion-order independent, and that two conflicting records with identical `(period_end, filing_date, revision_number)` set `ambiguous_candidate_count = 2` and a `WARNING` in `audit_notes`.
- Run `python -m unittest discover -s .` from the `scripts/` directory and confirm 100% pass rate.

## Related Skills

- `point-in-time-database-for-ml-training-data`
- `lookahead-bias-elimination`
- `backtest-database-schema-for-point-in-time-queries`
- `corporate-action-adjusted-backtesting`
- `data-vendor-cross-validation-for-backtests`
- `reference-data-golden-source-designation`
- `survivorship-bias-free-universe-construction`
