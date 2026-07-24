# Deep Workflow Reference — offline-train-online-infer-deployment

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Model & Preprocessing Artifact Bundling:**
   - Use `ModelArtifactManager.export_artifact()` to package model weights, scaling parameters (mean, std), and feature schema into a single JSON artifact.

2. **Deterministic Version Hash Tagging:**
   - Compute SHA-256 content hashes (`content_hash`) over serialized model payloads to uniquely tag training runs.

3. **Strict Feature Schema Validation at Load Time:**
   - Invoke `ModelArtifactManager.load_and_validate()` at live bot startup to verify feature names and feature ordering match training specifications.
   - Raise `SchemaMismatchError` immediately on schema divergence to eliminate train/serve skew.

4. **Portable Real-Time Inference Execution:**
   - Standardize live input vectors using exported scaling parameters ($X_{\text{scaled}} = (X - \text{mean}) / \text{std}$) and evaluate inferences via `predict_live()`.

5. **Train/Serve Parity Verification:**
   - Compare offline evaluation predictions against online inference outputs using `verify_train_serve_parity()` before promoting new model versions to live trading.

## Failure Modes Observed in Production

- **Missing Scaler Export:** Exporting model coefficients without scaling parameters, passing raw live features into scaled model weights.
- **Silent Feature Reordering:** Reordering feature inputs in live pipelines, generating incorrect predictions without raising errors.
- **Unverified Retrain Deployments:** Promoting newly retrained models directly to live capital without paper/shadow parity testing.
- **Unversioned Artifacts:** Deploying unversioned model files, preventing correlation of live trading errors to specific training runs.

## Production Implementation Reference

- Reference code: `scripts/model_export.py` (`ModelArtifactManager`, `ModelArtifact`, `SchemaMismatchError`).
- Automated unit tests: `scripts/test_model_export.py`.
