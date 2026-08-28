# Workflows for Reproducible ML Training Pipelines

## 1. Resolve the code version, honestly

```bash
git rev-parse HEAD                 # 40 hex (SHA-1) or 64 hex (SHA-256)
test -z "$(git status --porcelain)" || echo "worktree is dirty"
```

`MLPipelineSpec` rejects `"HEAD"`, branch names and abbreviated hashes at
construction. A symbolic ref names a moving target; a manifest carrying one
identifies no code at all. If the worktree was dirty, pass
`worktree_dirty=True` — the flag is covered by the manifest tag, so the
provenance claim stays truthful instead of quietly overstating itself.

## 2. Build the spec

```python
spec = MLPipelineSpec(
    experiment_id="EXP_MOMENTUM_V3",
    git_commit_hash="9f1c3a2b...",      # resolved, 40 or 64 hex
    seed=20260828,                       # 0 <= seed <= 2**32 - 1
    hyperparameters={"learning_rate": 0.05, "epochs": 25},
    model_architecture="RidgeSGD",
    worktree_dirty=False,
)
```

Hyperparameters are validated for encodability **here**, not after training has
already been paid for, and are snapshotted into a read-only view: `frozen=True`
stops the attribute being rebound, not the dict behind it being edited.

## 3. Choose the tag before the run

```python
engine = ReproducibleMLTrainingPipelineEngine(spec, signing_key=vault.get("ml-manifest-key"))
assert engine.signature_algorithm == "HMAC-SHA-256"
```

Without `signing_key` the tag is a bare SHA-256 digest. That catches a truncated
file. It catches nothing against a person, because recomputing it requires no
secret. Keys come from a secrets manager and live somewhere other than the
artifact store — see `centralized-secrets-management-vault-integration`.

## 4. Write `train_fn` so the seeding is real

```python
def train_fn(dataset, hyperparameters):
    # Framework determinism belongs here, not in the harness.
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    # CUBLAS_WORKSPACE_CONFIG=:4096:8 must already be in the environment
    # (CUDA >= 10.2); it cannot be set from here.

    rng = np.random.default_rng(hyperparameters["numpy_seed"])   # explicit
    ...
    return weights.cpu().numpy().tobytes()
```

Two traps this shape avoids:

- `np.random.default_rng()` with no argument draws from OS entropy and is
  untouched by every global seed the harness applies. Construct it from a
  recorded seed and thread it through.
- Returning a live tensor or ndarray raises `ReproducibilityError` rather than
  being coerced into something that hashes stably but means nothing. Reduce to
  `bytes` or primitives yourself.

Do not mutate `dataset` in place. The engine re-digests it after training and
raises if it changed, because otherwise the replicates would train on different
data while the manifest still displayed the original digest.

## 5. Run, and measure

```python
manifest = engine.train_model(
    dataset, train_fn,
    replicate_runs=1,               # 0 = record only; each replicate costs a full fit
    probe_seed_sensitivity=True,    # one extra fit at a neighbouring seed, discarded
)
```

Cost: `1 + replicate_runs + probe_seed_sensitivity` training runs. For a
multi-day fit use `replicate_runs=0` and read the resulting
`is_reproducible=None` as *unknown*, never as a pass.

For data too large to hold, digest it chunk-wise and pass the digest as the
dataset:

```python
with open("features.parquet", "rb") as fh:
    digest = digest_stream(iter(lambda: fh.read(1 << 20), b""))
manifest = engine.train_model(digest, train_fn, replicate_runs=1)
```

## 6. Read the status

| `status` | `is_reproducible` | Meaning |
|---|---|---|
| `REPRODUCIBLE_MANIFEST_CREATED` | `True` | Replicates ran and every artifact digest matched. |
| `MANIFEST_RECORDED_UNVERIFIED` | `None` | Provenance recorded; reproducibility **not measured**. |
| `NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE` | `False` | Replicates disagreed. `replicate_hashes` holds the evidence. |

`seed_sensitivity_verified=False` means the artifact did not move when the seed
did. Expected for a closed-form estimator; a defect for anything meant to be
stochastic — and in either case it means the matching replicates say nothing
about whether seeding is under control.

Divergence is returned, not raised. A non-reproducible pipeline is a finding
that needs a signed record, not an exception that discards the evidence.

## 7. Reproduce later, and localise the difference

```python
result = engine.compare_manifests(current=rerun_manifest, previous=stored_manifest)
```

Both tags are verified first — a manifest that fails its own integrity check
cannot serve as either side of a comparison, so the walk stops at
`SIGNATURE_INVALID`. Then the fields are compared in **causal** order:

```
data_hash → hyperparameters_hash → environment_hash → git_commit_hash → seed → model_weights_hash
```

`status` names the first difference; `mismatched_fields` lists all of them.
This ordering is the point of the method: a weights mismatch explained by a
changed dataset is a data-governance incident, while a weights mismatch with
every input identical is genuine non-determinism in the training stack. Ranking
the symptom above the cause would send the investigation to the wrong team.

If `environment_hash` is the first difference, stop before blaming the model —
the two runs were not comparable in the first place. Read
`manifest.environment` for the Python, platform, NumPy and PyTorch pins and
reconcile those first.

## 8. Store the manifest

Persist it beside the model artifact and the run's log stream, and keep the
signing key elsewhere. A manifest in the same bucket as the artifact it attests
to, tagged with a key held in that same bucket, attests to nothing.
