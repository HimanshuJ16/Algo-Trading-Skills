---
name: reproducible-ml-training-pipelines
description: >-
  Production-grade ML training pipeline engine enforcing bitwise reproducibility via global random seeding, cryptographic dataset hashing (SHA-256), hyperparameter tracking, and model weights manifest generation.
domain: Machine Learning & Quantitative Research
subdomain: MLOps & Model Reproducibility
tags: ["mlops", "reproducibility", "random-seed", "data-hash", "sha256", "model-manifest", "hyperparameters"]
brokers_frameworks: ["SHA-256 Hashing", "MLflow/W&B Metadata Standard", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing machine learning models for quantitative alpha generation or trade execution. Non-deterministic ML training pipelines produce non-repeatable backtests, untraceable production bugs, and auditing failures. Achieving 100% reproducibility requires explicit global random seeding, immutable training dataset hashing (SHA-256), hyperparameter logging, git commit hash tracking, and cryptographic model artifact signing. This engine executes deterministic training workflows and outputs a verifiable `MLReproducibilityManifest`.

## Prerequisites

- ML pipeline specification (`experiment_id`, `seed`, `git_commit_hash`, `hyperparameters`, `model_architecture`).
- Training dataset sample/array for hashing.

## Workflow

1. **Global Random Seed Binding**:
   - Explicitly seed standard Python `random`, `numpy`, and DL framework random number generators.
2. **Cryptographic Dataset & Hyperparameter Hashing**:
   - Compute SHA-256 hash of training data array and hyperparameter configuration JSON.
3. **Deterministic Model Training**:
   - Execute model training under fixed random seeds and deterministic CPU/GPU flags.
4. **Model Artifact Hashing & Manifest Signing**:
   - Compute SHA-256 hash of trained model weights; generate signed `MLReproducibilityManifest`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unseeded Data Loaders**: Leaving multi-threaded data loader workers unseeded, causing non-deterministic data shuffling.
- **Unversioned Training Data**: Modifying underlying CSV/database features without logging a cryptographic dataset hash.
- **Non-Deterministic GPU Operations**: Using unconstrained CUDA algorithms (e.g. cuDNN non-deterministic convolutions).

## Verification

- Instantiate `ReproducibleMLTrainingPipelineEngine` with seed=12345. Run training twice on identical data sample $\implies$ verify `data_hash`, `model_weights_hash`, and `manifest_signature` are 100% bitwise identical. Modify dataset $\implies$ verify dataset hash changes.
- Run `python scripts/test_reproducible_training_pipeline.py`.

## Related Skills

- `reinforcement-learning-safety-constraints-for-execution`
- `factor-research-multiple-testing-correction`
---
