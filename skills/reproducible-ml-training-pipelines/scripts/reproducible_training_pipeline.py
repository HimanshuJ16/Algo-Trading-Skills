"""
reproducible-ml-training-pipelines: a provenance and *verification* harness for
a single ML training run.

What this module is
-------------------
It records what went into a training run -- dataset bytes, hyperparameters,
code version, environment -- as canonical SHA-256 digests, executes the
caller's training function under scoped RNG seeding, and then **re-executes it
and compares the artifact digests**. ``is_reproducible`` is the result of that
comparison. It is never asserted.

It does **not** train anything. An earlier version of this module shipped a
`learned_weights = [w + sum(data)]` placeholder and called the result a model.
That number was order-insensitive -- ``[1.0, 9.0]`` and ``[9.0, 1.0]`` produced
the same "weights hash" while producing different dataset hashes -- so the
manifest attested to a relationship that did not exist. Training is now the
caller's ``train_fn``; this module only measures it.

What this module cannot promise
-------------------------------
Bit-identical results in general. PyTorch's reproducibility guidance states
that "completely reproducible results are not guaranteed across PyTorch
releases, individual commits, or different platforms", and that results may
differ between CPU and GPU "even when using identical seeds". The same is true
of BLAS thread counts, SIMD paths and library versions. Reproducibility is a
property of a *pinned environment*; `EnvironmentFingerprint` records the pins
so a later reproduction attempt can tell whether it is even comparable.

Two limits are worth stating before use:

* ``PYTHONHASHSEED`` is fixed at interpreter startup. Setting
  ``os.environ["PYTHONHASHSEED"]`` from running code does nothing, so this
  module records the value and warns; it cannot apply one.
* Framework determinism flags (``torch.use_deterministic_algorithms``,
  ``tf.config.experimental.enable_op_determinism``) are the caller's to set
  inside ``train_fn``. This module seeds RNGs and measures the outcome; it does
  not reach into a framework's kernel selection.

Digests vs. signatures
----------------------
Without a key, ``manifest_signature`` is a SHA-256 **digest**: it detects
accidental corruption, and nothing more, because anyone who can edit the
manifest can recompute it. Pass ``signing_key`` to get an HMAC-SHA-256 tag
(NIST FIPS 198-1), which is what makes the manifest tamper-evident against a
party without the key. ``signature_algorithm`` records which one you have.

Related: `backtest-determinism-and-reproducibility` covers process-global seed
application and event-stream ordering for backtest engines;
`dependency-pinning-and-reproducible-builds` covers pinning the library
versions this module only fingerprints.
"""
from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from contextlib import contextmanager
from types import MappingProxyType
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Tuple
import hashlib
import hmac
import logging
import math
import os
import platform
import random
import re

logger = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_NUMPY = False

try:
    import torch
    HAS_TORCH = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_TORCH = False

__all__ = [
    "MAX_SEED",
    "ReproducibilityError",
    "ManifestStatus",
    "ComparisonStatus",
    "TrainFunc",
    "canonical_bytes",
    "sha256_hex",
    "digest_stream",
    "seeded_rng_scope",
    "EnvironmentFingerprint",
    "MLPipelineSpec",
    "MLReproducibilityManifest",
    "ManifestComparison",
    "ReproducibleMLTrainingPipelineEngine",
    "HAS_NUMPY",
    "HAS_TORCH",
]

#: NumPy's legacy ``RandomState`` rejects seeds outside this range
#: ("Seed must be between 0 and 2**32 - 1"). Restricting to it keeps one
#: integer usable as the seed for ``random``, NumPy and PyTorch alike.
MAX_SEED = 2 ** 32 - 1

#: A resolved git object id: 40 hex for SHA-1 repositories, 64 for SHA-256 ones.
_GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

#: ``train_fn(dataset, hyperparameters) -> artifact``.
TrainFunc = Callable[[Any, Mapping[str, Any]], Any]


class ReproducibilityError(ValueError):
    """Raised when a run cannot be canonicalised, seeded or attested."""


class ManifestStatus(str, Enum):
    """Outcome of a ``train_model`` call."""

    #: Replicates were run and every artifact digest matched.
    REPRODUCIBLE_MANIFEST_CREATED = "REPRODUCIBLE_MANIFEST_CREATED"
    #: ``replicate_runs=0``: provenance recorded, reproducibility not measured.
    MANIFEST_RECORDED_UNVERIFIED = "MANIFEST_RECORDED_UNVERIFIED"
    #: Replicates disagreed. The pipeline is not deterministic in this
    #: environment; the manifest is evidence of that, not of a model.
    NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE = "NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE"


class ComparisonStatus(str, Enum):
    """Outcome of ``compare_manifests``, naming the first field that differs."""

    MANIFEST_MATCH = "MANIFEST_MATCH"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    DATA_HASH_MISMATCH = "DATA_HASH_MISMATCH"
    HYPERPARAMETERS_HASH_MISMATCH = "HYPERPARAMETERS_HASH_MISMATCH"
    ENVIRONMENT_HASH_MISMATCH = "ENVIRONMENT_HASH_MISMATCH"
    CODE_VERSION_MISMATCH = "CODE_VERSION_MISMATCH"
    SEED_MISMATCH = "SEED_MISMATCH"
    MODEL_WEIGHTS_HASH_MISMATCH = "MODEL_WEIGHTS_HASH_MISMATCH"


# --------------------------------------------------------------- canonical bytes

def _encode_float(value: float, path: str) -> bytes:
    """Encodes a float by its exact IEEE-754 bits.

    ``float.hex()`` is exact and does not route through the float-to-decimal
    conversion path, which makes the "nothing is rounded" contract explicit
    rather than a property of ``repr``. The contract is the point: the previous
    version of this module hashed ``round(weight, 6)``, so two runs differing in
    the last ulp -- the classic signature of a summation-order divergence,
    ``sum([0.1] * 10) == 0.9999999999999999`` against ``0.1 * 10 == 1.0`` --
    hashed identically and were reported as reproducible. A detector blind to
    the thing it detects is worse than none, because it certifies.
    """
    if not math.isfinite(value):
        # json.dumps renders every NaN as the same ``NaN`` token, so two runs
        # whose weights had both collapsed to NaN would hash identically and be
        # declared reproducible. Reject rather than launder.
        raise ReproducibilityError(
            f"{path} is {value!r}; non-finite values cannot be attested "
            "(all NaNs share one digest and would appear reproducible)"
        )
    return b"f:" + float(value).hex().encode("ascii") + b";"


def canonical_bytes(value: Any, path: str = "value") -> bytes:
    """Serialises ``value`` to a type-tagged, length-prefixed byte string.

    Every digest in this module runs through here rather than through
    ``json.dumps``, for three reasons:

    * ``json.dumps`` emits bare ``NaN``/``Infinity`` tokens -- not valid JSON,
      and silently collision-prone (see `_encode_float`).
    * String lengths are prefixed, so ``["a", "b"]`` and ``["ab"]`` cannot
      collide the way concatenated text can.
    * Unsupported types raise `ReproducibilityError` naming the path, instead of
      an uncaught ``TypeError`` from deep inside the encoder.

    Supported: ``None``, ``bool``, ``int``, ``float``, ``str``, ``bytes``,
    sequences and mappings thereof. Mapping keys must be strings -- mixed key
    types have no total order, so their serialisation would depend on insertion
    order.

    Note: lists and tuples share a tag, so ``(1, 2)`` and ``[1, 2]`` produce the
    same digest. That is deliberate -- a hyperparameter written as a tuple in
    one run and a list in the next is the same configuration -- and is a stated
    limitation, not an oversight.

    Raises:
        ReproducibilityError: On an unsupported type, a non-finite float, a
            non-string mapping key, or a structure deeper than 64 levels.
    """
    return b"".join(_canonical_chunks(value, path, 0))


def _canonical_chunks(value: Any, path: str, depth: int) -> Iterator[bytes]:
    if depth > 64:
        raise ReproducibilityError(
            f"{path} nests deeper than 64 levels; refusing to encode "
            "(a self-referential structure would not terminate)"
        )
    if value is None:
        yield b"n;"
    elif isinstance(value, bool):
        # Checked before int: bool is a subclass of int, and True would
        # otherwise be indistinguishable from 1.
        yield b"b:1;" if value else b"b:0;"
    elif isinstance(value, int):
        yield b"i:" + str(value).encode("ascii") + b";"
    elif isinstance(value, float):
        yield _encode_float(value, path)
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        yield b"s:" + str(len(raw)).encode("ascii") + b":" + raw + b";"
    elif isinstance(value, (bytes, bytearray)):
        yield b"y:" + str(len(value)).encode("ascii") + b":" + bytes(value) + b";"
    elif isinstance(value, MappingABC):
        items = list(value.items())
        for key, _ in items:
            if not isinstance(key, str):
                raise ReproducibilityError(
                    f"{path} has a non-string key {key!r} of type "
                    f"{type(key).__name__}; mixed key types have no stable sort order"
                )
        yield b"d:" + str(len(items)).encode("ascii") + b":"
        for key, item in sorted(items, key=lambda kv: kv[0]):
            yield from _canonical_chunks(key, f"{path}.{key}", depth + 1)
            yield from _canonical_chunks(item, f"{path}.{key}", depth + 1)
        yield b";"
    elif isinstance(value, SequenceABC):
        items = list(value)
        yield b"l:" + str(len(items)).encode("ascii") + b":"
        for index, item in enumerate(items):
            yield from _canonical_chunks(item, f"{path}[{index}]", depth + 1)
        yield b";"
    else:
        raise ReproducibilityError(
            f"{path} is of unsupported type {type(value).__name__}; convert it "
            "to a primitive, list, dict or bytes first (e.g. ndarray.tobytes(), "
            "tensor.cpu().numpy().tobytes())"
        )


def sha256_hex(payload: bytes) -> str:
    """SHA-256 (NIST FIPS 180-4) of ``payload``, lowercase hex."""
    if not isinstance(payload, (bytes, bytearray)):
        raise ReproducibilityError("payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


def digest_stream(chunks: Iterable[bytes]) -> str:
    """Digests a byte stream incrementally, for datasets too large to hold.

    Hash a parquet/CSV file chunk-wise with this, then pass the returned hex
    string as ``dataset``. The manifest then records a digest of a digest,
    which is still a unique fingerprint of the underlying bytes -- but note that
    it fingerprints the *file encoding*, so a re-serialisation of identical
    values (different compression, different row group size) changes it.

    Raises:
        ReproducibilityError: If any chunk is not bytes, or the stream is empty
            (an empty stream would hash to the digest of the empty string and
            be indistinguishable from a genuinely empty file).
    """
    hasher = hashlib.sha256()
    seen = False
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, (bytes, bytearray)):
            raise ReproducibilityError(
                f"chunk {index} must be bytes, got {type(chunk).__name__}"
            )
        hasher.update(chunk)
        seen = True
    if not seen:
        raise ReproducibilityError("chunk stream was empty; nothing to digest")
    return hasher.hexdigest()


# ------------------------------------------------------------------- RNG scoping

@contextmanager
def seeded_rng_scope(seed: int) -> Iterator[None]:
    """Seeds the process-global RNGs, then **restores** them on exit.

    ``random.seed(s)`` is a process-wide side effect. A training helper that
    seeds without restoring silently rewinds the RNG stream of whatever else
    shares the interpreter -- a running simulation, a sampler, a jitter model --
    and the damage is invisible because both sides still "work". This context
    manager snapshots ``random``, NumPy's legacy global ``RandomState`` and
    PyTorch's CPU/CUDA generators, seeds them, and puts the previous state back
    in a ``finally`` block.

    What it does not reach:

    * ``np.random.default_rng()`` -- a fresh `Generator` draws from OS entropy
      and is unaffected by any global seeding. Construct it from ``seed``
      explicitly and thread it through.
    * ``PYTHONHASHSEED`` -- fixed at interpreter startup; see
      `EnvironmentFingerprint.capture`.
    * Framework kernel selection (cuDNN algorithm choice, op determinism). Set
      those inside ``train_fn``.

    Raises:
        ReproducibilityError: If ``seed`` is not an int in ``[0, MAX_SEED]``.
    """
    _validate_seed(seed)
    python_state = random.getstate()
    numpy_state = np.random.get_state() if HAS_NUMPY else None
    torch_state = torch.get_rng_state() if HAS_TORCH else None  # pragma: no cover
    torch_cuda_state = (
        torch.cuda.get_rng_state_all()
        if HAS_TORCH and torch.cuda.is_available()
        else None
    )  # pragma: no cover - requires CUDA

    try:
        random.seed(seed)
        if HAS_NUMPY:
            np.random.seed(seed)
        if HAS_TORCH:  # pragma: no cover - optional dependency
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
        yield
    finally:
        random.setstate(python_state)
        if HAS_NUMPY and numpy_state is not None:
            np.random.set_state(numpy_state)
        if HAS_TORCH and torch_state is not None:  # pragma: no cover
            torch.set_rng_state(torch_state)
        if torch_cuda_state is not None:  # pragma: no cover - requires CUDA
            torch.cuda.set_rng_state_all(torch_cuda_state)


def _validate_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReproducibilityError(
            f"seed must be an int, got {type(seed).__name__}"
        )
    if not 0 <= seed <= MAX_SEED:
        raise ReproducibilityError(
            f"seed must be in [0, {MAX_SEED}] so the same integer is a legal "
            f"seed for NumPy's legacy RandomState; got {seed}"
        )


# ------------------------------------------------------------------ environment

@dataclass(frozen=True)
class EnvironmentFingerprint:
    """The pins that decide whether two runs are even comparable."""

    python_version: str
    python_implementation: str
    platform_tag: str
    pythonhashseed: str
    hash_randomisation_disabled: bool
    numpy_version: Optional[str] = None
    torch_version: Optional[str] = None

    @classmethod
    def capture(cls) -> "EnvironmentFingerprint":
        """Reads the current interpreter and library versions.

        ``PYTHONHASHSEED`` is *recorded*, never set: hash randomisation is fixed
        before ``main`` runs, so assigning ``os.environ["PYTHONHASHSEED"]`` from
        inside the process has no effect while looking like it worked. When it
        is unset, ``str``/``bytes`` hashing -- and therefore ``set`` and ``dict``
        iteration order derived from it -- varies per process, and a pipeline
        that iterates a set of feature names is non-deterministic for that
        reason alone.
        """
        raw = os.environ.get("PYTHONHASHSEED")
        disabled = raw is not None and raw != "random"
        if not disabled:
            logger.warning(
                "PYTHONHASHSEED is %s: str/bytes hashing is randomised per "
                "process, so set/dict iteration order may vary between runs. "
                "It must be exported before the interpreter starts; it cannot "
                "be set from running code.",
                "unset" if raw is None else repr(raw),
            )
        return cls(
            python_version=platform.python_version(),
            python_implementation=platform.python_implementation(),
            platform_tag=platform.platform(terse=True),
            pythonhashseed=raw if raw is not None else "random",
            hash_randomisation_disabled=disabled,
            numpy_version=np.__version__ if HAS_NUMPY else None,
            torch_version=torch.__version__ if HAS_TORCH else None,  # pragma: no cover
        )

    def canonical_payload(self) -> bytes:
        return canonical_bytes(
            {
                "python_version": self.python_version,
                "python_implementation": self.python_implementation,
                "platform_tag": self.platform_tag,
                "pythonhashseed": self.pythonhashseed,
                "numpy_version": self.numpy_version,
                "torch_version": self.torch_version,
            },
            "environment",
        )


# ------------------------------------------------------------------------- spec

@dataclass(frozen=True)
class MLPipelineSpec:
    """Identifies a training run and the code that produced it.

    Args:
        experiment_id: Non-empty label for the run.
        git_commit_hash: A **resolved** git object id (40 or 64 lowercase hex).
            Symbolic refs are rejected: a manifest recording ``"HEAD"`` names a
            moving target and identifies no code at all.
        seed: Master seed in ``[0, MAX_SEED]``.
        hyperparameters: Mapping accepted by `canonical_bytes`.
        model_architecture: Free-text architecture label.
        worktree_dirty: True if the working tree had uncommitted changes when
            the run started. A clean commit id recorded alongside uncommitted
            edits is a false provenance claim, so it is carried in the manifest
            and covered by the signature.
    """

    experiment_id: str
    git_commit_hash: str
    seed: int = 42
    hyperparameters: Mapping[str, Any] = field(default_factory=dict)
    model_architecture: str = "unspecified"
    worktree_dirty: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise ReproducibilityError("experiment_id must be a non-empty string")
        if not isinstance(self.git_commit_hash, str) or not _GIT_OBJECT_ID.fullmatch(
            self.git_commit_hash
        ):
            raise ReproducibilityError(
                f"git_commit_hash must be a full 40-hex or 64-hex git object id "
                f"(run `git rev-parse HEAD`), got {self.git_commit_hash!r}; a "
                "symbolic ref such as 'HEAD' or a branch name identifies a "
                "moving target, not the code that ran"
            )
        _validate_seed(self.seed)
        if not isinstance(self.hyperparameters, MappingABC):
            raise ReproducibilityError(
                f"hyperparameters must be a mapping, got "
                f"{type(self.hyperparameters).__name__}"
            )
        if not isinstance(self.model_architecture, str) or not self.model_architecture.strip():
            raise ReproducibilityError("model_architecture must be a non-empty string")
        if not isinstance(self.worktree_dirty, bool):
            raise ReproducibilityError("worktree_dirty must be a bool")
        # Fail here rather than mid-training on an un-encodable hyperparameter.
        snapshot = dict(self.hyperparameters)
        canonical_bytes(snapshot, "hyperparameters")
        # frozen=True stops attribute rebinding, not mutation of the dict behind
        # the attribute. Without this snapshot a caller could edit the mapping
        # after the manifest was signed and the recorded hyperparameters_hash
        # would silently stop describing the spec it came from.
        object.__setattr__(self, "hyperparameters", MappingProxyType(snapshot))
        if self.worktree_dirty:
            logger.warning(
                "Experiment %s records commit %s but the worktree was dirty; the "
                "commit id does not describe the code that ran.",
                self.experiment_id, self.git_commit_hash[:12],
            )


# --------------------------------------------------------------------- manifest

@dataclass(frozen=True)
class MLReproducibilityManifest:
    """Attestation for one training run.

    ``is_reproducible`` is ``None`` when reproducibility was not measured
    (``replicate_runs=0``). It is never set to ``True`` without a comparison
    behind it.
    """

    experiment_id: str
    seed: int
    git_commit_hash: str
    model_architecture: str
    worktree_dirty: bool
    data_hash: str
    hyperparameters_hash: str
    environment_hash: str
    model_weights_hash: str
    manifest_signature: str
    signature_algorithm: str
    is_reproducible: Optional[bool]
    seed_sensitivity_verified: Optional[bool]
    verification_runs: int
    replicate_hashes: Tuple[str, ...]
    status: str
    audit_notes: str
    environment: EnvironmentFingerprint

    def signing_payload(self) -> bytes:
        """Every provenance field, canonically encoded, excluding the tag itself.

        The earlier signature covered only ``experiment_id``, ``seed`` and the
        three hashes, so ``git_commit_hash`` could be rewritten without
        invalidating it -- an attestation that did not attest to the code
        version it displayed. Architecture, dirty flag, environment digest and
        the verification outcome are all inside the tag now.
        """
        return canonical_bytes(
            {
                "experiment_id": self.experiment_id,
                "seed": self.seed,
                "git_commit_hash": self.git_commit_hash,
                "model_architecture": self.model_architecture,
                "worktree_dirty": self.worktree_dirty,
                "data_hash": self.data_hash,
                "hyperparameters_hash": self.hyperparameters_hash,
                "environment_hash": self.environment_hash,
                "model_weights_hash": self.model_weights_hash,
                "is_reproducible": self.is_reproducible,
                "seed_sensitivity_verified": self.seed_sensitivity_verified,
                "verification_runs": self.verification_runs,
                "replicate_hashes": list(self.replicate_hashes),
                "status": self.status,
            },
            "manifest",
        )


@dataclass(frozen=True)
class ManifestComparison:
    """Result of checking a rerun against a recorded manifest."""

    matched: bool
    status: str
    mismatched_fields: Tuple[str, ...]
    notes: str


# ------------------------------------------------------------------------ engine

class ReproducibleMLTrainingPipelineEngine:
    """Records, seeds, verifies and attests one ML training run.

    Args:
        spec: The run's identity and inputs.
        signing_key: Optional secret. When supplied, ``manifest_signature`` is
            an HMAC-SHA-256 tag (FIPS 198-1) and the manifest is tamper-evident
            against anyone without the key. When omitted it is a bare SHA-256
            digest, which detects accidental corruption only -- anyone who can
            edit the manifest can recompute it. ``signature_algorithm`` records
            which of the two you are holding.
    """

    def __init__(
        self,
        spec: MLPipelineSpec,
        signing_key: Optional[bytes] = None,
    ) -> None:
        if not isinstance(spec, MLPipelineSpec):
            raise ReproducibilityError(
                f"spec must be an MLPipelineSpec, got {type(spec).__name__}"
            )
        if signing_key is not None:
            if not isinstance(signing_key, (bytes, bytearray)):
                raise ReproducibilityError("signing_key must be bytes or None")
            if len(signing_key) < 16:
                raise ReproducibilityError(
                    "signing_key must be at least 16 bytes; a short key makes "
                    "the HMAC tag guessable and the tamper-evidence nominal"
                )
        self.spec = spec
        self._signing_key = bytes(signing_key) if signing_key is not None else None

    # ------------------------------------------------------------ digests

    @property
    def signature_algorithm(self) -> str:
        return "HMAC-SHA-256" if self._signing_key is not None else "SHA-256"

    def _tag(self, payload: bytes) -> str:
        if self._signing_key is None:
            return sha256_hex(payload)
        return hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()

    def hash_dataset(self, dataset: Any) -> str:
        """Digests the training data.

        ``dataset`` may be a sequence of numbers, ``bytes``, a nested
        list/dict structure, or -- for data too large to hold -- the hex string
        returned by `digest_stream`.
        """
        return sha256_hex(canonical_bytes(dataset, "dataset"))

    def hash_hyperparameters(self) -> str:
        return sha256_hex(canonical_bytes(dict(self.spec.hyperparameters), "hyperparameters"))

    def hash_artifact(self, artifact: Any) -> str:
        """Digests whatever ``train_fn`` returned.

        Weights must be reduced to primitives or bytes by the caller
        (``ndarray.tobytes()``, ``tensor.cpu().numpy().tobytes()``, a
        ``state_dict`` of lists). An un-encodable artifact raises rather than
        being coerced into something that hashes stably but means nothing.
        """
        return sha256_hex(canonical_bytes(artifact, "artifact"))

    # ------------------------------------------------------------ verification

    def verify_signature(self, manifest: MLReproducibilityManifest) -> bool:
        """Recomputes the tag over the manifest's own fields.

        Compared with ``hmac.compare_digest`` so the check does not leak the
        expected tag through its timing.

        Raises:
            ReproducibilityError: If the manifest was tagged with a different
                algorithm than this engine is configured for -- comparing an
                HMAC tag against a bare digest would always fail and read as
                tampering rather than as a key mix-up.
        """
        if manifest.signature_algorithm != self.signature_algorithm:
            raise ReproducibilityError(
                f"manifest was tagged with {manifest.signature_algorithm} but this "
                f"engine is configured for {self.signature_algorithm}; supply the "
                "matching signing_key before verifying"
            )
        return hmac.compare_digest(
            self._tag(manifest.signing_payload()), manifest.manifest_signature
        )

    def compare_manifests(
        self,
        current: MLReproducibilityManifest,
        previous: MLReproducibilityManifest,
    ) -> ManifestComparison:
        """Localises *why* a reproduction attempt diverged.

        A bare "not reproducible" is not actionable. This reports the first
        differing field in causal order -- signature, then data, hyperparameters,
        environment, code version, seed, and only then the artifact -- because a
        weights mismatch explained by a changed dataset is a different incident
        from one with every input identical.
        """
        for manifest, label in ((current, "current"), (previous, "previous")):
            if not isinstance(manifest, MLReproducibilityManifest):
                raise ReproducibilityError(
                    f"{label} must be an MLReproducibilityManifest, got "
                    f"{type(manifest).__name__}"
                )

        for manifest, label in ((previous, "previous"), (current, "current")):
            if not self.verify_signature(manifest):
                return ManifestComparison(
                    matched=False,
                    status=ComparisonStatus.SIGNATURE_INVALID.value,
                    mismatched_fields=("manifest_signature",),
                    notes=(
                        f"The {label} manifest failed its own integrity check; its "
                        "fields cannot be compared."
                    ),
                )

        ordered = (
            ("data_hash", ComparisonStatus.DATA_HASH_MISMATCH),
            ("hyperparameters_hash", ComparisonStatus.HYPERPARAMETERS_HASH_MISMATCH),
            ("environment_hash", ComparisonStatus.ENVIRONMENT_HASH_MISMATCH),
            ("git_commit_hash", ComparisonStatus.CODE_VERSION_MISMATCH),
            ("seed", ComparisonStatus.SEED_MISMATCH),
            ("model_weights_hash", ComparisonStatus.MODEL_WEIGHTS_HASH_MISMATCH),
        )
        mismatched = tuple(
            name for name, _ in ordered
            if getattr(current, name) != getattr(previous, name)
        )
        if not mismatched:
            return ManifestComparison(
                matched=True,
                status=ComparisonStatus.MANIFEST_MATCH.value,
                mismatched_fields=(),
                notes="All attested inputs and the artifact digest match.",
            )

        status = next(s for name, s in ordered if name == mismatched[0])
        notes = (
            f"Reproduction diverged; first difference in causal order is "
            f"{mismatched[0]} (all differing: {', '.join(mismatched)})."
        )
        logger.error("Experiment %s: %s", current.experiment_id, notes)
        return ManifestComparison(
            matched=False,
            status=status.value,
            mismatched_fields=mismatched,
            notes=notes,
        )

    # ------------------------------------------------------------------ training

    def train_model(
        self,
        dataset: Any,
        train_fn: TrainFunc,
        replicate_runs: int = 1,
        probe_seed_sensitivity: bool = False,
    ) -> MLReproducibilityManifest:
        """Runs ``train_fn``, re-runs it, and attests what was observed.

        Args:
            dataset: Passed through to ``train_fn`` and digested. See
                `hash_dataset`.
            train_fn: ``f(dataset, hyperparameters) -> artifact``. Called inside
                `seeded_rng_scope`. Set any framework determinism flags here.
            replicate_runs: Additional executions used to *measure*
                reproducibility. Each costs a full training run, so the default
                of 1 doubles wall-clock. ``0`` records provenance without
                measuring and yields ``is_reproducible=None`` with status
                ``MANIFEST_RECORDED_UNVERIFIED`` -- an honest "unknown", not a
                pass.
            probe_seed_sensitivity: Run one extra training at ``seed + 1`` and
                discard it, to check the artifact actually depends on the seed.
                When it does not, matching replicates prove nothing about
                seeding -- see `seed_sensitivity_verified` on the manifest.

        Returns:
            The manifest. ``status`` is
            ``NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE`` when replicates disagreed;
            that is a recorded finding, not an exception, because the divergent
            digests are the evidence.

        Raises:
            ReproducibilityError: On invalid arguments, an un-encodable dataset
                or artifact, or a non-finite value anywhere in either.
        """
        if not callable(train_fn):
            raise ReproducibilityError(
                f"train_fn must be callable, got {type(train_fn).__name__}"
            )
        if isinstance(replicate_runs, bool) or not isinstance(replicate_runs, int):
            raise ReproducibilityError("replicate_runs must be an int")
        if replicate_runs < 0:
            raise ReproducibilityError("replicate_runs must be non-negative")
        if not isinstance(probe_seed_sensitivity, bool):
            raise ReproducibilityError("probe_seed_sensitivity must be a bool")

        data_hash = self.hash_dataset(dataset)
        hp_hash = self.hash_hyperparameters()
        environment = EnvironmentFingerprint.capture()
        environment_hash = sha256_hex(environment.canonical_payload())

        hyperparameters = dict(self.spec.hyperparameters)
        primary_hash = self.hash_artifact(
            self._run_once(train_fn, dataset, hyperparameters, self.spec.seed)
        )

        replicate_hashes = tuple(
            self.hash_artifact(
                self._run_once(train_fn, dataset, hyperparameters, self.spec.seed)
            )
            for _ in range(replicate_runs)
        )

        # A train_fn that shuffles or normalises the dataset in place would
        # leave later replicates training on different data while the manifest
        # still displayed the original digest. Re-digesting is cheap next to a
        # training run and turns a silent mis-attestation into an error.
        if self.hash_dataset(dataset) != data_hash:
            raise ReproducibilityError(
                "train_fn mutated the dataset in place; the recorded data_hash "
                "would not describe the data the replicates trained on. Pass a "
                "copy, or make train_fn non-mutating."
            )

        seed_sensitivity = None
        if probe_seed_sensitivity:
            probe_seed = self.spec.seed + 1 if self.spec.seed < MAX_SEED else self.spec.seed - 1
            probe_hash = self.hash_artifact(
                self._run_once(train_fn, dataset, hyperparameters, probe_seed)
            )
            seed_sensitivity = probe_hash != primary_hash
            if not seed_sensitivity:
                logger.warning(
                    "Experiment %s: train_fn produced an identical artifact under "
                    "seed %s and %s, so matching replicates do not demonstrate that "
                    "RNG seeding is under control. Expected for a closed-form "
                    "estimator; a defect if the model is meant to be stochastic.",
                    self.spec.experiment_id, self.spec.seed, probe_seed,
                )

        if replicate_runs == 0:
            is_reproducible: Optional[bool] = None
            status = ManifestStatus.MANIFEST_RECORDED_UNVERIFIED
        elif all(h == primary_hash for h in replicate_hashes):
            is_reproducible = True
            status = ManifestStatus.REPRODUCIBLE_MANIFEST_CREATED
        else:
            is_reproducible = False
            status = ManifestStatus.NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE

        notes = self._audit_notes(
            status, data_hash, primary_hash, replicate_runs, is_reproducible, seed_sensitivity
        )
        if status is ManifestStatus.NON_REPRODUCIBLE_ARTIFACT_DIVERGENCE:
            logger.error(notes)
        else:
            logger.info(notes)

        manifest = MLReproducibilityManifest(
            experiment_id=self.spec.experiment_id,
            seed=self.spec.seed,
            git_commit_hash=self.spec.git_commit_hash,
            model_architecture=self.spec.model_architecture,
            worktree_dirty=self.spec.worktree_dirty,
            data_hash=data_hash,
            hyperparameters_hash=hp_hash,
            environment_hash=environment_hash,
            model_weights_hash=primary_hash,
            manifest_signature="",
            signature_algorithm=self.signature_algorithm,
            is_reproducible=is_reproducible,
            seed_sensitivity_verified=seed_sensitivity,
            verification_runs=replicate_runs,
            replicate_hashes=replicate_hashes,
            status=status.value,
            audit_notes=notes,
            environment=environment,
        )
        # The tag covers every field above, so it is computed last and
        # substituted into an otherwise identical manifest.
        return replace(
            manifest, manifest_signature=self._tag(manifest.signing_payload())
        )

    def _run_once(
        self,
        train_fn: TrainFunc,
        dataset: Any,
        hyperparameters: Dict[str, Any],
        seed: int,
    ) -> Any:
        """One seeded execution, with the caller's RNG state left untouched.

        ``hyperparameters`` is copied per call: a ``train_fn`` that mutates the
        mapping it is handed would otherwise make run *n+1* differ from run *n*
        for reasons the manifest could not see.
        """
        with seeded_rng_scope(seed):
            return train_fn(dataset, dict(hyperparameters))

    def _audit_notes(
        self,
        status: ManifestStatus,
        data_hash: str,
        weights_hash: str,
        replicate_runs: int,
        is_reproducible: Optional[bool],
        seed_sensitivity: Optional[bool],
    ) -> str:
        verdict = {None: "unmeasured", True: "verified", False: "FAILED"}[is_reproducible]
        sensitivity = {None: "unprobed", True: "seed-sensitive", False: "seed-INSENSITIVE"}[
            seed_sensitivity
        ]
        return (
            f"ML REPRODUCIBILITY [{status.value}] ({self.spec.experiment_id}): "
            f"seed={self.spec.seed}, data={data_hash[:12]}, weights={weights_hash[:12]}, "
            f"replicates={replicate_runs} ({verdict}), sensitivity={sensitivity}, "
            f"tag={self.signature_algorithm}"
        )
