# Pre-Flight Checklist — Reproducible ML Training Pipelines

## Provenance

- [ ] Is `git_commit_hash` a **resolved** object id from `git rev-parse HEAD`, not `"HEAD"`, a branch name, or an abbreviation?
- [ ] Was `git status --porcelain` checked, and `worktree_dirty=True` passed if it was non-empty?
- [ ] Are the hyperparameters the ones that actually ran, and encodable (no NaN, no objects, no non-string keys)?
- [ ] Is `experiment_id` traceable to the run's log stream and model artifact?

## Seeding

- [ ] Is the seed inside `[0, 2**32 - 1]`, so it is legal for NumPy's legacy `RandomState` as well as `random` and PyTorch?
- [ ] Does `train_fn` construct every `np.random.default_rng()` from a recorded seed rather than calling it bare? (No global seeding reaches a fresh `Generator`.)
- [ ] Was `PYTHONHASHSEED` exported **before** the interpreter started, if any part of the pipeline iterates a `set` or string-keyed `dict`?
- [ ] Are the framework determinism flags set inside `train_fn` — `torch.use_deterministic_algorithms(True)`, `cudnn.benchmark = False`, `tf.config.experimental.enable_op_determinism()` — and `CUBLAS_WORKSPACE_CONFIG` exported for CUDA ≥ 10.2?

## Measurement

- [ ] Was `replicate_runs >= 1`? If it was `0`, is `is_reproducible=None` being read as **unknown** rather than as a pass?
- [ ] Does `status` say `REPRODUCIBLE_MANIFEST_CREATED` — not merely that a manifest exists?
- [ ] If `seed_sensitivity_verified` is `False`, is the model genuinely closed-form? (If it is meant to be stochastic, matching replicates prove nothing.)
- [ ] If `NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE`, have the differing `replicate_hashes` been traced to a source — wall-clock read, unseeded generator, thread count, non-deterministic kernel — before the model is promoted?

## Attestation

- [ ] Does `signature_algorithm` read `HMAC-SHA-256` for any manifest that will be quoted as evidence? (`SHA-256` is an integrity check only; anyone who can edit the manifest can recompute it.)
- [ ] Is the signing key held somewhere other than the artifact store, and at least 16 bytes?
- [ ] Does `verify_signature` pass on the stored manifest before its fields are quoted anywhere?

## Reproduction

- [ ] When a rerun diverges, was `compare_manifests` used rather than eyeballing the weights hash?
- [ ] Is the reported `status` the **cause** (data, hyperparameters, environment, code, seed) rather than the symptom (`MODEL_WEIGHTS_HASH_MISMATCH`)?
- [ ] If `environment_hash` differs, were the recorded pins reconciled before the model was blamed?

## Scope

- [ ] Is it understood that reproducibility is not validity — that a reproducible pipeline reproduces its look-ahead bias exactly? (See `hyperparameter-tuning-without-target-leakage`.)
- [ ] Where the EU regime applies, are the *who changed* and *who approved* halves of RTS 6 Art. 5(7) recorded in the firm's governance system? (The manifest covers *when* and *nature* only.)
