---
name: offline-train-online-infer-deployment
description: >-
  Use when a model trained in an offline pipeline must run inference inside a live bot
  process, to eliminate train-serve skew with a digest-verified artifact bundling
  weights, scalers and the feature contract.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, train-serve-skew, model-artifact-export, feature-schema-validation, model-versioning, sha256
  brokers_frameworks: "scikit-learn; ONNX; PMML"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever a model is trained in one environment (offline, on historical data, likely with a full ML stack — pandas, scikit-learn, or similar) and needs to run inference in a separate live bot process, often with a lighter dependency footprint and stricter latency requirements. The core risk this skill addresses is **train/serve skew**: any difference between how a feature was computed or transformed during training and how the equivalent step happens live will silently degrade or invalidate the model's predictions, usually without any error — the bot keeps running, it just makes worse decisions.

## When NOT to Use

- The model trains and serves inside the same process from the same in-memory objects — there is no artifact boundary to protect, and the machinery here only adds surface area.
- The model is a tree ensemble, gradient-boosted model, or neural network. The reference implementation in `scripts/model_export.py` serves **standardised linear models only** (weights + intercept + per-feature mean/std + a logistic or identity link). Export those through ONNX or the framework's own format and apply only the artifact-hygiene ideas here — digest verification, schema pinning, parity gating.
- Preprocessing involves fitted transforms this artifact format cannot express (target encoding, PCA, imputers, learned bucketisation). Serialise the whole fitted pipeline instead; a partial export is precisely the skew this skill exists to prevent.

## Prerequisites

- A serialization format both environments can read reliably — JSON for a linear model's parameters, ONNX for portability across frameworks, or the native framework format when training and inference share the same library and version.
- A single shared feature-computation code path, or at minimum a rigorously mirrored one, between training and inference.
- A place to record each artifact's expected SHA-256 digest **outside** the artifact file, so a bot can be told which model it is supposed to be running. See `model-versioning-and-rollback`.

## Workflow

1. Extract the exact feature computation used during training into a standalone module that has no dependency on the training pipeline's batch/dataframe context — it must be importable and runnable identically in the live bot's real-time, single-row-at-a-time context.
2. Prefer sharing that literal code between offline training and online inference (same file imported by both) over reimplementing "the same logic" for the live bot. Reimplementation is the single largest source of train/serve skew, since even a functionally-intended-to-be-identical rewrite can differ in floating-point precision, rolling-window edge handling, or timezone treatment.
3. Export the **fitted preprocessing parameters alongside the weights**, not the weights alone. For a scikit-learn `StandardScaler`, export `scaler.mean_` and `scaler.scale_` — not a separately recomputed standard deviation. sklearn stores `scale_ = 1.0` for a zero-variance feature rather than `0.0`, so a `std` of `0` in an artifact is a signal that the export took the wrong attribute; `export_artifact()` rejects it rather than inventing a fallback.
4. Record the model's **output transform (link function)** in the artifact, not just the linear coefficients. Applying a sigmoid to coefficients trained for a regression target — or serving a logistic model's raw score as if it were a probability — produces plausible numbers that are wrong at every threshold comparison downstream.
5. Compute the content digest over the model content only, **excluding the export timestamp**. A digest that covers the wall clock changes on every export of the same model, which makes it useless both for detecting a changed model and for verifying an artifact at load. Store that digest wherever the deployment's intended version is tracked.
6. Verify the digest **at load time in the live bot**, before the model is allowed to produce a signal, and log the model id, version and digest prefix at startup. Understand the limit: a digest stored inside the artifact it protects detects truncation, partial writes, and accidental substitution — not tampering, since whoever can rewrite the file can rewrite the digest beside it. Only comparison against an out-of-band recorded digest gives you that.
7. Pin the exact feature set **and feature order** into the artifact, and have the inference code validate at load that the vector it is about to construct matches that schema exactly. A code change that reorders or renames features must fail loudly, not shift every weight onto the wrong feature.
8. Decide, explicitly, what the bot does when a live feature is missing or non-finite — and make that decision fail-closed. Never default a missing feature to zero: after standardisation a "zero" becomes `-mean/std`, an input far outside the training distribution that the model will answer confidently and wrongly. The reference implementation raises `SchemaMismatchError` (deployment is broken — halt) or `FeatureValidationError` (this observation is unusable — skip the bar, don't trade it) so the caller can distinguish the two.
9. Before the model drives real capital, run a shadow/paper period and gate promotion on `verify_train_serve_parity()`: feed identical historical feature vectors through both the offline evaluation path and the exported live-inference path and require agreement within tolerance. Treat an empty or all-NaN comparison as a failure, not a pass.

> Full step-by-step procedure with reference-implementation detail: see `references/workflows.md`.
> Framework/serialization coverage and regulatory touchpoints: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Reimplementing feature computation separately for the live bot "for performance" or "in a different language," introducing subtle differences from the training-time computation.
- Exporting model coefficients without the fitted scaling parameters, so live features enter the model on a different scale than it was trained on. This throws no error and looks like a working bot: on this skill's own test fixture the missing-scaler path returns `0.99999999` where the correct answer is `0.32`.
- Defaulting a missing live feature to `0.0`. The standardiser turns that into a multi-sigma outlier rather than a neutral value, so the "safe" default is the most dangerous input in the feature's range.
- Letting NaN reach the sigmoid. `min(50.0, nan)` returns `50.0` in Python because `nan < 50.0` is `False`, so the common "clamp the logit before `exp`" idiom converts missing data into a maximum-confidence `1.0` signal. Reject non-finite inputs; use a branch-stable sigmoid instead of clamping.
- Treating `abs(offline - online) > tolerance` as a sufficient parity check. It is `False` for two NaNs, so a parity gate written that way reports a pair of all-NaN prediction sets as verified — as it does an empty comparison that examined nothing.
- Computing a "content hash" over a payload that includes the export timestamp, so two exports of an identical model produce different digests and no artifact can ever be verified against a recorded one.
- Carrying a digest in the artifact but never recomputing it at load, which detects nothing.
- Writing the artifact non-atomically, so a crash mid-export leaves a truncated JSON file that the bot loads at its next restart.
- Deploying a newly retrained model to live trading without a shadow/paper parity period.
- Not versioning artifacts, making it impossible to correlate a live performance anomaly with a specific training run after the fact.

## Verification

- Run the same historical feature vectors through the offline evaluation pipeline and the exported live-inference path, and confirm predictions agree within a stated tolerance. Confirm the parity gate rejects — not accepts — an empty comparison and any non-finite prediction.
- Confirm re-exporting an unchanged model produces an identical digest, and that changing any weight, scaler value, feature name, or link changes it.
- Corrupt a copy of the artifact (edit a weight, truncate the file, delete `content_hash`) and confirm the loader refuses it rather than serving it.
- Confirm the live bot logs the loaded model's id, version and digest at startup, and that an operator compares this against the intended version before trading begins.
- Confirm a reordered, renamed, or short feature schema raises at load, and that a missing or NaN live feature raises at prediction time instead of producing a number.
- Run `python -m unittest discover -s skills/offline-train-online-infer-deployment/scripts` and confirm all tests pass.

## Related Skills

- `feature-engineering-without-leakage`
- `feature-store-for-live-and-backtest-parity`
- `model-versioning-and-rollback`
- `model-staleness-detection`
- `paper-to-live-promotion-checklist`
