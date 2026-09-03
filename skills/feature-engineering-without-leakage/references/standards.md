# Standards & Framework Coverage — feature-engineering-without-leakage

| Framework / Library | Relevance to this skill |
|---|---|
| Python `pandas` | `shift()` sign convention, `merge_asof(direction='backward', allow_exact_matches=...)`, rolling/expanding window causality, `rank()` for the separation test. |
| Python `numpy` | Tolerant float comparison (`isclose(..., equal_nan=True)`) for the prefix-invariance test. |
| Financial ML frameworks (scikit-learn, LightGBM, XGBoost) | Where whole-sample `fit_transform` preprocessing introduces the leakage class that the causality screen detects and the correlation screen cannot. |

No third-party dependency beyond `pandas` and `numpy` is required.

## Method Sources

- **Kaufman, S., Rosset, S., Perlich, C., Stitelman, O.**, "Leakage in Data Mining: Formulation, Detection, and Avoidance," *ACM Transactions on Knowledge Discovery from Data* 6(4), Article 15, December 2012, DOI [10.1145/2382577.2382579](https://dl.acm.org/doi/10.1145/2382577.2382579).
  - §3.2 defines the **no-time-machine requirement** — a legitimate model builds only "on features with information from a time earlier (or sometimes, no later) than that of the target" — which is the rule this skill enforces.
  - §4.1 makes timestamps the concrete implementation of legitimacy: "legitimacy tags are time-stamps with sufficient precision," which is why the as-of merge joins on publication time.
  - §5 is the basis for this skill's central limitation: the detection methods it surveys (EDA, surprising model performance, early field testing) "all require some degree of domain knowledge," and the paper therefore "place[s] an emphasis on leakage avoidance during data collection, where we have more control over the data." The screens here are candidate filters, not proofs of absence.
- **pandas documentation**, [`pandas.merge_asof`](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html): a `'backward'` search "selects the last row in the right DataFrame whose 'on' key is less than or equal to the left's key"; `allow_exact_matches` defaults to `True`; both frames "must be first sorted by the merge key in ascending order."

## Audit Limitations (read before signing off)

The three screens in `scripts/feature_audit.py` are candidate filters. A report with zero
findings means no screen fired — not that the feature set is legitimate. Specifically:

1. **Weak leakage passes.** Next-period financial returns are near-unpredictable, so a real leak can sit below `correlation_threshold`.
2. **Restated raw history passes.** Adjusted closes and backfilled fundamentals are causal *within* the delivered frame; every screen passes. Only the publication timestamp reveals them.
3. **Sparse forward reaches can pass.** A truncation cut only exposes a forward dependency that crosses that cut.
4. **Destroyed row order passes.** A shuffled-then-reindexed frame is indistinguishable from a sorted one.
5. **Train/test contamination is out of scope** — see `walk-forward-validation-setup` and `hyperparameter-tuning-without-target-leakage`.

## Regulatory & Operational Notes

**No securities regulator prescribes a data-leakage test for an ML trading model.** Leakage
auditing is a model-risk and research-integrity control, not a filing obligation. Where a
firm is already subject to one of the following, this audit is evidence supporting it:

- **Model risk governance — US banking organizations.** Federal Reserve **SR 11-7** / OCC Bulletin **2011-12**, *Guidance on Model Risk Management* (4 April 2011). It requires that validation include "Outcomes Analysis … comparing model outputs to corresponding actual outcomes," of which "Back-testing is one form … that involves the comparison of actual outcomes with model forecasts **during a sample time period not used in model development**." A leaked feature set defeats that comparison, because the "not used in model development" separation no longer holds in substance. SR 11-7 also expects "documentation of model development and validation that is sufficiently detailed to allow parties unfamiliar with a model to understand how the model operates, as well as its limitations and key assumptions" — which is what the sign-off checklist and this limitations section produce. *Applicability: Federal Reserve-supervised banking organizations and OCC-supervised national banks. It is not binding on an independent trading firm, fund, or individual, though it is widely adopted as a reference framework.*
- **AI/ML use reporting — India.** SEBI Circular **SEBI/HO/MIRSD/DOS2/CIR/P/2019/10** (4 January 2019), *Reporting for Artificial Intelligence (AI) and Machine Learning (ML) applications and systems offered and used by market intermediaries*, establishes a periodic reporting obligation for registered market intermediaries. *It is a reporting framework; it does not prescribe leakage testing. Confirm the current reporting template and scope directly with SEBI/the exchanges before relying on this — the field-level requirements were not verified here and have been revised since 2019.*

Claims about CFTC quantitative-model documentation requirements previously carried in this
file could not be substantiated against any primary source and have been removed.
