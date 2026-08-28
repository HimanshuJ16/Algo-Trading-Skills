---
name: patent-filing-data-for-innovation-signal-research
description: >-
  Point-in-time equity factor research on corporate patent filings — score each filing from the
  date it actually became public (18-month pre-grant publication or grant, whichever came first),
  remove citation truncation with Hall-Jaffe-Trajtenberg cohort scaling, and standardise velocity
  and citation quality separately before weighting them into an Innovation Quality Score.
domain: Quantitative Research & Alternative Data
subdomain: Corporate Innovation & R&D Alpha Signals
tags: ["patent-data", "uspto", "innovation-signal", "forward-citations", "point-in-time-data", "citation-truncation", "quant-factor", "r-and-d"]
brokers_frameworks: ["USPTO Open Data Portal", "35 U.S.C. 122", "Article 93 EPC", "NBER Patent Citation Data File", "Python standard library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when building a cross-sectional equity factor from corporate patent filings. The
engine turns a stream of patent records into a point-in-time Innovation Quality Score (IQS) and
its z-scored factor, keyed on an explicit `as_of` cut-off.

Balance-sheet R&D expense measures *spending*, not *productivity*, and the published result is
about the ratio between them: Hirshleifer, Hsu & Li (2013) find that "innovative efficiency (IE),
patents or citations scaled by research and development expenditures, is a strong positive
predictor of future returns after controlling for firm characteristics and risk." Note what that
sentence does and does not say — the predictor is patents **scaled by R&D**, the sample is US
firms 1981–2006, and the authors attribute the effect substantially to mispricing and limited
investor attention rather than to a risk premium. A raw patent count is mostly a firm-size proxy.

Three things make this harder than counting patents, and the module exists for all three:

- **A filing is not public on its filing date.** 35 U.S.C. 122(a) holds applications "in
  confidence." Publication comes later, and the gap is where look-ahead bias enters.

- **Forward citations are a forward-looking quantity by construction.** Hall, Jaffe & Trajtenberg
  (2001) put a number on it: if the lag distribution were stable, "patents granted in year 2,000
  will receive just half of their citations by 2,010, 75% by 2,020, and even by 2,050 they will
  still be receiving some." Scoring a 2016 cross-section with a citation count pulled from today's
  database therefore imports roughly a decade of future information.

- **Raw counts are dominated by scale, and the two obvious components are the same component.**
  A *sum* of citations over a firm's patents grows with the patent count, so weighting "velocity"
  against "citation total" weights firm size against firm size.

## When NOT to Use

- **Without per-date citation snapshots, for a historical backtest.** Every record must carry
  `citations_observed_asof`, and the engine raises `PatentDataError` when that date is after
  `as_of`. This is the point of the module, not an obstacle to route around: if you only have
  today's cumulative counts, you can score *today's* cross-section honestly and nothing earlier.
  Do not "fix" the exception by widening `as_of` or stripping the observation date.

- **As an innovative-efficiency replication.** Hirshleifer, Hsu & Li scale by an R&D capital stock
  built from a weighted 5-year expense history and use grant-year cohorts of USPTO subcategories.
  This engine takes one flat `innovation_input` per asset and builds cohorts from the availability
  year. It is the same idea, not the same estimator, and it will not reproduce their numbers.

- **On a universe too small to standardise.** The factor is a cross-sectional z-score. With
  population sigma and N assets, the largest attainable |z| is sqrt(N−1), so the ±3 winsorisation
  is inert below N = 10 and z-scores in a handful of names describe universe size more than
  innovation. Read `universe_below_recommended_size` and `winsorisation_can_bind` before ranking.

- **For patent valuation.** Counts and citations proxy technological impact, not economic value.
  A factor built here says nothing about whether a patent is enforceable, licensed, or worth
  anything.

- **Across jurisdictions without checking the availability rule.** The 18-month rule is common to
  35 U.S.C. 122(b)(1)(A) and Article 93(1)(a) EPC, but the EPC clock runs from the filing *or
  priority* date, and other offices differ. Supply observed dates; do not compute them.

- **As a compliance control over alternative data.** For MNPI and vendor-governance questions see
  `insider-trading-controls-for-alternative-data-usage` and
  `alternative-data-vendor-due-diligence-checklist`.

## Prerequisites

- Per patent: `asset_id`, `patent_id`, `filing_date`, and **both** availability dates where they
  exist — `pre_grant_publication_date` and `grant_date`. `patent_id` must be unique; assignee
  disambiguation joins routinely emit one row per assignee per patent.
- `forward_citations` **with** the `citations_observed_asof` date the count was read. Required
  whenever the count is non-zero; an undated cumulative count cannot be checked for look-ahead.
- `technology_class` (CPC/IPC/USPC subcategory). Together with the availability year this forms
  the citation cohort. Records with no class fall into `UNCLASSIFIED` and are adjusted against
  each other, which is weaker than a real classification.
- Optional but strongly recommended: `innovation_inputs`, an `{asset_id: R&D spend}` mapping in
  your own units. Without it the velocity term is a raw count and the factor carries a firm-size
  component; the report says so in `warnings`.
- A data source. The USPTO **Open Data Portal** (`data.uspto.gov`) is the current first-party
  route and requires a USPTO.gov account with a linked ID.me account to obtain an API key; rate
  limits are published at `data.uspto.gov/apis/api-rate-limits` — read them there rather than
  assuming a number. The legacy PatentsView API (`api.patentsview.org`) was discontinued on
  1 May 2025 and returns HTTP 410, and `patentsview.org` now redirects to the Open Data Portal.

## Workflow

1. **Establish each patent's public availability date — never its filing date.**
   `public_availability_date` is the **earlier of** `pre_grant_publication_date` and `grant_date`.
   - **Decision point — this is a `min()`, not "filing + 18 months".** Under 35 U.S.C.
     122(b)(1)(A) an application publishes "promptly after the expiration of a period of 18 months
     from the earliest filing date for which a benefit is sought", and MPEP 1120 puts the
     projected date at the later of that or roughly 14 weeks from the filing receipt, issuing
     weekly on Thursdays. But a **non-publication request** under 35 U.S.C. 122(b)(2)(B)(i) is
     available where the invention "has not been and will not be the subject of an application
     filed in another country ... that requires publication", and a **secrecy order** under
     35 U.S.C. 181 suppresses publication entirely. Those applications become public only at
     grant. A synthesised `filing_date + 18 months` is wrong for exactly the filings a
     non-publication request was chosen to hide.
   - A pending, unpublished application has no availability date and carries no tradable
     information. It is counted in `not_yet_public_excluded`, not silently dropped.

2. **Evaluate as of an explicit instant.** `compute_patent_innovation_signals(records, as_of=...)`
   scores only patents public at or before `as_of` and inside the rolling `lookback_years` window
   (default 5, inclusive at both ends). Pass the whole history and roll `as_of` forward.
   - **Decision point — a citation count observed after `as_of` is rejected, not clipped.** The
     engine cannot know which of those citations predate the cut-off, so it refuses rather than
     guessing. Re-read the count as of the evaluation date.
   - Counts read across a wide span of dates cover different exposure windows and are not
     cross-comparable even when each individually passes the cut-off; the span is reported in
     `citation_observation_span_days` and warned on above `max_citation_observation_span_days`.

3. **Deduplicate on `patent_id` before counting anything.** One patent arriving as several
   assignee rows inflates velocity for precisely the largest, most-joined issuers. Duplicates are
   counted in `duplicate_patent_ids_dropped`.

4. **Remove citation truncation with cohort scaling.** Each patent's count is divided by the mean
   count of its `(technology_class, availability_year)` cohort. This is Hall, Jaffe & Trajtenberg's
   "fixed-effects approach ... scaling citation counts by dividing them by the average citation
   count for a group of patents to which the patent of interest belongs"; Hirshleifer, Hsu & Li
   apply the same construction because it "helps control for citation propensity attributed to
   differences in technology fields, grant year, and citing year."
   - **Decision point — a cohort below `min_cohort_size` is not trusted.** Those patents keep
     their raw, still-truncation-biased counts and are counted in `cohorts_below_min_size`. A
     cohort of one would adjust that patent to exactly 1.0 and tell you nothing.
   - A cohort in which nothing has been cited yet has mean 0 and is left unadjusted rather than
     divided by.

5. **Build two components that are not the same component.**
   - Velocity $V_i$ = patents available in the window, divided by `innovation_input` when supplied.
   - Quality $Q_i$ = the **mean** cohort-adjusted ratio per patent — scale-free by construction,
     so it is not a second copy of the count. Optionally compressed with $\ln(1+x)$ so one
     mega-cited patent cannot carry an issuer.

6. **Standardise each component, then weight.**
   $$Z^V_i = \frac{V_i - \mu_V}{\sigma_V}, \qquad Z^Q_i = \frac{Q_i - \mu_Q}{\sigma_Q}$$
   $$IQS_i = \frac{w_{\text{vel}} Z^V_i + w_{\text{cite}} Z^Q_i}{w_{\text{vel}} + w_{\text{cite}}}$$
   - **Decision point — the order matters and is the whole reason the weights mean anything.**
     Weighting a raw count against a sum of logs, as $w_v V_i + w_c C_i$ does, hands the factor to
     whichever term happens to carry the larger numeric scale, regardless of the weights. After
     standardisation the factor is invariant to the units of either component.
   - The composite is re-standardised and then winsorised to ±`winsorize_z`. Winsorisation is
     applied last, so the delivered factor is unit-variance but only approximately mean-zero once
     any name is clipped.

7. **Reconcile before using the output.** `records_supplied` equals `patents_scored` plus the
   three exclusion counters exactly; `report.reconciles()` checks it. Read `warnings` and the
   `status` field — `SIGNALS_GENERATED`, `EMPTY_UNIVERSE`, `INSUFFICIENT_UNIVERSE` (no peers to
   standardise against) or `NO_DISPERSION` (every asset identical, nothing to rank). On the last
   three, `top_innovator` is `"NONE"` and is not a key in `z_scores`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Dating a patent by its filing date.** The application was confidential then (35 U.S.C. 122(a)).
  Hall, Jaffe & Trajtenberg recommend the application date as "the relevant time placer" for
  measuring *when invention happened* — that is a question about innovation, not about what a
  portfolio could have known. For a tradable signal the date is publication.

- **Dating it by the grant date instead, and assuming that is conservative.** For most
  applications the 18-month A-publication comes first, so grant-dating discards roughly a year and
  a half of genuinely public information. The correct rule is the earlier of the two, per patent.

- **Using today's cumulative citation count in a historical cross-section.** This is the largest
  single source of look-ahead in patent factors, and it does not look like a bug: the counts are
  real, they are just measured too late. Half of a patent's lifetime citations arrive in its first
  decade (HJT 2001).

- **Comparing raw citation counts across technology fields or filing cohorts.** A biotech patent
  from 2015 and a software patent from 2022 have incomparable expected counts. Cohort-scale first.

- **Summing citations instead of averaging them.** A sum is a count in disguise and reintroduces
  the size factor you were trying to escape.

- **Treating the ±3 winsorisation as an active control.** With population sigma it cannot bind
  until N ≥ 10; below that it is decorative.

- **Letting a NaN citation count become a zero.** `max(0, float('nan'))` evaluates to `0` in
  Python, so a failed upstream join silently becomes "this patent was never cited." The engine
  raises instead.

- **Counting one patent once per assignee row.** Disambiguated assignee tables are one-to-many.

## Verification

- `PatentFilingRecord("AAA", "a1", filing_date=..., grant_date=2023-05-01,
  pre_grant_publication_date=2021-07-01).public_availability_date` $\implies$ `2021-07-01`; with
  `pre_grant_publication_date=None` $\implies$ the grant date; with neither $\implies$ `None`.
- Three issuers — AAA with 10 patents × 1 citation, BBB with 2 × 50, CCC with 4 × 5, one cohort
  of 16 whose mean is 8.125 $\implies$ quality terms 0.123077 / 6.153846 / 0.615385 and a factor
  of +0.807947 / +0.601221 / −1.409168. Multiplying every citation count by 10 $\implies$ an
  identical factor (unit invariance).
- Supply a patent granted after `as_of` $\implies$ `not_yet_public_excluded == 1` and it is absent
  from the scored count. Supply `citations_observed_asof > as_of` $\implies$ `PatentDataError`.
- Supply `forward_citations=float("nan")` $\implies$ `PatentDataError`, not a zero-citation patent.
- Empty input $\implies$ `status == "EMPTY_UNIVERSE"`; one asset $\implies$ `INSUFFICIENT_UNIVERSE`
  with no scores emitted; identical assets $\implies$ `NO_DISPERSION`.
- Run `python -m unittest discover -s skills/patent-filing-data-for-innovation-signal-research/scripts`.

## Related Skills

- `insider-transaction-filing-signal-research`
- `earnings-call-transcript-nlp-signal-research`
- `lookahead-bias-elimination`
- `point-in-time-fundamentals-data-joins`
- `factor-research-multiple-testing-correction`
- `alternative-data-vendor-due-diligence-checklist`
