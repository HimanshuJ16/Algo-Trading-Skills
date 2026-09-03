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

Both conditions are also enforced by the engine (`_validate_matrix`), along with
a non-empty check on each axis. They are hard errors, not warnings: a single NaN
cell poisons the per-feature scale, hence every perturbation, and would otherwise
report a 0% flip rate and PASS. Fix the data upstream rather than filtering the
exception.

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
  "flip_rate_ci_upper_pct": 2.64,
  "ci_confidence_level": 0.95,
  "is_robust": true,
  "is_robust_at_ci": true,
  "noise_type": "montecarlo_worst",
  "n_trials": 25,
  "worst_trial_index": 14,
  "epsilon": 0.01,
  "flip_tolerance_pct": 5.0,
  "seed": 42,
  "flipped_indices": [37, 112, 889, ...]
}
```

- `is_robust_at_ci` — **the gating verdict**. True only when the flip rate *and*
  its one-sided Wilson upper bound both clear the tolerance.
- `vulnerability_score_pct` / `flip_rate_ci_upper_pct` — the point estimate and
  its confidence bound. `is_robust` reflects only the former and is retained for
  backwards compatibility; do not gate on it alone.
- `flipped_indices` — the actionable set: these samples sit on a flippable
  boundary. Feed them back to adversarial training as the augmentation target.
- `worst_trial_index` — which of the `n_trials` draws produced the max; useful
  for diagnosing whether a single pathological draw dominates.
- `epsilon`, `flip_tolerance_pct`, `seed` — echoed so the snapshot is a
  self-contained audit record without re-reading the config.

### 6. Gate the deployment

```python
if report.is_robust_at_ci:
    promote(model)
elif report.is_robust:
    # Point estimate clears, confidence bound does not: marginal, not a pass.
    escalate_for_larger_holdout(model, report)
else:
    reject(model)
    route_to_adversarial_training(model, report.flipped_indices)
```

`is_robust_at_ci` is the statistical-validity guard from `standards.md` §4: a
4.7% point estimate on 200 samples is not a clean pass. The engine also logs the
run at WARNING with a `MARGINAL:` suffix whenever `is_robust` holds but
`is_robust_at_ci` does not, so a marginal result cannot slip by as a routine
INFO-level pass line.

### 7. Adversarial training (remediation path)

When the gate rejects, retrain on perturbed data:

```python
# Augment the training set with the same noise model that failed the gate.
# tester.perturb() applies the configured epsilon, scales, domain bounds and
# projection, so the augmentation matches what the gate actually tested.
tester = SignalAdversarialTester(config)
rng = np.random.default_rng(config.seed)
augmented = np.vstack([X_train] + [tester.perturb(X_train, rng) for _ in range(3)])
y_augmented = np.concatenate([y_train] * 4)   # labels unchanged
model.fit(augmented, y_augmented)
```

Pass an explicit `rng` when drawing more than one augmentation: omitting it
re-seeds from `config.seed` and returns the *same* perturbed matrix every call.

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
| `is_robust` true but `is_robust_at_ci` false (log says `MARGINAL:`) | Validation set too small to resolve the tolerance | Grow the set to the size `standards.md` §4 requires; do not treat a marginal pass as clean |
| `ValueError: X_clean contains N non-finite value(s)` | NaN/inf in the validation matrix | Fix upstream. Pre-v1.3.0 this silently produced a 0% flip rate and an automatic PASS |
| `ValueError: model prediction contains NaN/inf` | The model emits NaN scores on some rows | A broken model, not a gate failure — a NaN score decodes to one class for clean and perturbed alike |
| Flip rate 100% with explicit `feature_bounds` | Validation samples sit far outside training-set bounds | Pre-v1.3.0 the domain clip moved them by many multiples of ε and scored it as an ε-bounded flip; now the ε ball is honoured. Investigate the train/validation shift |

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
    "flip_rate_ci_upper_pct": 2.64,
    "ci_confidence_level": 0.95,
    "flip_tolerance_pct": 5.0,
    "is_robust": true,
    "is_robust_at_ci": true,
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
- Tests: `scripts/test_signal_adversarial_tester.py` (47 unit tests).
- Operational checklist: `assets/checklist.md`.

## Cross-references

- `feature-engineering-without-leakage` — validation-set integrity upstream of this gate.
- `reproducible-ml-training-pipelines` — the seed discipline the gate depends on.
- `model-card-documentation-for-trading-models` — where the report is persisted.
- `model-versioning-and-rollback` — version-over-version robustness regression as a block.
- `backtest-outlier-and-bad-tick-filtering` — the complementary live-side noise filter.
- `class-imbalance-handling-for-rare-signal-events` — wide CIs on rare-signal flip rates.
