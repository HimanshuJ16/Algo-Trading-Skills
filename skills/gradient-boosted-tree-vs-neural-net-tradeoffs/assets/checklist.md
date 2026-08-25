# Pre-Flight Checklist — GBDT vs Neural Net Family Selection

## Specification

- [ ] Modality is one of `TABULAR_ENGINEERED` / `RAW_HIGH_FREQUENCY_TICKS` — not a
      near-miss string. Anything else raises rather than defaulting.
- [ ] `sample_size_rows` is the count of labelled rows **available for training**, not
      the raw tick count that could in principle be collected.
- [ ] `latency_budget_us` is finite, strictly positive, and in **microseconds**.
- [ ] Governance posture uses `STRICT_MODEL_GOVERNANCE`, not the deprecated
      `STRICT_SR11_7_MIFID2` (SR 11-7 was superseded by SR 26-2 on 17 April 2026).
- [ ] Understood that `feature_count` is recorded but does not affect the score.

## Reading the output

- [ ] `data_sufficiency` checked — if `BELOW_DEEP_LEARNING_REFERENCE`, the sequential
      dimension was demoted and the deep-learning branch never really competed.
- [ ] `stated_limitations` read in full before the recommendation was acted on.
- [ ] A `RECOMMEND_HYBRID_ENSEMBLE` result understood as "the priors do not separate
      these families", not as "an ensemble is optimal".
- [ ] Any overridden prior can be defended — in particular, an unequal
      `regime_shift_robustness` prior asserts an advantage the published benchmarks do
      not support.

## Before acting on it

- [ ] Walk-forward bake-off of **both** families planned on the real dataset, under
      transaction costs, with the evaluation window fixed before results are inspected.
- [ ] Latency measured against the real budget on the real runtime — the engine's
      latency dimension is a prior, not a measurement.
- [ ] No claim is being made anywhere that a regulator mandates SHAP, EBMs, or any
      other named explainability technique. None does.
- [ ] If this is a live EU algorithmic trading system: the family switch is logged as a
      **material change** under ESMA's 2026 briefing (para. 31) — retested, timestamped,
      approved and recorded before deployment.

## Audit record

- [ ] Full report persisted, including `score_gap`, `decision_margin`,
      `applied_dimension_weights`, `dimension_evidence` and `config_fingerprint`.
- [ ] `config_fingerprint` recorded, so a later reviewer can distinguish a changed
      decision from a changed configuration.
