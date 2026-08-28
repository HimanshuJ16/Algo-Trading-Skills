# Pre-Flight Checklist — Point-in-Time Fundamentals Data Joins

Sign off before a fundamental signal is backtested or promoted. Any unchecked box
in **Store** or **Join** invalidates the backtest, not just the audit.

## Store — is the data capable of a PIT join at all?

- [ ] Is the filing store **append-only per filing** — one row per `(ticker, metric, period_end_date, filing_date, revision_number)`?
- [ ] Are restatements stored as **new rows** (same period, later filing date, higher revision) rather than in-place edits?
- [ ] If the vendor only ships a current view, has filing history been rebuilt from EDGAR or from dated snapshots? (It cannot be recovered after an overwrite.)
- [ ] Are all dates strict zero-padded ISO-8601 `YYYY-MM-DD`, validated at ingest rather than compared as free-form strings?
- [ ] Are records with `filing_date < period_end_date` rejected?
- [ ] Are non-finite (`NaN`, `±Inf`) values rejected at ingest?
- [ ] Is batch ingest atomic, so a rejected record cannot leave a half-loaded batch queryable?

## Join — is the as-of predicate right?

- [ ] Are joins performed on **availability date** (`filing_date + availability_lag_days`), not on `period_end_date` and not on bare `filing_date`?
- [ ] Is `availability_lag_days >= 1` whenever the source is EDGAR's assigned filing date? (Filings accepted up to 5:30 p.m. ET carry that day's date but post-date the 4:00 p.m. ET close — 17 CFR 232.13(a)(2).)
- [ ] If the lag is set to 0, is intraday availability resolved elsewhere — e.g. from the EDGAR submission header's `ACCEPTANCE-DATETIME`?
- [ ] Does the filter apply **both** `period_end_date <= T` and `availability_date <= T`?
- [ ] Does record selection order by `period_end_date` **first**, then `filing_date`, then `revision_number`? (Filing-date-first returns a stale fiscal period whenever an amendment post-dates a newer period's original filing.)
- [ ] Is selection insertion-order independent, and are conflicting duplicates surfaced rather than silently resolved?
- [ ] Is any vendor ingestion delay added on top of the EDGAR lag rather than assumed to be zero?

## Audit — is the leakage measured, not assumed?

- [ ] Is restatement lookahead audited by comparing the **value** a naive join would have returned, not by counting filtered records?
- [ ] Are `unreleased_filing_blocked` and `restatement_leakage_blocked` reported as **distinct** findings? (A count-based audit reports blocked restatement leakage on data containing no restatements.)
- [ ] Has the universe been swept under both joins, with `leakage_type` counts and `|pit_value − naive_value|` recorded?
- [ ] Does the strategy's edge survive the PIT join? (If not, it was trading the difference between the two.)
- [ ] Are unfiled and future-period reports excluded for every historical query date?

## Corrections and non-reliance

- [ ] Are Item 4.02 Form 8-K non-reliance disclosures tracked as `non_reliance_date` per record?
- [ ] Is there an explicit decision for the interval between the 8-K and the amendment — suppress the signal, or trade it knowingly?
- [ ] Is the as-reported value retained rather than deleted when a non-reliance disclosure lands?

## Scope

- [ ] Is the availability lag re-derived for any non-US reporting regime, rather than inheriting the SEC-derived default?
- [ ] Are the *other* backtest leaks covered separately — universe selection, survivorship, and price adjustment? (A clean PIT fundamentals join does not make a backtest clean.)
- [ ] Are filing deadlines (10-K 60/75/90 days; 10-Q 40/45 days by filer status) used only as data-quality bounds, never to impute a missing filing date?

**Signed off by:** ______________________  **Date:** ______________
