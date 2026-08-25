---
name: gradient-boosted-tree-vs-neural-net-tradeoffs
description: >-
  Use when choosing between gradient-boosted trees (LightGBM/XGBoost) and deep
  networks (LSTM/Transformer) for a financial ML signal — producing an
  evidence-tagged, auditable prior over dataset modality, available rows,
  inference latency and model-governance posture, as the starting hypothesis
  for an empirical bake-off.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- gradient-boosted-trees
- lightgbm
- xgboost
- neural-networks
- model-selection
- model-governance
brokers_frameworks:
- LightGBM
- XGBoost
- PyTorch / TensorFlow
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill at the point where a financial ML signal's architecture is still open:
tabular engineered features fed to a **GBDT** (LightGBM, XGBoost, CatBoost), or a raw
tick/order-book sequence fed to a **deep network** (LSTM, Transformer, TFT). The choice
is usually made by habit or by whichever paper was read most recently, and the cost of
getting it wrong is paid months later in training time, inference latency and
unexplainable attributions.

This module turns that choice into a record. It scores both families across five
dimensions, weights them by the constraints you actually have, and emits a
`ModelFamilyTradeoffReport` carrying the recommendation, the weights applied, the
evidence behind each prior, and the limitations of the whole exercise.

**What it produces is a prior, not a result.** It says which family to benchmark first.
It does not say which will perform better on your data — only a walk-forward bake-off
does that. Federal Reserve SR 26-2 names "a comparison of alternative assumptions and
methodologies" as a testing activity; this engine chooses the alternative, not the
winner.

## When NOT to Use

- **As evidence that one family is better.** Every score in this engine is a documented
  prior drawn from published benchmarks on *other people's datasets*. A recommendation
  is a hypothesis to test, and every report says so in `stated_limitations`.
- **To certify a latency budget.** The `inference_speed_latency` dimension is a
  family-level prior and is explicitly low-confidence: latency is governed by model
  size, batch size and runtime, not by family. A 5,000-tree LightGBM model is not faster
  than a small MLP under ONNX Runtime. Measure it — see
  `model-inference-latency-budget-for-live-trading`.
- **To claim a regulatory obligation.** No regulator mandates SHAP, EBMs, or any named
  explainability technique. `references/standards.md` quotes what SR 26-2 and the ESMA
  briefing actually say, including their own statements that they are non-binding.
- **To argue that GBDTs are robust to regime shift.** They are not known to be. The
  `regime_shift_robustness` prior is deliberately equal for both families and therefore
  cannot tilt the result — TableShift found no family consistently better out of
  distribution. Trees additionally cannot extrapolate outside their training range,
  which is exactly what a regime shift produces.
- **When the interpretability requirement is absolute.** If a reviewer must reconstruct
  a specific order from the model's own terms, the decision is already made — go to
  `explainable-boosting-machines-for-regulated-signals`.
- **As portfolio or strategy design.** This picks a function approximator. It says
  nothing about whether the features are leak-free or the labels are meaningful — see
  `feature-engineering-without-leakage` and `synthetic-labels-from-triple-barrier-method`.

## Prerequisites

- `modality` — `TABULAR_ENGINEERED` or `RAW_HIGH_FREQUENCY_TICKS`. A closed vocabulary:
  an unrecognised value raises rather than defaulting.
- `sample_size_rows` — labelled rows **available for training**, not the tick count you
  could collect. Gates the deep-learning branch.
- `feature_count` — recorded in the audit record; **does not affect the score.** No
  published threshold on feature count separates the two families, and inventing one
  would make the record less honest.
- `latency_budget_us` — inference budget in microseconds; must be finite and strictly
  positive.
- `regulatory_compliance` — `STRICT_MODEL_GOVERNANCE` or `INTERNAL_RESEARCH`. The legacy
  `STRICT_SR11_7_MIFID2` is still accepted, logged as deprecated, and canonicalised:
  SR 11-7 was superseded by SR 26-2 on 17 April 2026, and the old value also merged a US
  banking reference with an EU investment-firm one.

## Workflow

1. **Build the specification — the engine refuses to guess.**
   Unrecognised modality or governance values raise; so do non-finite or non-positive
   latency budgets and non-positive counts.
   - **Decision point — an unrecognised modality is never defaulted.** It used to be:
     any string that was not exactly `TABULAR_ENGINEERED` fell through to the sequential
     branch, so a dataset labelled `"TABULAR"` produced a governance record asserting
     "raw high-frequency tick sequence favours deep-network representation learning" —
     the opposite of the caller's data. A wrong justification in an audit record is
     worse than no record.
   - **Decision point — a `NaN` latency budget must raise, not pass.** `nan <= 500.0` is
     `False`, so a `NaN` budget silently drops the latency constraint while the report
     still looks fully evaluated.

2. **Score five dimensions, weighted by the binding constraints.**
   Priors (GBDT / NN), each carrying its evidence string into the report:
   - **Tabular data fit**: 9.5 / 5.5 — Grinsztajn et al. 2022; Shwartz-Ziv & Armon 2022.
   - **Sequential pattern extraction**: 3.0 / 9.5 — the tabular results are scoped to
     tabular data; raw tick sequences are the other regime.
   - **Interpretability**: 9.0 / 4.0 — TreeSHAP computes exact tree attributions in
     polynomial time; deep attribution is estimated. A *tractability* gap, not a legal one.
   - **Inference speed**: 9.0 / 5.0 — **low confidence**, directional only.
   - **Regime-shift robustness**: 6.0 / 6.0 — **deliberately equal**, so it cannot move
     the result.

   Weights: 0.40 tabular / 0.50 sequential / 0.25 governance / 0.25 latency when that
   constraint binds, 0.05 when it does not; 0.15 regime always; then normalised to 1.
   The latency constraint binds at `latency_budget_us <= 500.0`.

   - **Decision point — the sequential dimension is gated on data volume.** Below
     `deep_learning_reference_rows` (default 10,000) it drops to residual weight even
     for tick data: a deep sequence model cannot realise a representation-learning
     advantage without enough data to learn a representation. That default is anchored
     on Grinsztajn et al.'s "~10K samples" *tabular* regime and extended to sequence
     models by judgement — it is a constructor argument because it is not a published
     threshold.

3. **Compare `score_gap` against `decision_margin` (default 1.0).**
   - `score_gap >= margin` → `RECOMMEND_LIGHTGBM_XGBOOST`
   - `score_gap <= -margin` → `RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER`
   - otherwise → `RECOMMEND_HYBRID_ENSEMBLE`
   - **Decision point — `score_gap` is derived from the unrounded scores, not by
     subtracting the two published ones.** Subtracting two independently-rounded scores
     leaves 0.01 of slack, which is enough to let an equally-scored dimension shift the
     result. Both `score_gap` and `decision_margin` are in the report, so the
     recommendation follows from the record.
   - **Decision point — hybrid means "these priors do not separate the families".** Not
     "an ensemble is optimal". It is a signal to benchmark both.

4. **Run the bake-off the recommendation is only a prior for.** Walk-forward, both
   families, under transaction costs, evaluation window fixed before results are read.

5. **Persist the report, and treat a family switch as a material change.** In a
   deployed EU algorithmic trading system, swapping GBDT for LSTM "may alter the
   behaviour, risk profile, or compliance posture" of the algorithm, so it must be
   retested, timestamped, approved and recorded.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the recommendation as a benchmark result.** These scores come from
  published benchmarks on other datasets. The engine ranks hypotheses; it cannot rank
  models it has never seen fitted on data it has never seen.
- **Citing SR 11-7 as the governing model-risk standard.** SR 26-2 (17 April 2026)
  "supersedes and replaces SR letter 11-7". SR 26-2 also states it "does not set forth
  enforceable standards or prescriptive requirements" and addresses banking
  organizations — largely those above $30bn in assets, not proprietary trading firms.
- **Asserting that a regulator requires SHAP.** None does. SR 26-2 mentions
  "interpretability measures" once, as one *optional* validation assessment alongside
  "benchmarking to other models"; SHAP appears nowhere in it. The ESMA briefing derives
  an explainability expectation from a compliance-staff-competence article and names no
  technique — and says of itself that it is "non-binding and not subject to a 'comply
  or explain' mechanism".
- **Assuming GBDTs survive regime shifts better.** TableShift, across 15 shift tasks and
  19 model types, found "no model consistently outperforms the standard tabular
  baselines of XGBoost, LightGBM, or CatBoost" — and no technique eliminated the gaps.
  Worse for the intuition: tree predictions are "piecewise constant approximations, and
  therefore they are not good at extrapolation", so a feature moving beyond its training
  range saturates a GBDT rather than extrapolating. A regime shift is precisely that move.
- **Inferring latency from model family.** Tree-versus-network says little; trees × depth
  × features × runtime says a lot. A deep multi-layer LSTM will not hit a sub-100µs
  budget without specialised runtimes, but neither will a 5,000-tree ensemble.
- **Forcing a Transformer onto engineered tabular features.** Longer training, more
  tuning, and — per both tabular benchmarks — usually worse results. XGBoost "requires
  much less tuning" for the same or better accuracy.
- **Running a deep sequence model on a few thousand rows.** The representation-learning
  advantage needs data to realise. The engine gates this and records
  `data_sufficiency = BELOW_DEEP_LEARNING_REFERENCE`, but the gate is only as good as
  the reference figure you set.
- **Letting a silent enum typo pick the model family.** Covered in the workflow, and
  worth repeating: the failure was silent, produced a confident recommendation, and
  wrote an inverted justification into the audit record.

## Verification

- Instantiate `ModelFamilySelectorEngine()`. **Scenario 1** — `TABULAR_ENGINEERED`,
  $N=100{,}000$, $M=50$, $200\mu s$, `STRICT_MODEL_GOVERNANCE`: weights before
  normalisation are $0.40/0.05/0.25/0.25/0.15$ (total $1.10$), so
  GBDT $= 9.35/1.10 = 8.50$ and NN $= 5.825/1.10 = 5.30$, giving
  `RECOMMEND_LIGHTGBM_XGBOOST`.
- **Scenario 2** — `RAW_HIGH_FREQUENCY_TICKS`, $N=5{,}000{,}000$, $20{,}000\mu s$,
  `INTERNAL_RESEARCH`: weights $0.05/0.50/0.05/0.05/0.15$ (total $0.80$), so
  GBDT $= 4.72$ and NN $= 7.97$, giving `RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER`.
- **Scenario 3 — starved sequence data.** The same tick spec with $N=5{,}000$ demotes
  the sequential dimension to residual weight: GBDT $= 6.93$, NN $= 6.00$, gap $0.93$,
  inside the default margin, so `RECOMMEND_HYBRID_ENSEMBLE` with
  `data_sufficiency = BELOW_DEEP_LEARNING_REFERENCE`.
- Verify the report reconstructs itself: $\sum_d \text{dimension\_scores}[d] \times
  \text{applied\_dimension\_weights}[d]$ reproduces both published scores, and the
  weights sum to $1$.
- Verify `regime_shift_robustness` is decision-neutral: replacing its prior with any
  other *equal* pair leaves `score_gap` and the recommendation unchanged.
- Negative checks that must **raise**: modality `"TABULAR"`; compliance `"SR11-7"`;
  `latency_budget_us` of `NaN`, `inf`, `0.0` or `-1.0`; `sample_size_rows` or
  `feature_count` of `0` or a `bool`; a spec mutated into an invalid state after
  construction; a non-`DatasetSpec` argument; and engine configs with an incomplete
  prior set, an out-of-range or non-finite prior, a negative `decision_margin`, or a
  `deep_learning_reference_rows` below 1.
- Run `python -m unittest discover -s skills/gradient-boosted-tree-vs-neural-net-tradeoffs/scripts`
  and confirm all tests pass.

## Related Skills

- `explainable-boosting-machines-for-regulated-signals`
- `model-inference-latency-budget-for-live-trading`
- `walk-forward-validation-setup`
- `backtesting-ml-models-against-transaction-costs`
- `feature-engineering-without-leakage`
- `model-versioning-and-rollback`
- `model-card-documentation-for-trading-models`
