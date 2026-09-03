# Financial-ML Robustness Standards — adversarial-robustness-of-trading-signals

## 1. Epsilon calibration — one bid-ask spread

The perturbation budget `epsilon` is the single most important knob. It must
represent a **plausible, realizable** noise floor, not an arbitrary number.

| Instrument class | Recommended `epsilon` (as fraction of feature scale) | Rationale |
|---|---|---|
| Large-cap US equity (price feature) | Half the average bid-ask spread / price | LULD collars and one-tick moves are the realistic noise. |
| Mid / small-cap equity | Full average spread / price | Wider spreads; perturbations of a full spread are realistic. |
| FX major pair | 0.5–1 pip / price | Tick noise at the pip level. |
| Crypto top-tier | 1–2 bps | Volatile microstructure, but top books are tight. |
| Normalized ratio feature ∈ [0, 1] | 0.01–0.02 | Scale-free; ε is already a fraction. |

**Rule of thumb:** ε should be the largest perturbation that an attacker (or a
flash crash) could plausibly introduce *without* itself being arbitraged away.
Larger ε over-states vulnerability; smaller ε gives a false sense of safety.

Always pair ε with **per-feature `feature_scales`** derived from the *training*
set, so the test is independent of which validation sample is drawn. The
built-in `ptp(X_validation)` fallback is convenient but couples ε to the sample.

## 2. Noise models and their guarantees

| `noise_type` | What it is | Guarantee | Cost | Use for |
|---|---|---|---|---|
| `uniform` | Random noise in `[-ε, +ε]·scale` per cell | Average-case stress | 1× predict | Baseline / smoke test |
| `random_sign` | `±ε·scale` with random per-cell sign (always full magnitude) | Stronger average-case than `uniform`; **not** worst-case | 1× predict | Default governance stress |
| `montecarlo_worst` | Max flip rate over `n_trials` `random_sign` draws | One-sided **lower bound** on the true worst case | `n_trials`× predict | Tighter black-box governance |
| (FGSM / PGD) | `x + ε·sign(∇ₓ L)`, iterated + projected | **True worst case** within the L∞ ball | 1× grad (+iters) | Differentiable models — use an external library, then feed the flip rate into this gate |

### Why `random_sign` is not worst-case

Worst-case directional noise pushes **every** feature in the direction that
maximizes the loss — which requires the loss gradient. `random_sign` picks
directions uniformly at random; for a high-dimensional feature space the chance
it picks the worst direction is exponentially small. `montecarlo_worst` tightens
the bound by taking the max over many random draws, but it remains a **lower
bound** on the true worst case. Read a `montecarlo_worst` pass as "not found
vulnerable in N trials", not "robust".

### Projection: the eps ball and the feasible domain

A perturbation is projected onto the **intersection** of the L-infinity ball
`[x - eps*scale, x + eps*scale]` and the feasible domain (`feature_bounds`, or
the observed validation range when `clip_to_clean_domain=True`) — the same
convention PGD uses.

The domain clip alone is not enough. `feature_bounds` are meant to come from the
*training* set, and a validation sample can legitimately sit outside them; a
plain clip would then drag that sample back by far more than `eps*scale`, and any
resulting signal change would be scored as an epsilon-bounded flip even though
the move was orders of magnitude larger. When the two constraints conflict — i.e.
the clean sample is already outside the domain — **the epsilon budget wins**, and
the perturbed point stays within `eps*scale` of the clean point. The budget is
the invariant the verdict actually claims to test.

If many samples fall outside `feature_bounds`, that is a data problem to fix
upstream (train/validation distribution shift), not something to configure around.

### The legacy `worst_case_sign` alias

The original implementation named its random-sign model `worst_case_sign`.
That name was inaccurate and misleading to operators. It is accepted for
backwards compatibility and silently routed to `random_sign`, with a
deprecation warning logged. New code must use the honest names.

## 3. From empirical to certified robustness

This engine provides **empirical** robustness: it can demonstrate a model is
*vulnerable*, but it can never *prove* a model is robust — an untested
perturbation direction might still flip it. The stronger guarantee is
**certified** robustness.

**Cohen, Rosenfeld & Kolter (2019), "Certified Adversarial Robustness via
Randomized Smoothing."** Add Gaussian noise `N(0, σ²I)` to each input, classify
by the most probable class under the noisy base classifier, and certify an
L2 ball of radius:

```
R = (σ/2) · (Φ⁻¹(p_A) − Φ⁻¹(p_B))
```

where `p_A` is the probability of the top class and `p_B` the runner-up under
the smoothed classifier. Within radius `R` the prediction is *provably* stable.
This is a fundamentally different (and stronger) guarantee than the empirical
flip rate here. When a governance program requires a *provable* robustness
radius — e.g. for a regulated signal — graduate from this gate to a randomized-
smoothing certifier and record both the certified radius and the empirical
flip rate in the model card.

## 4. Statistical validity of the flip rate

The vulnerability score is a binomial proportion: `flipped / total` samples. A
point estimate under the tolerance is **not** a pass — the question is whether
the *true* flip rate is under the tolerance, which is a one-sided question about
an unobserved parameter.

The engine therefore reports `flip_rate_ci_upper_pct`, a one-sided **Wilson
(1927) score** upper confidence bound at `ci_confidence_level` (default 0.95),
and the stricter verdict `is_robust_at_ci`. Wilson is used rather than the normal
(Wald) approximation because it stays inside `[0, 1]` and keeps sensible coverage
at small flip counts — the regime a robustness gate actually lives in. A Wald
bound at zero flips has zero width and would wrongly certify any model.

Note the bound is genuinely one-sided: a one-sided 95% bound is *not* the upper
end of a two-sided 95% interval (that would be a one-sided 97.5% bound).

### How much validation data a tolerance needs

For a **5% tolerance**, the largest observed flip rate whose one-sided 95% Wilson
upper bound still clears 5%:

| Validation size | Max observed flip rate that clears the gate | Verdict confidence |
|---|---|---|
| 100 | 1.00% (1 flip) | Unusable — only a near-zero rate can pass |
| 200 | 2.00% (4 flips) | Unreliable |
| 500 | 3.20% (16 flips) | Borderline |
| 1000 | 3.80% (38 flips) | Usable |
| 2000 | 4.15% (83 flips) | Reliable for a 5% gate |
| 5000 | 4.48% (224 flips) | Comfortable |
| 10000 | 4.64% (464 flips) | Comfortable |

Read the other way — an observed rate of exactly 5.00% has a 95% Wilson upper
bound of 9.92% at n=100, 6.86% at n=500, 5.86% at n=2000 and 5.37% at n=10000.
A model sitting *on* its tolerance never clears the gate at any practical sample
size, which is the intended behaviour.

A **1% tolerance** is much hungrier: it admits at most a 0.60% observed rate at
n=2000 and 0.83% at n=10000. The older guidance that 10000 samples make a 1% gate
"reliable" was wrong — a 1.0% observed rate cannot clear a 1% gate at that size.

So: a pass at 4.7% on 200 samples is statistically indistinguishable from a fail.
Grow the validation set, or raise the tolerance to a threshold the data can
actually resolve. (These figures are reproduced by
`test_wilson_upper_bound_matches_independent_values`, whose expected values were
derived by solving the score equation `(p_hat - p) / sqrt(p(1-p)/n) = -z` with a
root finder rather than by re-running the closed form.)

### Caveat for `montecarlo_worst`

The reported score there is the **maximum** flip rate over `n_trials` draws — an
upward-biased statistic, and the trials are dependent through the shared
validation set. The Wilson bound describes sampling uncertainty in *that worst
trial's* rate over the validation samples; it is **not** a confidence bound on
the true worst case. It remains the right conservative input to the gate, but do
not report it as "the worst case with 95% confidence".

## 5. Determinism and reproducibility

- The engine uses `np.random.default_rng(seed)`. Always set `seed` (default 42).
- A `None` seed makes the gate non-deterministic and unfit for CI governance.
- Pin the seed in the model card alongside ε and the noise model so a future
  reviewer can reproduce the exact `RobustnessReport`.
- `batch_size` chunking is bit-identical to whole-array evaluation (verified by
  `test_batch_size_chunking_matches_whole_array`); use it freely for large sets.

## 6. Output decoding contract

| Model `predict()` returns | Decoding |
|---|---|
| 1D int / bool | Used directly as the signal |
| 1D float | `(score > decision_threshold).astype(int)` (default 0.5) |
| 2D `(n, k)` with k ≥ 2 | `argmax(axis=1)` — probability or logit matrix |
| 2D `(n, 1)` | Raveled then treated as 1D float |
| 1D categorical (str / object) | Compared for equality as-is — an sklearn classifier fit on `["BUY", "SELL"]` targets returns string labels, and flip detection only needs `!=` |
| Anything non-finite, or rank > 2 | `ValueError` — see below |

Verify the decode matches your BUY/SELL (or long/flat/short) convention before
trusting the flip count — an inverted class ordering inverts the signal and
silently corrupts the score.

Two decodes are rejected outright rather than tolerated, because both used to
produce a silent PASS:

- **Non-finite output.** `nan > threshold` is `False`, so a NaN-producing model
  decodes to the same class for the clean and the perturbed input and reports a
  0% flip rate.
- **Rank > 2 output.** A 3-D array survived the per-sample length check but made
  `flipped_indices` index a *flattened* array — the augmentation target set fed
  back to adversarial training pointed at rows that do not exist.
