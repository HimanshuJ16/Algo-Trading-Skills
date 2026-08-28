# Pre-Flight Checklist — Patent Innovation Signal

## Point-in-time integrity (get these wrong and nothing downstream is salvageable)

- [ ] Is every patent dated by `min(pre_grant_publication_date, grant_date)` — never by
      `filing_date`, which is confidential under 35 U.S.C. 122(a)?
- [ ] Are availability dates **observed**, not computed as `filing_date + 18 months`? (A
      non-publication request under 35 U.S.C. 122(b)(2)(B)(i) means no A-publication ever; at the
      EPO the 18 months run from the *priority* date, Art. 93(1)(a) EPC.)
- [ ] Does every non-zero `forward_citations` carry the `citations_observed_asof` date it was read?
- [ ] Is that date at or before `as_of` for every record — i.e. no back-dated cross-section scored
      with today's cumulative counts?
- [ ] Is `citation_observation_span_days` small enough that the counts cover comparable exposure
      windows?
- [ ] Are pending, unpublished applications excluded (`not_yet_public_excluded`) rather than
      counted?

## Metric construction

- [ ] Are forward citations cohort-scaled by `(technology_class, availability_year)` before any
      cross-sectional comparison? (Raw counts are incomparable across fields and grant years.)
- [ ] Is `cohorts_below_min_size` acceptable — how many patents kept raw, truncation-biased counts?
- [ ] Is the quality term a **mean** per patent, not a sum? (A sum is the patent count in disguise.)
- [ ] Is velocity scaled by an `innovation_input` (R&D)? If not, do you accept that the factor
      carries a firm-size component?
- [ ] Is `assets_missing_innovation_input` empty, so the cross-section is not mixing ratios with
      raw counts?
- [ ] Are the two components standardised **before** they are weighted?

## Universe and output

- [ ] Is the universe large enough that cross-sectional z-scores mean something
      (`universe_below_recommended_size` is `False`)?
- [ ] Does `winsorisation_can_bind` say the ±limit is actually active at this N, or is it inert?
- [ ] Does `report.reconciles()` return `True`?
- [ ] Has `status` been branched on before indexing `z_scores[top_innovator]`? (It is `"NONE"` on
      `EMPTY_UNIVERSE`, `INSUFFICIENT_UNIVERSE` and `NO_DISPERSION`.)
- [ ] Have all entries in `report.warnings` been read and accepted?

## Data hygiene

- [ ] Are duplicate `patent_id`s deduplicated (one patent, many assignee rows)?
- [ ] Do NaN/Inf citation counts raise rather than silently score as zero?
- [ ] Is `technology_class` populated, rather than defaulting large numbers of patents into
      `UNCLASSIFIED`?

## Interpretation

- [ ] Is the claim being made about this factor limited to what the evidence supports — R&D-scaled
      innovative efficiency, US 1981–2006, attributed substantially to mispricing (Hirshleifer,
      Hsu & Li 2013) — rather than "patent counts predict returns"?
- [ ] Is `claim_count` being used only as a reported diagnostic, not as an unjustified score term?
