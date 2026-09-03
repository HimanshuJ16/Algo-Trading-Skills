---
name: reproducible-ml-training-pipelines
description: >-
  Use when a model will size, time or select trades and someone must later reproduce it
  exactly; records dataset, hyperparameters, code version and environment as SHA-256
  digests and trains under scoped RNG seeding.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, mlops, reproducibility, provenance-manifest, sha256, hmac, random-seed, model-attestation
  brokers_frameworks: "Reproducible ML Training Pipeline Engine; Python standard library (hashlib, hmac, random); NumPy / PyTorch (optional, detected at import)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when a model's output will size, time or select trades, and someone will later have to answer *"what exactly produced this model, and can we get it back?"* — a live-P&L investigation, a strategy post-mortem, a due-diligence request, or an internal validation review. The engine records the run's inputs as canonical digests, executes the caller's training function under scoped seeding, **re-executes it and compares the artifact digests**, and emits an `MLReproducibilityManifest` whose `is_reproducible` field is the result of that comparison.

Where the EU MiFID II algorithmic-trading regime applies, RTS 6 Article 5(7) requires an investment firm to "keep records of any material change made to the software used for algorithmic trading, allowing it to determine: (a) when a change was made; (b) the person that has made the change; (c) the person that has approved the change; (d) the nature of the change." A model retrained on new data is such a change when its output drives orders. The manifest supplies (a) *when* and (d) *the nature of the change* — commit id, dataset digest, hyperparameter digest, environment pins. (b) *who made* and (c) *who approved* are the firm's governance record, not this module's; it has no author or approver field rather than inviting an unverified one.

## What This Measures, and What It Cannot

**It cannot guarantee bit-identical results, and no tool can.** PyTorch's own reproducibility guidance states that "completely reproducible results are not guaranteed across PyTorch releases, individual commits, or different platforms", and that results may differ between CPU and GPU "even when using identical seeds". BLAS thread counts, SIMD paths and library versions behave the same way. Reproducibility is a property of a **pinned environment**, so `EnvironmentFingerprint` records the pins — Python version and implementation, platform, NumPy and PyTorch versions, `PYTHONHASHSEED` — and a later reproduction attempt can tell from the manifest whether it is even comparable before it starts blaming the model.

Inside that envelope the engine does three things:

1. **Digests the inputs** through a canonical, type-tagged, length-prefixed encoder — not `json.dumps`.
2. **Scopes the seeding**, so a training run cannot silently rewind the RNG stream of whatever else shares the interpreter.
3. **Measures the outcome** by re-running and comparing, and reports `is_reproducible=None` when it was not asked to measure. It is never set to `True` without a comparison behind it.

## When NOT to Use

- **As a trainer.** It trains nothing. `train_fn` is yours; the engine calls it, seeds around it, and hashes what it returns. A previous version shipped a `w + sum(data)` placeholder and hashed it as "model weights" — an artifact that was order-insensitive, so two different datasets with equal sums produced identical weights digests while their dataset digests differed.

- **As a determinism enforcer for a framework.** `torch.use_deterministic_algorithms(True)`, `torch.backends.cudnn.benchmark = False`, `CUBLAS_WORKSPACE_CONFIG=:4096:8` (required by PyTorch for cuBLAS determinism on CUDA ≥ 10.2, and it must be exported before the process starts), `tf.config.experimental.enable_op_determinism()` — all of those belong inside your `train_fn`. The engine seeds RNGs and measures; it does not reach into kernel selection.

- **When the training cost makes a replicate unaffordable.** Verification means running training again: `replicate_runs=1` doubles wall-clock. For a multi-day fit, record with `replicate_runs=0` and accept `is_reproducible=None` — an honest "unknown". Do not read the unverified manifest as a pass.

- **As a substitute for a leakage audit.** A pipeline can be perfectly reproducible and perfectly contaminated: reproducing a look-ahead bug reproduces the bug. See `hyperparameter-tuning-without-target-leakage` and `point-in-time-database-for-ml-training-data`.

- **For backtest-engine determinism.** Event-stream ordering, simulated clocks and trade-log checksums are `backtest-determinism-and-reproducibility`. This skill is about the training run that produces the model, not the simulation that evaluates it.

- **With `signing_key` omitted, as tamper evidence.** Without a key the tag is a bare SHA-256 digest: it catches accidental corruption, and nothing else, because anyone who can edit the manifest can recompute it. See the Workflow decision point.

## Prerequisites

- **A resolved git object id** — `git rev-parse HEAD`, 40 hex (SHA-1) or 64 hex (SHA-256). Symbolic refs are rejected: a manifest recording `"HEAD"` names a moving target. If the worktree was dirty, pass `worktree_dirty=True` (`git status --porcelain` non-empty); a clean commit id recorded next to uncommitted edits is a false provenance claim.
- **`train_fn(dataset, hyperparameters) -> artifact`**, returning primitives, lists, dicts or `bytes` (`ndarray.tobytes()`, `tensor.cpu().numpy().tobytes()`). Non-finite values are rejected, not hashed.
- **A seed in `[0, 2**32 - 1]`** — NumPy's legacy `RandomState` rejects anything outside that range, so the bound keeps one integer usable for `random`, NumPy and PyTorch alike.
- **`PYTHONHASHSEED` exported before the interpreter starts**, if any part of the pipeline iterates a `set` or a `dict` keyed on strings. It cannot be set from running code; the engine records and warns, it cannot apply one.
- **A signing key from a secrets manager**, at least 16 bytes, if the manifest has to survive contact with anyone who might benefit from editing it. See `centralized-secrets-management-vault-integration`.

## Workflow

1. **Resolve the code version before anything else, and be honest about the worktree.** `MLPipelineSpec` rejects `"HEAD"`, branch names and abbreviated hashes at construction. It also validates the hyperparameters are encodable there — failing at construction rather than after a four-hour fit has already been paid for — and snapshots them into a read-only view, because `frozen=True` blocks attribute rebinding, not mutation of the dict behind the attribute.

2. **Decide the tag before the run, not after.**
   - **Decision point — a digest is not a signature.** With no key, `manifest_signature` is `SHA-256(manifest fields)`. That detects a truncated file or a corrupted transfer. It detects nothing at all against a person, because recomputing it requires no secret. Pass `signing_key` and the tag becomes HMAC-SHA-256 (NIST FIPS 198-1), which is what makes the manifest evidence rather than decoration. `signature_algorithm` records which of the two you are holding — check it before quoting a manifest in a review.

3. **Let the engine seed, and let it put the state back.** `seeded_rng_scope` snapshots `random`, NumPy's legacy global `RandomState` and PyTorch's CPU/CUDA generators, seeds them, and restores the previous state in a `finally` block.
   - **Decision point — global seeding is a side effect on everything sharing the interpreter.** A helper that calls `random.seed(42)` and does not restore silently rewinds the stream of a running simulation, sampler or jitter model. Both sides keep "working", which is why it goes unnoticed.
   - **Decision point — `np.random.default_rng()` is not reached by any global seeding.** A fresh `Generator` draws from OS entropy. Construct it from the seed inside `train_fn` and thread it through explicitly, or your "seeded" run is not seeded.

4. **Choose how much verification the run can afford.** `replicate_runs=1` (default) re-runs training once and compares artifact digests; `2` gives a second independent confirmation; `0` records provenance and measures nothing.
   - **Decision point — matching replicates do not prove the seeding works.** If `train_fn` ignores the RNG entirely, every replicate matches trivially. Set `probe_seed_sensitivity=True` to run once more at a neighbouring seed and discard it: `seed_sensitivity_verified=False` means the artifact did not move. That is correct and expected for a closed-form estimator, and a defect for anything meant to be stochastic — the engine logs the warning and leaves the judgement to you.

5. **Read the status, and do not read `None` as `True`.** `REPRODUCIBLE_MANIFEST_CREATED` means measured and matching. `MANIFEST_RECORDED_UNVERIFIED` means not measured. `NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE` means replicates disagreed — returned as a manifest, not raised as an exception, because `replicate_hashes` is the evidence of *how* they disagreed.

6. **When a reproduction attempt fails months later, localise it.** `compare_manifests(current, previous)` verifies both tags, then walks the fields in causal order — data, hyperparameters, environment, code version, seed, and only then the artifact — and reports the **first** difference.
   - **Decision point — a weights mismatch explained by a changed dataset is a different incident from one with every input identical.** The first is a data-governance failure; the second is genuine non-determinism in the training stack. `mismatched_fields` lists everything that differs, `status` names the cause rather than the symptom.

7. **Store the manifest where the model is stored, and keep the key elsewhere.** A manifest sitting in the same bucket as the artifact it attests to, tagged with a key held in the same bucket, attests to nothing.

> Full procedure: see `references/workflows.md`.
> Standards, citations and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A manifest that asserts reproducibility instead of measuring it.** Hard-coding `is_reproducible=True` produces a document that looks like evidence and contains none. This is the failure mode that makes a reproducibility harness *worse* than no harness, because it certifies.
- **Calling a bare hash a signature.** An unkeyed digest is recomputable by whoever edited the manifest. Presenting one to a validator or an auditor as tamper evidence is a misrepresentation of what SHA-256 alone provides.
- **A tag that does not cover the fields it displays.** A signature over only `experiment_id`, `seed` and three hashes leaves `git_commit_hash` free to be rewritten while the manifest still verifies. Whatever a manifest shows, its tag must cover.
- **Rounding before hashing.** Hashing `round(weight, 6)` reports the canonical divergence signature — `sum([0.1] * 10) == 0.9999999999999999` against `0.1 * 10 == 1.0` — as bit-identical. Hash the exact bits.
- **Hashing floats through `json.dumps`.** It emits bare `NaN`/`Infinity` tokens, which are not valid JSON — and every NaN renders identically, so two runs whose weights had both collapsed to NaN hash the same and are declared reproducible.
- **Seeding globally and not restoring.** `random.seed()` is process-wide. The damage lands on whatever else shares the interpreter and is invisible from both sides.
- **Assuming `np.random.seed()` covers modern NumPy code.** NumPy documents it as a "convenience, legacy function that exists to support older code that uses the singleton `RandomState`". Anything built on `default_rng()` is untouched by it.
- **Setting `PYTHONHASHSEED` from inside the program.** `os.environ["PYTHONHASHSEED"] = "0"` runs without error and does nothing: hash randomisation is fixed at interpreter startup. The call looks like it worked.
- **Recording a commit id from a dirty worktree.** The hash then describes code that was never what ran. Set `worktree_dirty=True` and let the manifest carry it.
- **A `train_fn` that mutates the dataset in place.** Shuffle or augment the caller's list and the replicates train on different data while the manifest still displays the original digest. The engine re-digests after training and raises rather than attesting the mismatch.
- **Digesting a generator or an iterator as the dataset.** Consuming it to hash leaves it empty for training. Unsupported types are rejected for this reason.
- **Treating reproducibility as validity.** A reproducible pipeline reproduces its look-ahead bias exactly.

## Verification

- **Measured, not asserted.** A trainer returning a fresh counter value on each call must yield `is_reproducible=False`, `status="NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE"` and two distinct entries in `replicate_hashes`. Against the pre-2.0 implementation this trainer was certified reproducible.
- **Unverified is not passed.** `replicate_runs=0` must return `is_reproducible is None`, `status="MANIFEST_RECORDED_UNVERIFIED"` and `replicate_hashes == ()`.
- **Seed-sensitivity probe.** A `random.random()`-based trainer must give `seed_sensitivity_verified=True`; a closed-form mean must give `False` with a warning logged, and still report `is_reproducible=True`.
- **RNG isolation.** Seed `random` to 999, draw three values; reseed to 999, run `train_model`, draw three more — they must be equal. Confirm the same for NumPy's legacy global state, and confirm restoration still happens when `train_fn` raises.
- **Tag coverage.** With a signing key, mutating **any** of `experiment_id`, `seed`, `git_commit_hash`, `model_architecture`, `worktree_dirty`, the four hashes, `is_reproducible`, `seed_sensitivity_verified`, `verification_runs`, `replicate_hashes` or `status` must fail `verify_signature`. Rewriting the commit hash specifically is the pre-2.0 regression. Verifying an HMAC-tagged manifest with an unkeyed engine must raise, not silently read as tampering.
- **Comparison localises the cause.** Rerunning on a changed dataset must give `status="DATA_HASH_MISMATCH"` with `mismatched_fields[0] == "data_hash"` even though `model_weights_hash` also differs. `DATA_HASH_MISMATCH` was documented pre-2.0 with no code path able to emit it. A tampered manifest on either side must give `SIGNATURE_INVALID`.
- **Canonical encoding.** `["a", "b"]` ≠ `["ab"]`; `True` ≠ `1`; `1` ≠ `1.0`; `0.0` ≠ `-0.0`; `sum([0.1]*10)` ≠ `1.0`; `{"a":1,"b":2}` == `{"b":2,"a":1}`; `[1,2]` == `(1,2)` by documented design. NaN, ±Inf, non-string mapping keys, unsupported types and self-referential structures must each raise `ReproducibilityError`, and the message must name the path (`hyperparameters.outer[1]`).
- **Input rejection.** `"HEAD"`, an abbreviated or uppercase commit hash, a seed outside `[0, 2**32 - 1]`, a bool seed, an empty `experiment_id`, non-mapping hyperparameters, an un-encodable or NaN hyperparameter, a non-callable `train_fn`, a negative `replicate_runs`, a signing key under 16 bytes, and a `train_fn` that appends to the dataset must each raise `ReproducibilityError`. A non-finite dataset must raise **before** `train_fn` is called even once.
- **Determinism of the manifest itself.** Two engines built from identical specs, run on identical data with the same trainer, must produce equal manifests including the signature.
- Run `python -m unittest discover -s skills/reproducible-ml-training-pipelines/scripts` and confirm a 100% pass rate.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `dependency-pinning-and-reproducible-builds`
- `model-versioning-and-rollback`
- `data-lineage-tracking-for-audit-and-debugging`
- `point-in-time-database-for-ml-training-data`
- `feature-store-for-live-and-backtest-parity`
- `hyperparameter-tuning-without-target-leakage`
- `model-card-documentation-for-trading-models`
- `research-environment-vs-production-environment-parity`
- `backtest-audit-trail-for-regulatory-review`
- `centralized-secrets-management-vault-integration`
