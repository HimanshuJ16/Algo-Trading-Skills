# Standards for Point-in-Time Fundamentals Data Joins

## Engineering standards enforced by the engine

| Rule | Standard | Rationale |
|---|---|---|
| Availability date | `availability_date = filing_date + availability_lag_days` (calendar days, default 1) | A filing date is a *date*, not a market-close timestamp. See the EDGAR cutoff below. |
| As-of join condition | `period_end_date <= as_of_date` **AND** `availability_date <= as_of_date` | Either condition alone admits data that was not public at `T`. |
| Selection order | `period_end_date`, then `filing_date`, then `revision_number` — descending | Filing-date-first ordering lets a late amendment to an old period outrank the original filing of a newer one. |
| Restatement isolation | A restatement is a new record (same period, later filing, higher revision). Later revisions are never returned for an earlier `as_of_date`. | The market traded the as-reported number; the corrected one did not exist. |
| Date format | Strict zero-padded ISO-8601 `YYYY-MM-DD`, validated at ingest | Lexicographic comparison is correct only for this layout; anything else fails silently rather than loudly. |
| Impossible dates | `filing_date < period_end_date` rejected; `non_reliance_date < filing_date` rejected | Physically impossible and a common vendor corruption that reintroduces lookahead. |
| Value domain | Finite real numbers only; NaN and ±Inf rejected at ingest | A non-finite fundamental propagates through every downstream ratio without raising. |
| Batch ingest | Atomic — one invalid record rejects the whole batch | A partially ingested batch queries as if it were complete. |
| Determinism | Ties broken on a total sort key; conflicting duplicates counted, not silently resolved | Results must not depend on insertion order. |
| Timezone | Filing dates are US Eastern business dates as assigned by EDGAR. The engine does no timezone conversion; it operates on dates. | Converting a date to a timestamp requires an assumed time of day, which is what `availability_lag_days` makes explicit instead. |

## Regulatory touchpoints (United States, SEC / Exchange Act)

All items below are **mandatory rules on the registrant**, not on the trading system. They are cited here because they determine when data becomes public, which is what the join depends on. They are US-specific; other jurisdictions have different regimes and the default availability lag must be re-derived before applying this engine outside the US.

| Claim | Authority | Detail |
|---|---|---|
| An electronic submission transmitted before 5:30 p.m. Eastern time is filed that business day; after 5:30 p.m. it is deemed filed the next business day. | Regulation S-T Rule 13(a)(2), 17 CFR 232.13(a)(2) | The operative fact for this skill: a 10-K accepted at 4:45 p.m. ET carries filing date `D` yet became public *after* the 4:00 p.m. ET regular-session close. Forms 3/4/5, Schedule 13D/13G, Schedule 14N and Form 144 have a 10 p.m. ET cutoff under 17 CFR 232.13(a)(4); Rule 462(b) registration statements under (a)(3). |
| EDGAR's assigned filing date (`FILED AS OF DATE`) is not the acceptance instant. | EDGAR submission header `ACCEPTANCE-DATETIME` | Acceptance datetime is when EDGAR accepted (and therefore disseminated) the submission. Where a vendor exposes it, use it and set `availability_lag_days=0`; the assigned filing date alone cannot distinguish a 9 a.m. filing from a 5:29 p.m. one. |
| Form 10-K is due 60 / 75 / 90 days after fiscal year end for large accelerated / accelerated / non-accelerated filers. | Exchange Act Rule 13a-1 and Form 10-K General Instruction A(2); filer categories defined in Rule 12b-2 (17 CFR 240.12b-2) | Bounds the maximum plausible gap between `period_end_date` and `filing_date`. Useful as a data-quality assertion, **not** as a substitute for the actual filing date — issuers routinely file early, and Rule 12b-25 permits a limited extension. |
| Form 10-Q is due 40 days after quarter end for accelerated and large accelerated filers, 45 days for all others. | Exchange Act Rule 13a-13 and Form 10-Q General Instruction A(1) | Same use and same caveat. |
| A registrant must disclose publicly when previously issued financial statements should no longer be relied upon. | Form 8-K Item 4.02 | This disclosure normally precedes the amended 10-K/10-Q. Modelled as the optional `non_reliance_date`: the value is still returned (it is what the market had) but flagged. |

### What is *not* claimed

- No claim is made that any trading-system control here is required by regulation. The join is a research-correctness control, not a compliance control.
- The default `availability_lag_days = 1` is an engineering choice that bounds the 5:30 p.m. ET window at daily granularity. It is **not** a regulatory figure and no rule prescribes it.
- Filing-deadline figures bound what is *permitted*; they say nothing about when a specific issuer actually filed. Never impute a filing date from a deadline.

## Sources

- 17 CFR 232.13 — Date of filing; adjustment of filing date. <https://www.law.cornell.edu/cfr/text/17/232.13>
- SEC, *Extending Form 144 EDGAR Filing Hours*, Release 33-11159 (restates the 5:30 p.m. / 10 p.m. ET cutoffs). <https://www.sec.gov/files/rules/final/2023/33-11159.pdf>
- SEC EDGAR Filer Manual (Volume II) — submission header fields including `ACCEPTANCE-DATETIME`. <https://www.sec.gov/files/edgar/filermanual/efmvol2-c10.pdf>
- Form 8-K, Item 4.02 — Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review. <https://www.sec.gov/fast-answers/answers-form8khtm.html>
- Periodic-report deadlines by filer status (secondary, corroborating): PwC Viewpoint, *SEC 3125 — The accelerated filer system*. <https://viewpoint.pwc.com/dt/us/en/pwc/pwc_sec_volume/pwc_sec_volume_US/3000_registration_an_US/sec_3125_the_acceler_US.html>
