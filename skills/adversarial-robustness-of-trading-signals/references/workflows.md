# Deep Workflow Reference — adversarial-robustness-of-trading-signals

SKILL.md is the **interface contract**; this file holds the **engineering
rationale** and full procedure.

## Full procedure

### 1. Freeze the validation set

The gate is only as honest as its validation data.

```python
# X_clean must be:
#   - out-of-sample (never seen in training),
#   - frozen (checksummed and version-pinned),
#   - 2-D float, finite, aligned with the model's training feature schema.
assert X_clean.ndim == 2
assert np.all(np.isfinite(X_clean))
```

Persist the validation-set hash in the model card so a future reviewer can
reproduce the exact `RobustnessReport`. Re-running the gate on a re-sampled
validation set invalidates cross-version comparability.

### 2. Derive feature scales from the *training* set

```python
train_scales = np.ptp(X_train, axis=0)
train_scales[train_scales == 0.0] = 1.0   # guard zero-variance features
```

Pass these as `feature_scales` so ε is independent of which validation sample
is drawn. The built-in `ptp(X_validation)` fallback couples the perturbation
budget to the sample — convenient for a smoke test, wrong for governance.

### 3. Set the perturbation budget

```python
config = AdversarialRobustnessConfig(
    epsilon=0.01,                # 1% of per-feature scale; calibrate to spread
    flip_tolerance_pct=5.0,      # reject above 5% flips
    noise_type=NOISE_MONTECARLO_WORST,
    n_trials=25,                 # max flip over 25 draws
    seed=42,
    feature_scales=train_scales,
    feature_bounds=train_bounds,  # [min, max] per feature, from training set
    clip_to_clean_domain=True,
    decision_threshold=0.5,
)
```

### 4. Run the evaluation

```python
tester = SignalAdversarialTester(config)
report = tester.evaluate_model(model.predict, X_clean)
```

### 5. Read the report

```python
{
  "total_samples": 2000,
  "flipped_signals": 41,
  "vulnerability_score_pct": 2.05,
  "is_robust": true,
  "noise_type": "montecarlo_worst",
  "n_trials": 25,
  "worst_trial_index": 14,
  "flipped_indices": [37, 112, 889, ...]
}
```

- `vulnerability_score_pct` — the gating metric.
- `flipped_indices` — the actionable set: these samples sit on a flippable
  boundary. Feed them back to adversarial training as the augmentation target.
- `worst_trial_index` — which of the `n_trials` draws produced the max; useful
  for diagnosing whether a single pathological draw dominates.

### 6. Gate the deployment

```
if report.is_robust and ci_upper_bound(report) <= config.flip_tolerance_pct:
    promote(model)
else:
    reject(model)
    route_to_adversarial_training(model, report.flipped_indices)
```

The `ci_upper_bound` check is the statistical-validity guard from
`standards.md` §4: a 4.7% point estimate on 200 samples is not a clean pass.

### 7. Adversarial training (remediation path)

When the gate rejects, retrain on perturbed data:

```python
# Augment the training set with the same noise model that failed the gate.
augmented = np.vstack([X_train, perturb(X_train, config)])
y_augmented = np.concatenate([y_train, y_train])  # labels unchanged
model.fit(augmented, y_augmented)
```

This smooths the decision boundary at the flipped samples. Re-run the gate;
iterate until `vulnerability_score_pct` sits comfortably under tolerance with a
narrow confidence interval.

## Pipeline diagram

```
   Training set ──► feature_scales, feature_bounds
                         │
   Validation set ──► X_clean
                         │
                         ▼
            ┌─────────────────────────┐
            │  SignalAdversarialTester  │
            │  ┌─────────────────────┐ │
            │  │ 1. clean signals    │ │
            │  │ 2. inject noise      │ │  (uniform / random_sign /
            │  │    + clip to domain  │ │   montecarlo_worst × n_trials)
            │  │ 3. adv signals       │ │
            │  │ 4. compare → flips   │ │
            │  │ 5. worst-of-N        │ │
            │  └─────────────────────┘ │
            └────────────┬────────────┘
                         │
                         ▼
                RobustnessReport
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
       is_robust?              flipped_indices
            │                         │
      ┌─────┴─────┐                   └─► adversarial training
      ▼           ▼                       augmentation target
   promote    reject + route
```

## Failure modes & escalation

| Symptom | Probable cause | Action |
|---|---|---|
| Flip rate ~50% on a constant-boundary test set | Degenerate validation set (all samples at one boundary) | Re-test on a realistic distribution; the ~50% is a threshold artifact, not a model defect |
| `random_sign` passes, `montecarlo_worst` fails | A specific perturbation direction the single draw missed | Trust the worst-of-N; reject and retrain |
| Flip rate jumps between CI runs | `seed` is `None` (non-deterministic) | Pin `seed` in the config and model card |
| Domain clipping collapses all noise | Validation feature domain is degenerate (constant column) | Widen via `feature_bounds` or set `clip_to_clean_domain=False` for deliberate off-manifold stress |
| Flip rate is 0% on everything | Model returned probabilities but decode is wrong (class order inverted) | Verify the argmax class ordering matches BUY/SELL |
| Gate passes but model fails live | ε too small for the real microstructure noise | Re-calibrate ε to one bid-ask spread; re-run with `montecarlo_worst` |
| Flip rate confidence interval straddles the tolerance | Validation set too small | Grow the set or bootstrap the CI; do not treat a marginal pass as clean |

## Integration with the model card

Persist a snapshot of the gate result alongside the model:

```json
{
  "adversarial_robustness": {
    "seed": 42,
    "epsilon": 0.01,
    "noise_type": "montecarlo_worst",
    "n_trials": 25,
    "vulnerability_score_pct": 2.05,
    "flip_tolerance_pct": 5.0,
    "is_robust": true,
    "validation_set_hash": "sha256:...",
    "flipped_index_count": 41
  }
}
```

This makes the robustness verdict reproducible and comparable across model
versions — a regression in `vulnerability_score_pct` between versions is a
blocking promotion signal (see `model-versioning-and-rollback`).

## Production implementation reference

- Engine: `scripts/signal_adversarial_tester.py`
  (`SignalAdversarialTester`, `AdversarialRobustnessConfig`, `RobustnessReport`).
- Tests: `scripts/test_signal_adversarial_tester.py` (26 unit tests).
- Operational checklist: `assets/checklist.md`.

## Cross-references

- `feature-engineering-without-leakage` — validation-set integrity upstream of this gate.
- `reproducible-ml-training-pipelines` — the seed discipline the gate depends on.
- `model-card-documentation-for-trading-models` — where the report is persisted.
- `model-versioning-and-rollback` — version-over-version robustness regression as a block.
- `backtest-outlier-and-bad-tick-filtering` — the complementary live-side noise filter.
- `class-imbalance-handling-for-rare-signal-events` — wide CIs on rare-signal flip rates.
