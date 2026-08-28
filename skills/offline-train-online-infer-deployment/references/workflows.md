# Deep Workflow Reference — offline-train-online-infer-deployment

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

The reference implementation (`scripts/model_export.py`) serves **standardised linear
models**: a weight vector plus intercept applied to per-feature standardised inputs,
with a `logistic` link (binary probability) or an `identity` link (raw score). Tree
ensembles and neural nets need a format that captures model structure — see
`references/standards.md`.

## Full Procedure

1. **Bundle the model with its preprocessing, in one artifact.**
   `ModelArtifactManager.export_artifact()` packages `model_id`, `version`,
   `feature_schema`, per-feature `{mean, std}`, `weights`, `intercept` and `link`
   into a single JSON artifact, validating all of it first.
   - Export `scaler.mean_` and `scaler.scale_` from the fitted `StandardScaler`.
     scikit-learn documents that `scale_` is `1` for a zero-variance feature ("If a
     variance is zero, we can't achieve unit variance, and the data is left as-is,
     giving a scaling factor of 1"), so a `std` of `0` in an artifact means the
     export read the wrong attribute. Export rejects it.
   - Export `coef_[0]`, not `coef_`. scikit-learn's binary `LogisticRegression`
     stores `coef_` with shape `(1, n_features)`; passing the matrix produces a
     weight/feature count mismatch, which export rejects.
   - The write is atomic (temp file in the destination directory, fsync,
     `os.replace`), so an interrupted export leaves the previous artifact intact
     rather than a truncated file the bot loads at its next restart.

2. **Tag training runs with a digest of the content, not of the moment.**
   The SHA-256 digest covers `model_id`, `version`, `feature_schema`,
   `preprocessing`, `weights`, `intercept` and `link`, serialised canonically
   (`sort_keys`, no insignificant whitespace, ASCII-escaped, `allow_nan=False`).
   `exported_at` is deliberately **outside** the digest: including it means an
   identical model digests differently on every export, which destroys both change
   detection and load-time verification.
   - Record the returned 64-character digest in your deployment tracking, outside the
     artifact. See `model-versioning-and-rollback` for an append-only registry.

3. **Verify integrity, then schema, at live bot startup.**
   `ModelArtifactManager.load_and_validate()` recomputes the digest and compares it
   (constant-time) against the recorded one, revalidates every structural invariant,
   and only then compares `feature_schema` against the live pipeline's feature names
   and order, raising `SchemaMismatchError` on divergence. Integrity is checked
   first so a corrupted artifact is diagnosed as corruption, not as a schema
   disagreement.
   - **Limit of the guarantee:** the digest lives inside the file it protects. It
     detects truncation, partial writes and accidental substitution. It does not
     establish authenticity — anyone who can rewrite the artifact can rewrite the
     digest beside it. Only comparison against an out-of-band recorded digest does.
   - Artifacts written by the 1.x implementation are not loadable: their digest was
     truncated to 16 characters and covered the export timestamp, so it cannot be
     verified. Re-export from the training pipeline.

4. **Serve one observation at a time, fail-closed.**
   `predict_live()` computes `z = intercept + Σ wᵢ (xᵢ − meanᵢ) / stdᵢ` and applies
   the artifact's link — `logistic` returns `expit(z)`, matching scikit-learn's
   binary `predict_proba` (User Guide §1.1.11.1, `p(X) = expit(Xw + w₀)`).
   - A schema feature absent from the observation raises `SchemaMismatchError`: the
     deployment is broken; halt. Never default it — after standardisation a raw
     `0.0` becomes `−mean/std`, a far out-of-distribution input the model answers
     confidently and wrongly.
   - A non-finite or non-numeric value raises `FeatureValidationError`: this one
     observation is unusable; skip the bar, do not trade it.
   - The sigmoid uses the branch-stable form rather than clamping the score. Clamping
     distorts moderate scores, and because `nan` fails every comparison,
     `max(-50, min(50, nan))` evaluates to `50.0` and turns missing data into a
     maximum-confidence signal.
   - Keys not in the schema are ignored, so one shared feature dict can serve several
     models.

5. **Gate promotion on parity.**
   `verify_train_serve_parity()` returns `True` only when both prediction sequences
   are non-empty, equal length, wholly finite, and agree elementwise within
   tolerance. An empty comparison and an all-NaN comparison both return `False` —
   `abs(nan − nan) > tol` is `False`, so a naive gate reports them as verified.

## Failure Modes Observed in Production

- **Missing scaler export:** exporting coefficients without the fitted scaling
  parameters, feeding raw live features into weights fitted on standardised data. On
  this skill's test fixture that returns `0.99999999` where the correct answer is
  `0.32`.
- **Zero-filled missing features:** substituting `0.0` for a feature the live
  pipeline failed to produce. On the same fixture, `0.072` instead of `0.321`.
- **NaN read as certainty:** a single NaN feature reaching a clamp-then-sigmoid
  inference path returns probability `1.0`.
- **Silent feature reordering:** reordering live feature inputs, so every weight
  applies to the wrong feature, with no error raised.
- **Weight/feature count drift:** a weights list shorter than the schema, zero-padded
  at inference, silently dropping features from the model.
- **Unverifiable artifacts:** a digest carried in the artifact but never recomputed at
  load, so corruption and substitution both pass unnoticed.
- **Vacuous parity gates:** a promotion gate that reports success having compared zero
  samples, or two NaN-filled prediction sets.
- **Unversioned artifacts:** deploying model files with no id, version or digest,
  preventing correlation of live trading anomalies to specific training runs.

## Production Implementation Reference

- Reference code: `scripts/model_export.py` — `ModelArtifactManager`, `ModelArtifact`,
  `SchemaMismatchError`, `ArtifactValidationError`, `ArtifactIntegrityError`,
  `FeatureValidationError`.
- Automated unit tests: `scripts/test_model_export.py`.
