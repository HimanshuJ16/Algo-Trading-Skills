# Standards for Reproducible ML Training Pipelines

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Hashing | Dataset, hyperparameter, environment and artifact digests MUST be SHA-256 over a canonical, type-tagged, length-prefixed encoding. No rounding before hashing. | NIST FIPS 180-4 |
| Attestation | A manifest presented as tamper evidence MUST be tagged with HMAC-SHA-256 under a key held outside the artifact store. An unkeyed digest MUST be labelled as an integrity check only. | NIST FIPS 198-1 |
| Tag coverage | Every provenance field the manifest displays MUST be inside the tag, including the code version and the reproducibility verdict. | Repository mandate |
| Measurement | `is_reproducible` MUST be the result of comparing artifact digests across independent executions. It MUST be `None` when no comparison was run. No assumed, simulated or hard-coded verdicts. | Repository mandate |
| Seeding | Seeds MUST be applied inside a scope that restores the caller's prior RNG state. The global seed and the environment pins MUST be recorded in the manifest. | Python `random`, NumPy, PyTorch docs (below) |
| Code versioning | A resolved git object id (40- or 64-hex) MUST be recorded. Symbolic refs MUST be rejected. Uncommitted changes MUST be flagged. | Repository mandate |
| Non-finite values | NaN and ±Inf MUST be rejected rather than hashed; all NaNs would otherwise share one digest and appear reproducible. | Repository mandate |

## Verified sources

**NIST FIPS 180-4, _Secure Hash Standard (SHS)_.** <https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf>

Defines SHA-256. Used here for all content digests.

**NIST FIPS 198-1, _The Keyed-Hash Message Authentication Code (HMAC)_.** <https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.198-1.pdf>

HMAC provides message authentication and origin authentication, and requires a key shared between the party generating the tag and the party verifying it; the key cannot be recovered from the tag. This is the distinction the module surfaces through `signature_algorithm`: an **unkeyed** SHA-256 digest over the manifest establishes integrity against accidental corruption only, because anybody able to alter the manifest is equally able to recompute the digest. Only the keyed tag is evidence against a party without the key.

**PyTorch, _Reproducibility_.** <https://docs.pytorch.org/docs/stable/notes/randomness.html>

Verbatim: "Completely reproducible results are not guaranteed across PyTorch releases, individual commits, or different platforms." The same note states results "may not be reproducible between CPU and GPU executions, even when using identical seeds." Determinism controls documented there and left to the caller's `train_fn`: `torch.manual_seed`, `torch.use_deterministic_algorithms`, `torch.backends.cudnn.deterministic`, `torch.backends.cudnn.benchmark = False`.

**PyTorch, `torch.use_deterministic_algorithms`.** <https://docs.pytorch.org/docs/stable/generated/torch.use_deterministic_algorithms.html>

On CUDA ≥ 10.2, deterministic cuBLAS requires the environment variable `CUBLAS_WORKSPACE_CONFIG` set to `:4096:8` or `:16:8`; without it, an affected operation raises under `use_deterministic_algorithms(True)`. Like `PYTHONHASHSEED`, it is a process-startup setting.

**TensorFlow, `tf.config.experimental.enable_op_determinism` and `tf.keras.utils.set_random_seed`.** <https://www.tensorflow.org/api_docs/python/tf/config/experimental/enable_op_determinism>

`set_random_seed` sets the Python, NumPy and TensorFlow seeds; `enable_op_determinism` makes op outputs and side effects deterministic. The documentation is explicit that determinism costs performance and that "latency, memory consumption, throughput, and other performance characteristics are not made deterministic" by it. Both are the caller's to invoke inside `train_fn`.

**NumPy, `numpy.random.seed`.** <https://numpy.org/doc/stable/reference/random/generated/numpy.random.seed.html>

Documented as "a convenience, legacy function that exists to support older code that uses the singleton `RandomState`", with the stated best practice being "to use a dedicated `Generator` instance". Consequence relied on by this module: code built on `np.random.default_rng()` is **not** affected by any global seeding, and the caller must construct its generator from the recorded seed. The legacy `RandomState` also rejects seeds outside `[0, 2**32 - 1]` ("Seed must be between 0 and 2**32 - 1"), which is the bound `MLPipelineSpec` enforces so one integer remains legal across `random`, NumPy and PyTorch.

**Python, `PYTHONHASHSEED`.** <https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED>

Controls hash randomisation of `str` and `bytes`; accepts an integer in `[0, 4294967295]` (`0` disables randomisation) or `random`. It must be set **before the interpreter starts**. Assigning `os.environ["PYTHONHASHSEED"]` from running code therefore has no effect on the running process, which is why `EnvironmentFingerprint` records and warns rather than applying it.

**Commission Delegated Regulation (EU) 2017/589 of 19 July 2016 (MiFID II RTS 6).** <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589>

Article 5(7), verbatim: "An investment firm shall keep records of any material change made to the software used for algorithmic trading, allowing it to determine: (a) when a change was made; (b) the person that has made the change; (c) the person that has approved the change; (d) the nature of the change."

Scope and applicability, stated rather than assumed:

- **Jurisdiction: EU.** It binds investment firms engaged in algorithmic trading under Directive 2014/65/EU. It is not a global requirement and does not describe US, UK-post-onshoring, or APAC obligations.
- **Conditional relevance.** A retrained model is "software used for algorithmic trading" only when its output influences order generation or routing. A research model that never reaches production is out of scope.
- **Partial coverage.** The manifest addresses (a) *when*, via the run record, and (d) *the nature of the change*, via commit id, dataset digest, hyperparameter digest and environment pins. It does **not** address (b) *who made* or (c) *who approved* — those are governance records the firm keeps elsewhere. The module deliberately has no author or approver field rather than inviting an unverified one.
- Article 5(4) separately requires the testing methodology to establish that the system does not behave in an unintended manner and continues to work in stressed conditions. Reproducibility is a precondition for such testing to mean anything; it is not itself a discharge of Article 5(4).

## Stated limitations

1. **Reproducibility is measured inside one environment.** A `REPRODUCIBLE_MANIFEST_CREATED` status means the replicates matched on the machine, interpreter and library versions recorded in `EnvironmentFingerprint`. It carries no claim about another machine, another Python build, or CPU against GPU.
2. **The engine measures `train_fn`; it cannot see inside it.** A training function that reads a wall clock, an unseeded `default_rng()`, an environment variable or a mutable module global will be reported as non-reproducible without the engine being able to say why. Conversely, one that ignores its inputs will be reported as reproducible, which is why `probe_seed_sensitivity` exists.
3. **A matching set of replicates is not a validity claim.** Reproducing a look-ahead bug reproduces the bug exactly.
4. **Lists and tuples share an encoding tag.** `(1, 2)` and `[1, 2]` produce the same digest, deliberately: a hyperparameter written as a tuple in one run and a list in the next is the same configuration. A pipeline for which that distinction is meaningful must encode it some other way.
5. **`digest_stream` fingerprints bytes, not values.** Re-serialising identical data with different compression or row-group settings changes the digest. That is the correct behaviour for a byte-level attestation and the wrong tool for asking "is this the same data".
6. **The unkeyed tag is not tamper evidence.** Stated again here because it is the single most likely misreading of a field named `manifest_signature`. Check `signature_algorithm`.
7. **The dataset-mutation guard re-encodes the dataset.** For an in-memory dataset that is a second full canonical encode. For data large enough that this matters, pass a `digest_stream` result instead of the data itself.
