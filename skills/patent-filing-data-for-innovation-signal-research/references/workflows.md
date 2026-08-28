# Workflows for Patent Filing Data for Innovation Signal Research

## 0. Decide what date you are allowed to know things on

Fix the `as_of` instant first. Every other step is defined relative to it. If you cannot obtain
citation counts snapshotted at that instant, you can honestly score only the *current*
cross-section — say so rather than backdating today's counts.

## 1. Resolve each patent's public availability date

```
available = min(pre_grant_publication_date, grant_date)   # over whichever exist
```

- Both present → the A-publication almost always wins.
- `pre_grant_publication_date` absent → non-publication request (35 U.S.C. 122(b)(2)(B)(i)) or a
  secrecy order (35 U.S.C. 181); the patent becomes public at grant.
- Both absent → pending and confidential. No signal. Counted in `not_yet_public_excluded`.

Never synthesise the date as `filing_date + 18 months`. That formula is wrong for exactly the
subset a non-publication request exists to protect, and at the EPO the clock runs from the
priority date rather than the filing date (Art. 93(1)(a) EPC).

## 2. Validate and deduplicate

The engine raises `PatentDataError` on: blank `asset_id`/`patent_id`; a grant or publication date
preceding the filing date; non-finite or negative citation counts; a non-zero citation count with
no `citations_observed_asof`; and any `citations_observed_asof` after `as_of`.

Duplicate `patent_id`s (case-insensitive) are dropped and counted, not raised — assignee
disambiguation legitimately produces one row per assignee per patent, and counting them all
inflates velocity for the largest issuers specifically.

## 3. Apply the point-in-time and window filters

Keep a patent when `window_start <= available <= as_of`, where `window_start` is `as_of` minus
`lookback_years` (inclusive at both ends). Everything else lands in `not_yet_public_excluded` or
`outside_lookback_window_excluded`. The three counters plus `patents_scored` reconcile to
`records_supplied`; call `report.reconciles()` to assert it.

## 4. Build citation cohorts and adjust for truncation

Group the surviving patents by `(technology_class, availability_year)`, take each group's mean
citation count, and divide each patent's count by its cohort mean.

- Cohorts smaller than `min_cohort_size` are not trusted; their patents keep raw counts and are
  reported in `cohorts_below_min_size`. Those patents remain truncation-biased.
- A cohort whose mean is 0 (nothing cited yet) is left unadjusted rather than divided by.

## 5. Compute the two components

| Component | Definition | Why this form |
|---|---|---|
| Velocity $V_i$ | patents in window, ÷ `innovation_input` when supplied | The published predictor is patents **scaled by R&D**, not a count |
| Quality $Q_i$ | **mean** cohort-adjusted ratio per patent, optionally $\ln(1+x)$ | A mean is scale-free; a *sum* would just be the count again |

Without `innovation_inputs` the velocity term is a raw count and the report warns that the factor
carries a firm-size component. If some assets have an input and others do not, the cross-section
mixes ratios with counts and is not comparable — `assets_missing_innovation_input` lists them and
a warning is raised. Treat that as a data gap to close, not a note to skim.

## 6. Standardise, weight, winsorise — in that order

1. z-score $V$ and $Q$ separately across the universe (population sigma).
2. Weighted mean of the two z-scores, renormalised by the weight sum.
3. Re-standardise the composite.
4. Winsorise to ±`winsorize_z`.

Standardising *before* weighting is what makes `velocity_weight` and `citation_weight` mean what
they say. Weighting the raw scales instead hands the factor to whichever component has the larger
numeric range. A degenerate component (zero dispersion) contributes zeros rather than a fabricated
ranking, and the engine logs which one collapsed.

Because winsorisation is applied last, a clipped factor is unit-variance but only approximately
mean-zero.

## 7. Read the report before trusting the factor

| Field | Check |
|---|---|
| `status` | `SIGNALS_GENERATED` / `EMPTY_UNIVERSE` / `INSUFFICIENT_UNIVERSE` / `NO_DISPERSION` |
| `reconciles()` | must be `True`; `False` means records were lost upstream |
| `warnings` | scale mixing, small universe, inert winsorisation, untrusted cohorts, wide citation-read span |
| `winsorisation_can_bind` | `False` means the ±limit is decorative at this N |
| `universe_below_recommended_size` | `True` means the z-scores are coarse |
| `cohorts_below_min_size` | how many groups kept raw, truncation-biased counts |
| `citation_observation_span_days` | how far apart the citation reads were |
| `detail[asset]` | per-asset velocity, quality, both component z-scores, cohort coverage, mean claim count |

On `INSUFFICIENT_UNIVERSE`, `NO_DISPERSION` and `EMPTY_UNIVERSE`, `top_innovator` is `"NONE"` and
is not a key in `z_scores` — branch on `status` before indexing.

## 8. Roll forward

Hold the record set fixed, advance `as_of`, and re-run with citation counts re-snapshotted at each
date. The scored-patent count is monotone non-decreasing in `as_of` for a fixed window only until
older patents start leaving the trailing window — a drop there is the lookback working, not a bug.

## Note on claim counts

`claim_count` is carried, validated and reported as `detail[asset].mean_claim_count`. It is
deliberately **not** scored: no source consulted here establishes a defensible weight for claim
count in a return-predictive composite, and inventing one would be worse than omitting it. Use the
reported statistic for diagnostics, or add your own term with your own justification.
