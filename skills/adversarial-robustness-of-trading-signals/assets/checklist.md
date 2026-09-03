# Pre-Promotion / Sign-off Checklist — adversarial-robustness-of-trading-signals

Use this before promoting an ML trading-signal model past the adversarial-
robustness governance gate.

---

## 1. Validation set integrity

- [ ] **Out-of-sample** — `X_clean` was never seen in training (no leakage).
- [ ] **Frozen & version-pinned** — validation-set SHA-256 recorded in the model card.
- [ ] **2-D, non-empty, finite** — enforced by the engine; a `ValueError` here
      is a data-quality defect to fix upstream, never something to bypass.
- [ ] **Schema-aligned** — column order and dtypes match the model's training features.
- [ ] **Adequate size** — ≥ 2000 samples for a 5% tolerance gate, so an
      observed rate up to ~4.15% can still clear its own confidence bound
      (`standards.md` §4). A 1% gate needs far more.

## 2. Perturbation budget

- [ ] **`epsilon` calibrated to one bid-ask spread** per instrument (not a
      blanket 0.01); calibration documented.
- [ ] **`feature_scales` from the training set** (not the validation `ptp`
      fallback) so ε is sample-independent.
- [ ] **`feature_bounds`** (training-set `[min, max]` per feature) supplied, or
      `clip_to_clean_domain=True` — perturbations stay on the feasible manifold.
- [ ] **Validation samples lie inside `feature_bounds`** — samples outside them
      are held to the ε ball rather than clipped back, and a large excursion
      signals train/validation distribution shift worth investigating.
- [ ] **No infeasible inputs** — verify perturbed features do not go negative /
      exceed normalized bounds after clipping.

## 3. Noise model

- [ ] **`montecarlo_worst` with `n_trials ≥ 25`** is the governance noise model
      (tighter black-box bound than a single `random_sign` draw).
- [ ] **`uniform` / single-draw `random_sign`** results recorded in the model
      card but not used as the sole gating signal.
- [ ] **Legacy `worst_case_sign`** not used in new code (it is `random_sign`;
      the name was a misnomer).
- [ ] If the model is differentiable, a **real FGSM/PGD** attack was also run
      (external library) and its flip rate recorded — this gate alone is not
      worst-case for differentiable models.

## 4. Determinism

- [ ] **`seed` set** (not `None`) and pinned in the model card.
- [ ] **Re-run reproduces the same verdict** and `flipped_indices` in CI.
- [ ] **`batch_size` chunking** (if used) verified byte-identical to whole-array
      evaluation.

## 5. Output decoding

- [ ] **Decode matches the signal convention** — for a 2D probability matrix,
      `argmax` class ordering is BUY/SELL (or long/flat/short) as intended.
- [ ] For a 1D float score, **`decision_threshold`** is the production threshold
      (not a test-only value).

## 6. Verdict & remediation

- [ ] **`is_robust_at_ci` is true** — the Wilson upper bound, not just the
      point estimate, clears `flip_tolerance_pct`. Gating on `is_robust` alone
      is not sufficient.
- [ ] **`flip_rate_ci_upper_pct` and `ci_confidence_level`** recorded alongside
      the point estimate.
- [ ] For `montecarlo_worst`, the bound is **not** reported as a confidence
      bound on the true worst case (`standards.md` §4 caveat).
- [ ] If **rejected**, `flipped_indices` attached to the adversarial-training
      ticket as the augmentation target set.
- [ ] If **marginal** (`is_robust` true, `is_robust_at_ci` false — the run logs
      `MARGINAL:` at WARNING), the model is **not** promoted; grow the
      validation set.

## 7. Documentation

- [ ] `RobustnessReport.as_dict()` snapshot persisted to the model card — it
      carries `seed`, `epsilon`, `noise_type`, `n_trials`,
      `vulnerability_score_pct`, `flip_rate_ci_upper_pct`, `ci_confidence_level`,
      `is_robust`, `is_robust_at_ci` and `flip_tolerance_pct` directly. Record
      `validation_set_hash` alongside it.
- [ ] Version-over-version comparison recorded — a regression in
      `vulnerability_score_pct` vs the prior model is a blocking signal.

## Sign-off

- Quant Reviewer: ___________________________
- Date: ___________________________
- Model version: ___________________________
- Validation set hash: ___________________________
- `AdversarialRobustnessConfig` snapshot (paste JSON): ___________________________
- `RobustnessReport.as_dict()` snapshot: ___________________________
