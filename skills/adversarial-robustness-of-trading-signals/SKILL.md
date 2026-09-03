---
name: adversarial-robustness-of-trading-signals
description: Pre-deployment governance gate that measures how often an ML trading
  signal can be flipped by epsilon-bounded feature perturbations (FGSM-lite / market
  microstructure noise). Deterministic, domain-clipped, multi-noise-model robustness
  tester producing a vulnerability score with a Wilson confidence bound and
  flipped-sample indices. Rejects models whose signal flips faster than the
  configured tolerance before they go live.
domain: algorithmic-trading
subdomain: financial-ml-robustness
tags:
- ml
- trading
- adversarial-robustness
- fgsm
- signal-processing
- model-governance
- pre-deployment-gate
brokers_frameworks:
- scikit-learn
- numpy
jurisdictions: [global]  # technique is jurisdiction-agnostic
version: "1.3.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill **before promoting any ML trading-signal model to production**
— Random Forests, Gradient Boosted Trees, deep nets, or any model exposing a
`predict(X)` callable. Financial microstructure is adversarial in the technical
sense: a flash crash, a spoofing layer, or genuine quote noise can perturb order-
book features by less than one bid-ask spread and flip a model's signal from BUY
to SELL. A model that flips under such noise is unsafe to trade.

The skill produces a `SignalAdversarialTester` that injects epsilon-bounded
perturbations into the validation feature matrix and reports:

1. The **vulnerability score** — `% of samples whose signal flips`, with a
   one-sided Wilson upper confidence bound on that rate.
2. The **flipped-sample indices** — so quant research can attribute *which*
   inputs sit on the decision boundary.
3. A **deployment verdict** — robust / vulnerable against a tolerance threshold,
   in two strengths: `is_robust` (point estimate) and `is_robust_at_ci` (the
   confidence bound also clears the tolerance). Gate on the latter.

## When NOT to Use

- **Differentiable models requiring a *true* worst-case bound.** This engine is a
  black-box, gradient-free tester. For a differentiable net, real FGSM/PGD gives a
  tighter (and actually worst-case) attack: `adv_x = x + ε·sign(∇ₓ L)`. Use a
  dedicated adversarial-robustness library (CleverHans, Foolbox, ART) instead, then
  feed that attack's flip rate into this governance gate if you want a uniform
  scoring contract.
- **Provable-robustness requirements.** This is *empirical* robustness — it can
  only show a model is *vulnerable*, never prove it is *robust*. For a certified
  L2 radius use Cohen-style randomized smoothing (see `references/standards.md` §3).
- **Non-ML signals** (pure rule-based / threshold strategies with no learned
  component). Adversarial perturbation of a fixed threshold is just sensitivity
  analysis; use `backtest-parameter-sensitivity-analysis` instead.
- **Production-time / online monitoring.** The perturbation sweep is O(n_trials)
  expensive and assumes a static validation set. For live drift monitoring use
  `concept-drift-vs-staleness-differentiation` / `model-staleness-detection`.
- **Insufficient validation data.** With < ~1000 out-of-sample samples the flip
  rate confidence interval is wide; a "pass" near the tolerance is statistically
  meaningless. Bootstrap or use a larger holdout.

## Prerequisites

- Python 3.9+, `numpy`.
- A **trained model** exposing `predict(X)` returning one of:
  - integer/boolean 1D labels (used directly as the signal),
  - a 1D float score (split at `decision_threshold`, default 0.5), or
  - a 2D probability matrix (signal = `argmax` over columns).
- A **representative out-of-sample validation set** `X_clean` (2D non-empty,
  all-finite array — NaN/inf are rejected, see Pitfalls). Never test on training
  data — the model has seen it and will appear artificially robust.
- A **calibrated epsilon** — the perturbation budget. Default 0.01 (1% of
  per-feature scale); for equities, set this to one average bid-ask spread as a
  fraction of price (see `references/standards.md` §1).
- (Recommended) **Training-set feature scales** — pass `feature_scales` so the
  test is independent of the validation sample drawn (the ptp fallback couples ε
  to the sample).

## Workflow

1. **Assemble the validation set.** Pull a frozen, out-of-sample `X_clean` aligned
   with the model's training feature schema. Confirm it is 2D and finite.

2. **Configure the perturbation budget.** Construct an `AdversarialRobustnessConfig`:

   ```python
   from signal_adversarial_tester import (
       SignalAdversarialTester, AdversarialRobustnessConfig, NOISE_RANDOM_SIGN,
   )

   config = AdversarialRobustnessConfig(
       epsilon=0.01,               # 1% of per-feature scale
       flip_tolerance_pct=5.0,    # reject if > 5% of signals flip
       noise_type=NOISE_RANDOM_SIGN,   # full-magnitude random-sign stress
       n_trials=25,                # for montecarlo_worst only
       seed=42,                    # reproducible (reproducible-ml-pipelines)
       feature_scales=train_scales,   # from training set, not validation
       feature_bounds=train_bounds,   # [min, max] per feature for clipping
       clip_to_clean_domain=True,
       decision_threshold=0.5,
   )
   ```

3. **Run the evaluation:**

   ```python
   tester = SignalAdversarialTester(config)
   report = tester.evaluate_model(model.predict, X_clean)
   ```

4. **Interpret the verdict:**
   - `report.is_robust_at_ci == True` → the flip rate *and* its 95% Wilson upper
     bound clear the tolerance. This is the promotion signal; the model may
     proceed *subject to the other gates in your pipeline*.
   - `report.is_robust == True` but `is_robust_at_ci == False` → **marginal**.
     The point estimate clears the tolerance but the validation set is too small
     to resolve it. Do not promote: grow the holdout (see `standards.md` §4).
   - `report.is_robust == False` → reject. Route the model back to quant research
     for **adversarial training** (retrain on perturbed data to smooth the decision
     boundary), feature re-engineering, or a higher decision threshold. Use
     `tester.perturb(X_train, rng)` to generate the augmentation with the same
     noise model the gate failed on.

5. **Attribute the fragility.** `report.flipped_indices` identifies exactly which
   validation samples sit on a flippable boundary — the actionable set for a
   targeted adversarial-training augmentation.

## Decision Points

| Situation | Action |
|-----------|--------|
| `vulnerability_score_pct` just under tolerance (e.g. 4.7% vs 5%) | Check `is_robust_at_ci`. If the Wilson upper bound does not clear the tolerance the pass is statistically marginal — grow the validation set; do not promote. |
| Explicit `feature_bounds` are narrower than the validation range | Samples outside the bounds are held to the ε ball, not clipped all the way back — otherwise a multi-ε clip would be scored as an ε-bounded flip. Prefer bounds that actually contain the holdout. |
| `ValueError: X_clean contains N non-finite value(s)` | A NaN/inf cell would poison the per-feature scale and silently zero the flip rate. Drop or impute the rows upstream; do not disable the check. |
| `random_sign` passes but `montecarlo_worst` (n_trials=50) fails | The model is fragile to *some* perturbation direction that a single draw missed. Trust the worst-of-N bound; reject. |
| Flips concentrate in a feature subset (inspect `flipped_indices` → rows) | The model is over-reliant on a few fragile features. Re-engineer or drop them rather than globally retrain. |
| Flip rate is ~50% on a boundary-clustered set | Expected for a sharp threshold at the boundary — not a bug. Re-test on a *realistic* distribution, not a degenerate constant. |
| Model returns probabilities, not labels | Handled automatically (argmax over a 2D array). Verify the class ordering matches your BUY/SELL convention. |
| Need a *true* worst-case bound (not empirical) | This engine cannot provide it. Graduate to FGSM/PGD (differentiable models) or certified randomized smoothing. |
| `clip_to_clean_domain=True` collapses all noise | The validation feature domain is degenerate (constant column). Either widen the domain via `feature_bounds` or set `clip_to_clean_domain=False` for stress tests that deliberately leave the manifold. |
| Reproducibility required across CI runs | Always set `seed`. `None` makes the test non-deterministic and unfit for governance gating. |

## Common Pitfalls

- **Treating `random_sign` as worst-case.** The legacy `worst_case_sign` name was
  a misnomer — the signs are *random*, not gradient-derived. Worst-case directional
  noise needs the loss gradient (FGSM). `montecarlo_worst` gives a *lower bound*
  on the worst case by taking the max over N random trials; it is tighter than a
  single draw but still not a true worst case. Read the verdict accordingly.
- **Testing on training data.** The model has memorised its training set; flip
  rates will be artificially low and the gate is meaningless. Always use a frozen
  out-of-sample holdout.
- **Epsilon not calibrated to microstructure.** A blanket `epsilon=0.01` is wrong
  when features have wildly different scales (price in $ vs a normalized ratio in
  [0,1]). Either pass per-feature `feature_scales` (training-set) or calibrate ε
  to one bid-ask spread per instrument.
- **No domain clipping.** Perturbing a price feature to a negative value, or a
  normalized ratio above 1, produces *infeasible* inputs that overstate
  vulnerability (the model never sees them in production). Use
  `clip_to_clean_domain=True` or explicit `feature_bounds`.
- **Gating on the point estimate alone.** A 0% flip rate on 50 samples has a 95%
  Wilson upper bound of 5.13% — it cannot clear a 5% tolerance. `is_robust`
  answers "did the point estimate clear?"; only `is_robust_at_ci` answers "did
  the *evidence* clear?". Promote on the latter.
- **NaN in the validation set.** Before v1.3.0 a single NaN cell made the
  per-feature `ptp` NaN, which made every perturbation NaN, which decoded to one
  class for clean and adversarial alike — a silent 0% flip rate and an automatic
  PASS. Non-finite inputs and non-finite model outputs are now hard errors; treat
  them as data-quality defects, not as things to work around.
- **Non-deterministic governance.** A deployment gate that flips verdicts between
  CI runs is worse than no gate. Always set `seed`; pin it in the model card.
- **Single noise model.** `uniform` is average-case; it can pass a model that
  `random_sign` or `montecarlo_worst` fails. Run at least `random_sign` for
  governance; prefer `montecarlo_worst` for the tighter bound.
- **Ignoring the probability path.** A model returning 2D probabilities is decoded
  via `argmax`; a 1D float score via `decision_threshold`. Verify the decode
  matches your signal convention before trusting the flip count.
- **Using Gaussian noise only.** Gaussian samples rarely reach the ε boundary, so
  they *under*-state vulnerability versus `random_sign` (which always hits the
  boundary). Do not use Gaussian as the sole stress.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/adversarial-robustness-of-trading-signals/scripts -v
```

What they assert:

- A robust model passes under `uniform` noise.
- A fragile model fails under `random_sign` and under `montecarlo_worst`.
- `montecarlo_worst` reports the max flip rate across `n_trials` (≥ any single trial).
- Same `seed` → identical report and `flipped_indices` (determinism).
- 2D probability matrices decode via `argmax`; 1D float scores via `decision_threshold`.
- Domain clipping suppresses infeasible perturbations (both implicit and explicit bounds).
- Explicit `feature_scales` collapse a huge ε; zero-variance features fall back to scale 1.0.
- Config validation rejects negative ε/tolerance, invalid `noise_type`, `n_trials < 1`,
  non-positive `feature_scales`, mis-shaped `feature_bounds`, inverted bounds,
  bad `batch_size`, out-of-range `ci_confidence_level`; ε=0 logs a vacuous-gate warning.
- Inputs that used to yield a silent PASS now raise: non-finite `X_clean`,
  zero-sample or zero-feature matrices, non-finite model output, higher-rank
  model output (which previously corrupted `flipped_indices`).
- The domain clip never moves a sample further than ε·scale, so an out-of-domain
  sample cannot manufacture a flip.
- `wilson_upper_bound` matches values derived independently by solving the score
  equation, and stays non-zero at zero flips.
- A 0-flip run on 50 samples is `is_robust` but not `is_robust_at_ci`; the same
  result on 5000 samples clears both.
- Categorical (string) class labels are compared directly instead of raising.
- `perturb()` honours the ε budget and the feasible domain, and is seeded.
- Legacy `worst_case_sign` alias routes to `random_sign` (deprecation logged).
- `report.as_dict()` round-trips through JSON and carries `seed`, `epsilon` and
  `flip_tolerance_pct` for the model card.
- `batch_size` chunking is byte-identical to whole-array evaluation.
- Non-2D input raises `ValueError`.

Confirm with the operational checklist in `assets/checklist.md` before promoting.

## Success Criteria

An adversarial-robustness gate is **healthy in production** when:

1. Every model promotion produces a `RobustnessReport` persisted to the model
   card with `seed`, `epsilon`, `noise_type`, `n_trials`, and `vulnerability_score_pct`.
2. Promotion is gated on `is_robust_at_ci`, not on `vulnerability_score_pct`
   alone. A 5% tolerance needs roughly ≥ 2000 samples before a realistic
   observed rate can clear its own confidence bound (`standards.md` §4).
3. `montecarlo_worst` with `n_trials ≥ 25` is the default governance noise model;
   `uniform`/`random_sign` single-draw results are recorded but not gating.
4. `epsilon` is calibrated per instrument to one average bid-ask spread and the
   calibration is documented (not a blanket 0.01).
5. Rejected models are routed to adversarial training with the `flipped_indices`
   attached as the augmentation target set.
6. The gate is deterministic across CI runs (fixed `seed`); a re-run yields the
   same verdict.

## Related Skills

- `feature-engineering-without-leakage` — the validation set fed to this gate must
  be leak-free or the robustness verdict is invalid.
- `backtest-outlier-and-bad-tick-filtering` — adversarial perturbations and bad-tick
  outliers are two faces of the same noise; filter the latter, stress the former.
- `reproducible-ml-training-pipelines` — the seed discipline this gate depends on.
- `model-card-documentation-for-trading-models` — persist the RobustnessReport
  (seed, ε, noise model, vulnerability score) into the model card.
- `model-versioning-and-rollback` — a regression in vulnerability score between
  versions should block the promotion.
- `backtest-parameter-sensitivity-analysis` — for non-ML / rule-based strategies,
  where adversarial perturbation reduces to parameter sensitivity.
- `concept-drift-vs-staleness-differentiation` — live complement: this gate is
  pre-deployment; drift monitoring is post-deployment.
- `class-imbalance-handling-for-rare-signal-events` — rare-signal models have few
  positive samples to flip; the flip-rate CI is correspondingly wide.
