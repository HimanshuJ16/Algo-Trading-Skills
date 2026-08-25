# Workflows for Model Family Trade-Off Selection

The engine produces a **prior**, not a result. The full procedure is therefore five
steps, and the engine is only step 3. Stopping after step 3 is the main way this skill
gets misused.

## 1. Establish the specification, and refuse to guess

Ingest modality, sample size, feature count, latency budget and governance posture.

- `modality` and `regulatory_compliance` are closed vocabularies. An unrecognised value
  **raises**; it is never defaulted. A defaulted modality previously produced an audit
  note that described data the caller did not have — the record said "raw
  high-frequency tick sequence" for an engineered tabular dataset.
- `latency_budget_us` must be finite and strictly positive. A `NaN` budget compares
  `False` against every threshold, so it would silently drop the latency constraint
  while looking like it had been applied.
- `sample_size_rows` is the count of labelled rows or sequences **actually available for
  training**, not the raw tick count you could in principle collect.
- `feature_count` is recorded in the audit record and does not affect the score. There
  is no published threshold on feature count that separates the two families; inventing
  one would make the record less honest, not more informative.

## 2. Decide whether the decision is even open

Before scoring, check whether one constraint already settles it:

- **Is the interpretability requirement absolute?** If a named human has to be able to
  reconstruct a specific order from the model's terms, the decision is made — see
  `explainable-boosting-machines-for-regulated-signals`. The engine will agree, but you
  do not need it.
- **Is the latency budget already known to be violated by one family?** Then measure,
  do not score. See `model-inference-latency-budget-for-live-trading`.
- **Is this a change to a live EU algorithm?** Then the governance path in step 5 runs
  regardless of the outcome.

## 3. Score across the five dimensions

Each dimension carries a prior (GBDT score, NN score, evidence string). The caller's
constraints choose the weights:

| Dimension | Weight when binding | Weight otherwise | Binding condition |
|---|---|---|---|
| `tabular_data_fit` | 0.40 | 0.05 | `modality == TABULAR_ENGINEERED` |
| `sequential_pattern_extraction` | 0.50 | 0.05 | `modality == RAW_HIGH_FREQUENCY_TICKS` **and** enough rows |
| `interpretability_compliance` | 0.25 | 0.05 | `regulatory_compliance == STRICT_MODEL_GOVERNANCE` |
| `inference_speed_latency` | 0.25 | 0.05 | `latency_budget_us <= 500.0` |
| `regime_shift_robustness` | 0.15 | 0.15 | always |

Weights are then normalised to sum to 1, and each family's score is the weighted sum of
its dimension priors.

- **Decision point — the sequential dimension is gated on data volume.** A deep sequence
  model can only realise its representation-learning advantage if there is enough data
  to learn a representation. Below `deep_learning_reference_rows` (default 10,000) the
  sequential dimension drops to residual weight even for tick data, and the report
  records both a decision factor and a limitation saying so. That default is anchored on
  Grinsztajn et al.'s "medium-sized data (~10K samples)" regime, which is a *tabular*
  benchmark result extended to sequence models by judgement. It is a constructor
  argument precisely because it is not a published threshold — override it if you have
  a better figure for your data.
- **Decision point — `regime_shift_robustness` is scored equally for both families and
  is therefore decision-neutral by construction.** This is deliberate, not an oversight.
  TableShift found no model family consistently better out of distribution. If you
  override this prior with an unequal pair, you are asserting a robustness advantage the
  published benchmarks do not support — be able to defend it.

## 4. Read the recommendation as a hypothesis, then run the bake-off

- `score_gap >= decision_margin` → `RECOMMEND_LIGHTGBM_XGBOOST`
- `score_gap <= -decision_margin` → `RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER`
- otherwise → `RECOMMEND_HYBRID_ENSEMBLE`

- **Decision point — `RECOMMEND_HYBRID_ENSEMBLE` means "the priors do not separate these
  families for your specification".** It is a signal to benchmark both, not a finding
  that an ensemble is optimal. An ensemble is nonetheless a real option: Shwartz-Ziv &
  Armon found "an ensemble of deep models and XGBoost performs better on these datasets
  than XGBoost alone" — but that is their benchmark result, not yours.
- **Decision point — the recommendation is not evidence.** Whichever family it names,
  the next step is a walk-forward comparison of both on your data, under transaction
  costs, with the evaluation window fixed before you look at results. See
  `walk-forward-validation-setup`, `backtesting-ml-models-against-transaction-costs` and
  `feature-engineering-without-leakage`. SR 26-2 names "a comparison of alternative
  assumptions and methodologies" as a testing activity; this engine tells you which
  alternative to put first, not which one wins.
- **Decision point — a latency-driven recommendation still has to be measured.** The
  latency dimension is a family-level prior. It does not certify that any particular
  trained model meets the budget.

## 5. Persist the record, and treat a family switch as a material change

Persist the whole `ModelFamilyTradeoffReport`: recommendation, both scores, `score_gap`,
`decision_margin`, `dimension_scores`, `applied_dimension_weights`, `dimension_evidence`,
`data_sufficiency`, `stated_limitations` and `config_fingerprint`. The fingerprint ties
the recommendation to the exact priors and thresholds that produced it, so a later
reviewer can tell a changed *decision* from a changed *configuration*.

If the recommendation is acted on in a deployed EU algorithmic trading system, ESMA's
2026 supervisory briefing (para. 31) makes it a material change: it "may alter the
behaviour, risk profile, or compliance posture" of the algorithm, and firms "are
required to timestamp, approve, and record all material changes", with retesting
required under para. 30. Route it through `model-versioning-and-rollback` and
`risk-control-configuration-change-approval-workflow` before it goes live.
