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

The vulnerability score is a binomial proportion: `flipped / total` samples.
For a 5% tolerance gate, the 95% Wilson confidence interval on the flip rate
must sit *below* 5% for a "pass" to be meaningful:

| Validation size | CI half-width at p ≈ 5% | Verdict confidence |
|---|---|---|
| 100 | ±4.3% | A 4% score could be a true 5%+ → unreliable |
| 500 | ±1.9% | Borderline acceptable |
| 2000 | ±1.0% | Reliable for a 5% gate |
| 10000 | ±0.4% | Reliable for a 1% gate |

A pass at 4.7% on 200 samples is statistically indistinguishable from a fail.
Either grow the validation set, bootstrap the CI, or raise the tolerance to a
threshold the data can actually resolve.

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

Verify the decode matches your BUY/SELL (or long/flat/short) convention before
trusting the flip count — an inverted class ordering inverts the signal and
silently corrupts the score.

## Category

`financial-ml-robustness` — see top-level `mappings/` directory.
