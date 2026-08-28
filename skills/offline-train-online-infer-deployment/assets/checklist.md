# Pre-Flight / Sign-off Checklist — offline-train-online-infer-deployment

Use this before a newly exported model is allowed to drive live capital.

## Artifact export

- [ ] **Bundled preprocessing:** `export_artifact()` writes weights, intercept, per-feature `mean`/`std`, feature schema and link function into a single artifact — no scaler left behind in the training environment.
- [ ] **Correct sklearn attributes:** `scaler.mean_` and `scaler.scale_` exported (not a recomputed standard deviation); `coef_[0]` exported for a binary model, not the `(1, n_features)` `coef_` matrix.
- [ ] **Link function recorded:** `link` matches what the offline pipeline applied — `logistic` for `predict_proba`, `identity` for a raw score.
- [ ] **Deterministic digest:** re-exporting the unchanged model produces the same 64-character SHA-256 digest; changing any weight, scaler value, feature name or link changes it.
- [ ] **Atomic write:** an export that fails validation leaves the previous artifact intact and no `.tmp` files behind.

## Live bot load

- [ ] **Digest verified at load:** `load_and_validate()` recomputes and compares the digest; a corrupted, truncated or digest-less artifact raises `ArtifactIntegrityError` instead of loading.
- [ ] **Out-of-band comparison:** the artifact's digest is checked against the version recorded in deployment tracking — an in-file digest alone proves integrity, not authenticity.
- [ ] **Startup logging:** the bot logs model id, version and digest prefix before it produces its first signal, and an operator has confirmed it is the intended model.
- [ ] **Schema pinned:** `load_and_validate()` raises `SchemaMismatchError` on renamed, reordered, added or removed live features.

## Inference safety

- [ ] **No silent defaults:** a missing schema feature raises `SchemaMismatchError`; the bot does not substitute zero or a last-known value.
- [ ] **Non-finite input rejected:** a NaN or Inf feature raises `FeatureValidationError` and the observation is skipped — confirm it does not become a high-confidence signal.
- [ ] **Caller handles both errors distinctly:** deployment errors halt trading; per-observation errors skip the bar. Neither is swallowed by a bare `except`.

## Promotion gate

- [ ] **Parity verified:** `verify_train_serve_parity()` returns `True` over a non-empty set of shared historical vectors within the stated tolerance.
- [ ] **Gate is not vacuous:** confirm the gate returns `False` for an empty comparison and for non-finite predictions.
- [ ] **Shadow period completed:** the model has produced live predictions without placing orders, and they track offline expectations for equivalent conditions.
- [ ] **Automated testing:** `python -m unittest discover -s skills/offline-train-online-infer-deployment/scripts` passes with no failures.

## Sign-off

- Model id / version / digest: ___________________________
- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
