---
name: offline-train-online-infer-deployment
description: Use when exporting a trained ML signal classifier's weights from an offline
  training pipeline into a live trading bot's inference path
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
brokers_frameworks: []
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a model is trained in one environment (offline, on historical data, likely with a full ML stack — pandas, scikit-learn, or similar) and needs to run inference in a separate live bot process, often with a lighter dependency footprint and stricter latency requirements. The core risk this skill addresses is **train/serve skew**: any difference between how a feature was computed during training and how the equivalent feature is computed live will silently degrade or invalidate the model's predictions, often without any obvious error — the bot keeps running, it just makes worse decisions.

## Prerequisites

- A serialization format for weights/parameters that both the training and inference environments can read reliably (JSON for simple linear/tree models' parameters, ONNX for portability across frameworks, or the native framework format if training and inference share the same library)
- A single shared feature-computation code path, or at minimum a rigorously mirrored one, between training and inference

## Workflow

1. Extract the exact feature computation logic used during training into a standalone module/function that has no dependency on the training pipeline's batch/dataframe-specific context — this module must be importable and runnable identically in the live bot's real-time, single-row-at-a-time context.
2. Prefer sharing this literal code between offline training and online inference (same file/module imported by both) over reimplementing "the same logic" in a different language or framework for the live bot — reimplementation is the single largest source of train/serve skew, since even a functionally-intended-to-be-identical reimplementation can differ in subtle ways (floating point precision, rolling-window edge handling, timezone handling).
3. When weights must cross a language boundary (e.g., trained in Python, served in a Node.js bot), export in a format that fully captures the model's structure, not just final weights — for anything beyond a simple linear model, ensure the export includes preprocessing steps (scaling/normalization parameters, categorical encodings) since applying live features to weights trained on differently-scaled data silently produces wrong predictions with no error thrown.
4. Version every exported model artifact explicitly (e.g., filename or metadata field with training date, training data range, and a content hash) and have the live bot log which exact model version it loaded at startup — this makes it possible to correlate a live performance anomaly with a specific model version rather than guessing.
5. Pin the exact feature set and feature order used at training time into the exported artifact's metadata, and have the inference code validate at load time that the live feature vector it's about to construct matches that exact schema (same features, same order) — silently proceeding with a mismatched feature vector (e.g., after a code change added/reordered features) produces confidently wrong predictions.
6. Before allowing a newly exported model to drive live capital, run it through a shadow/paper period where it produces predictions against live data but does not place real orders, comparing its live predictions against what the offline evaluation predicted for equivalent historical conditions — a divergence here is the clearest signal of train/serve skew.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Reimplementing feature computation logic separately for the live bot "for performance" or "different language," introducing subtle differences from the training-time computation.
- Exporting only final model weights/coefficients without the accompanying preprocessing (scaling, encoding) parameters, so live features are fed into the model on a different scale than it was trained on.
- Not validating feature-vector schema (names/order) at inference load time, allowing a code change that reorders or renames features to silently corrupt predictions rather than throwing an error.
- Deploying a newly retrained model directly to live trading without a shadow/paper comparison period to catch train/serve skew before it costs real capital.
- Not versioning model artifacts, making it impossible to correlate a live performance issue with a specific training run after the fact.

## Verification

- Run the same set of historical feature vectors through both the offline evaluation pipeline and the exported live-inference path and confirm bit-for-bit (or acceptably close, for floating point) identical predictions — any divergence indicates train/serve skew that must be resolved before going live.
- Confirm the live bot logs the loaded model's version/hash at startup and that this is checked against the intended model version before trading begins.
- During a shadow/paper period, confirm live predictions track the offline-evaluated expected behavior for equivalent market conditions within an acceptable tolerance before promoting the model to live capital.

## Related Skills

- `feature-engineering-without-leakage`
- `model-staleness-detection`
- `paper-to-live-promotion-checklist`
