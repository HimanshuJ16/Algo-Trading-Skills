# Pre-Flight / Sign-off Checklist — offline-train-online-infer-deployment

Use this before considering the skill's implementation complete.

- [ ] **Artifact Packaging:** Confirm `export_artifact()` bundles model weights, scaling parameters, and feature schema into a single JSON artifact.
- [ ] **SHA-256 Content Hash Verification:** Confirm artifact content hash is generated and logged at bot load time.
- [ ] **Feature Schema Validation:** Confirm `load_and_validate()` raises `SchemaMismatchError` on mismatched live feature names or feature order.
- [ ] **Train/Serve Parity Verification:** Confirm `verify_train_serve_parity()` verifies prediction output alignment within $1\text{e-}6$ tolerance.
- [ ] **Automated Testing:** Run `python scripts/test_model_export.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
