# Pre-Flight Checklist — EBM / GA²M regulated signal

Sign off before an EBM-derived signal reaches production, and again after every
re-fit of the shape tables.

## Model definition

- [ ] Intercept $\beta_0$ taken from the fitted model, passed explicitly — not defaulted.
- [ ] `score_scale` set to `LOGIT` (classification) or `IDENTITY` (regression), and every
      downstream consumer converts with `logit_score_to_probability` rather than reading
      the raw score as a probability.
- [ ] `shape_table_version` identifies this specific calibration, not just the model family.
- [ ] Every univariate term $f_i$ and pairwise term $f_{ij}$ registered; each interaction
      registered exactly once (the pair is unordered).
- [ ] `required_feature_names()` matches the feature vector the live pipeline produces —
      names included.

## Monotonicity claims

- [ ] Every claim carries a direction, a scope, and an explicit `audit_grid`.
- [ ] Each grid spans the range the feature actually takes in production, tails included —
      the audit certifies nothing beyond its last point.
- [ ] For each `GLOBAL` claim: the feature appears in **no** interaction term. If it does,
      either the interaction is dropped or the claim is downgraded to `TERM` and the
      recorded limitation is accepted by whoever signs off.
- [ ] `audit_monotonicity()` runs in CI at model-registration time, not only at scoring time.

## Scoring behaviour

- [ ] A missing feature, an unknown feature name, and a non-finite value each raise — and
      no caller "fixes" them by substituting a default.
- [ ] Shape functions are pure: no cache, no RNG, no mutable state. (A stateful one fails
      the reproducibility check by design.)
- [ ] Every consumer gates on `status` before reading `total_predicted_score` — a failed
      audit still carries a score field, and it is NaN when a term returned NaN.

## Audit record

- [ ] Persisted per scored instance: `model_id`, `shape_table_version`, `term_fingerprint`,
      `score_scale`, intercept, every term contribution, `total_predicted_score`,
      `additive_identity_residual`, `status`, violations, stated limitations.
- [ ] Contributions stored unrounded; any rounding is presentation-only.
- [ ] Documentation quoting the additive-identity check describes it as a reproducibility
      and reconciliation check on the record — not as evidence the model is correct.

## Governance framing

- [ ] No document claims a regulator mandates exact feature attributions. US model-risk
      guidance (SR 26-2, 17 Apr 2026, superseding SR 11-7) states it "does not set forth
      enforceable standards or prescriptive requirements".
- [ ] Any US model-risk citation points at SR 26-2 / OCC 2026-13, not the superseded
      SR 11-7 / OCC 2011-12, and the entity is actually within scope (banking
      organizations; most relevant above $30bn in assets).
- [ ] EU explainability framing attributes the expectation correctly: Art. 2 RTS 6 as read
      by ESMA's Feb 2026 supervisory briefing, which is itself non-binding.
- [ ] Re-fitting the shape tables is handled as a material model change: new
      `shape_table_version`, re-run audit, re-test, record the change.
