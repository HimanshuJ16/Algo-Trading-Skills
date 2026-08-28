# Standards for Patent Filing Data for Innovation Signal Research

## Legal availability rules (mandatory — these are statute, not convention)

| Rule | Source | What it means for the signal |
|---|---|---|
| Applications are held "in confidence" until published. | 35 U.S.C. 122(a) | A `filing_date` is **never** an availability date. |
| Publication "promptly after the expiration of a period of 18 months from the earliest filing date for which a benefit is sought." | 35 U.S.C. 122(b)(1)(A) | The normal US availability date is the pre-grant (A) publication. |
| Projected publication date is the later of 18 months from the earliest claimed filing date or ~14 weeks from the filing-receipt mailing date; publications issue weekly on Thursdays. | MPEP 1120 | The 18-month rule is a floor with administrative slack — use the observed date. |
| Non-publication request available where the invention "has not been and will not be the subject of an application filed in another country ... that requires publication". | 35 U.S.C. 122(b)(2)(B)(i) | Some applications are **never** A-published and become public only at grant. |
| No publication while a secrecy order stands. | 35 U.S.C. 181 | Same effect: availability defers to grant, or never. |
| Publication 18 months from the date of filing or, where priority is claimed, from the date of priority; earlier on the applicant's request; simultaneous with the grant specification where grant comes first. | Article 93 EPC | The EPO clock runs from **priority**, not filing. Do not reuse a US-derived offset. |

**Availability date = `min(pre_grant_publication_date, grant_date)`** over whichever of the two
exists. `None` for a pending, unpublished application.

## Methodological standards (sourced)

| Metric | Standard | Source |
|---|---|---|
| Forward citation truncation | MUST be adjusted before cross-sectional comparison. Raw cumulative counts are not comparable across grant years or technology fields: "significant changes over time in the rate of patenting and in the number of citations made, as well as the inevitable truncation of the data, make it very hard to use the raw number of citations received by different patents directly in a meaningful way." | Hall, Jaffe & Trajtenberg (2001), NBER WP 8498 |
| Truncation remedy | Cohort scaling — the "fixed-effects approach ... scaling citation counts by dividing them by the average citation count for a group of patents to which the patent of interest belongs." Implemented here over `(technology_class, availability_year)`. | Hall, Jaffe & Trajtenberg (2001); applied in Hirshleifer, Hsu & Li (2013) as the mean over "all patents of the same subcategory granted in year t−j" |
| Truncation magnitude | Roughly half a patent's lifetime citations arrive in its first decade: "patents granted in year 2,000 will receive just half of their citations by 2,010, 75% by 2,020, and even by 2,050 they will still be receiving some." | Hall, Jaffe & Trajtenberg (2001) |
| Scale normalisation | The documented return predictor is an efficiency ratio: "innovative efficiency (IE), patents or citations scaled by research and development expenditures, is a strong positive predictor of future returns." Raw counts are a size proxy. | Hirshleifer, Hsu & Li (2013), *JFE* 107(3), 632–654 |
| Aggregation window | A 5-year window over patents is the published convention, tied to evidence that "technology cycles measured by the duration of the benefits of R&D spending are approximately 5 years in most industries." | Hirshleifer, Hsu & Li (2013), citing Lev & Sougiannis (1996) |
| Application-to-grant lag | ~2 years on average, historically: HJT report ~85% granted 2 years after filing and ~95% after 3, and advise "at least a 3-year 'safety lag'" when dating by application year. | Hall, Jaffe & Trajtenberg (2001) |

**Current pendency is not a constant.** The HJT figures describe a 1963–1999 sample. Read live
application-to-grant pendency from the USPTO Patents Pendency dashboard
(<https://www.uspto.gov/dashboard/patents/pendency.html>), which defines *First Office Action
Pendency* as "the average number of months from the patent application filing date to the date a
First Office Action is mailed" and *Traditional Total Pendency* as the average months "from the
patent application filing date to the date the application has reached final disposition."
Do not hard-code a pendency number into a signal.

## Engineering defaults (this module — NOT sourced requirements)

Every value below is an engineering default chosen for this implementation. None is imposed by a
regulator, an exchange, or a published result. Calibrate them.

| Parameter | Default | Note |
|---|---|---|
| `velocity_weight` / `citation_weight` | 0.50 / 0.50 | Applied to **standardised** components, so they are genuine mix weights. Renormalised by their sum. |
| `lookback_years` | 5 | The one default with a published antecedent (see above). |
| `winsorize_z` | 3.0 | Applied after standardisation. With population sigma, \|z\| ≤ sqrt(N−1), so ±3.0 is inert below N = 10. |
| `min_cohort_size` | 5 | Below this a cohort mean is not trusted as a truncation denominator. |
| `log_compress_citations` | `True` | $\ln(1+x)$ on the cohort ratio to stop one mega-cited patent carrying an issuer. Layered on top of the sourced cohort adjustment; not part of HJT. |
| `min_universe_size` | 2 | Hard floor — a cross-section of one has no dispersion to standardise against. |
| `max_citation_observation_span_days` | 31 | Counts read across a wider span cover different exposure windows. |
| `RECOMMENDED_MIN_UNIVERSE` | 30 | Advisory flag only; surfaced as `universe_below_recommended_size`. |

## Data source status

| Source | Status | Note |
|---|---|---|
| USPTO Open Data Portal (`data.uspto.gov`) | Current first-party route | Requires a USPTO.gov account with a linked ID.me account to obtain an API key. Rate limits are published at `data.uspto.gov/apis/api-rate-limits`; read them there rather than assuming a figure. |
| PatentsView legacy API (`api.patentsview.org`) | Discontinued 1 May 2025 | Returns HTTP 410. |
| `patentsview.org` | Redirects to the Open Data Portal | The PatentsView site migrated to `data.uspto.gov`; verify current PatentSearch API availability there before depending on it. |
| NBER Patent Citation Data File | Research reference | Hall, Jaffe & Trajtenberg (2001); historical (grants 1963–1999, citations 1975–1999), not a live feed. |
