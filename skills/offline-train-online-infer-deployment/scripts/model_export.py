"""
offline-train-online-infer-deployment: export a trained linear signal model into a
self-describing artifact, and evaluate it in a live bot with every train/serve skew
vector converted from a silent wrong answer into a raised exception.

Scope
-----
This module serves **standardised linear models** -- a weight vector plus intercept
applied to per-feature standardised inputs, with either a logistic link (binary
probability, matching scikit-learn's ``LogisticRegression.predict_proba``) or an
identity link (raw score). It is deliberately not an ONNX/PMML runtime; for tree
ensembles or neural nets, export through a format that captures model structure and
keep only the artifact-hygiene ideas here (see ``references/standards.md``).

Design notes
------------
* **The content hash is a hash of the content.** ``exported_at`` is excluded from the
  digest, so re-exporting an identical model yields an identical digest and a changed
  digest always means changed model content. The previous implementation hashed the
  wall clock alongside the weights, which made two exports of the same model look
  like two different models and made load-time verification impossible.

* **The digest is verified at load, not merely carried.** A live bot loading an
  artifact recomputes the digest over the same canonical field subset and refuses to
  serve on mismatch. This detects truncation, partial writes and accidental
  substitution. It does **not** establish authenticity: the digest lives inside the
  file it protects, so anyone able to rewrite the artifact can rewrite the digest
  beside it. For an out-of-band registry that records the expected digest separately,
  see the `model-versioning-and-rollback` skill.

* **Exports are atomic.** The payload is written to a temporary file in the target
  directory, fsynced, then ``os.replace``d into place, so a crash mid-export leaves
  the previous artifact intact rather than a half-written JSON file that a bot would
  load at the next restart.

* **Silent defaults are the skew.** Every ``.get(..., default)`` in the previous
  implementation was a path to a confidently wrong prediction: a live feature absent
  from the input dict became ``0.0`` *raw* (then standardised to ``-mean/std``, far
  outside the training distribution); a feature absent from the exported scaler was
  fed unscaled into scaled weights; a weights list shorter than the schema silently
  dropped features. Measured on this module's own test fixture, the missing-scaler
  path returned ``0.99999999`` where the correct answer was ``0.32``. All three now
  raise.

* **NaN cannot read as a signal.** ``min(50.0, nan)`` returns ``50.0`` in Python --
  ``nan < 50.0`` is ``False`` -- so the previous ``max(-50, min(50, logit))`` clamp
  turned a single NaN feature into a maximum-confidence ``1.0`` long signal. Inputs,
  parameters and the resulting score are all checked for finiteness, and the sigmoid
  is computed by the overflow-safe branch rather than by clamping, so no value is
  silently distorted.

* **Zero-variance scalers are rejected, not patched.** scikit-learn documents that
  ``StandardScaler`` stores ``scale_ = 1`` for a zero-variance feature ("If a
  variance is zero, we can't achieve unit variance, and the data is left as-is,
  giving a scaling factor of 1"). A ``std`` of ``0`` in an artifact therefore means
  the export wrote raw variance output rather than ``scaler.scale_``; the previous
  code responded by zeroing the feature's contribution, which is not what the
  training pipeline did to it.

* **Exception classes map to operator actions.** ``SchemaMismatchError`` -- the live
  pipeline and the model disagree about features; halt and fix the deployment.
  ``ArtifactValidationError`` / ``ArtifactIntegrityError`` -- the artifact is
  unusable; halt and re-export. ``FeatureValidationError`` -- this one observation is
  unusable; skip the bar, do not trade it. All derive from ``ValueError`` so existing
  ``except ValueError`` call sites keep working.

Compatibility
-------------
Artifacts written by the 1.x implementation are **not** loadable here: their digest
was truncated to 16 characters and covered the export timestamp, so it cannot be
verified. Re-export from the training pipeline. This is deliberate -- a live bot
should refuse an artifact whose integrity it cannot check.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence

logger = logging.getLogger(__name__)

#: Link functions this serving path can evaluate.
SUPPORTED_LINKS = ("logistic", "identity")

#: Fields covered by the content digest. ``exported_at`` and ``content_hash`` are
#: excluded so the digest identifies the model, not the moment it was written.
_HASHED_FIELDS = (
    "model_id",
    "version",
    "feature_schema",
    "preprocessing",
    "weights",
    "intercept",
    "link",
)


class SchemaMismatchError(ValueError):
    """Live feature names, order or count deviate from the artifact schema.

    Operator action: halt serving and reconcile the live feature pipeline with the
    model. Never fall back to a partial feature vector.
    """


class ArtifactValidationError(ValueError):
    """The artifact is structurally unusable (missing scaler, wrong weight count,
    non-finite parameter, unsupported link).

    Operator action: halt serving and re-export from the training pipeline.
    """


class ArtifactIntegrityError(ArtifactValidationError):
    """The artifact's recorded digest does not match its content, or the file could
    not be parsed as the JSON artifact it claims to be."""


class FeatureValidationError(ValueError):
    """A single live observation is unusable (missing value, NaN/Inf, overflow).

    Operator action: skip this observation. Do not substitute a default -- a
    substituted value is an out-of-distribution input the model will answer
    confidently and wrongly.
    """


@dataclass
class ModelArtifact:
    """A validated, digest-verified linear model ready for live inference."""

    model_id: str
    version: str
    content_hash: str
    exported_at: float
    feature_schema: List[str]
    preprocessing_params: Dict[str, Dict[str, float]]  # {feat: {"mean": float, "std": float}}
    weights: List[float]
    intercept: float
    link: str = "logistic"


def _require_finite(value: Any, label: str, exc: type = ArtifactValidationError) -> float:
    """Coerce ``value`` to a finite float or raise ``exc``.

    ``bool`` is rejected explicitly: it passes ``isinstance(x, int)`` and would
    otherwise become a silent ``1.0`` weight or feature value.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise exc(f"{label} must be a real number, got {type(value).__name__} ({value!r}).")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise exc(f"{label} must be finite, got {numeric!r}.")
    return numeric


def _reject_json_constant(constant_name: str) -> float:
    """``json`` hook that refuses the non-standard NaN/Infinity literals.

    ``json.dumps`` emits bare ``NaN``/``Infinity`` by default and ``json.loads``
    accepts them, so a NaN weight from a diverged training run would otherwise
    round-trip through an artifact unnoticed.
    """
    raise ArtifactIntegrityError(
        f"Artifact contains the non-standard JSON literal {constant_name!r}; "
        f"a model parameter is not finite. Re-export from a converged training run."
    )


def _canonical_bytes(content: Mapping[str, Any]) -> bytes:
    """Serialise the hashed field subset deterministically.

    ``sort_keys`` fixes key order, ``separators`` removes insignificant whitespace,
    ``ensure_ascii`` fixes the byte encoding regardless of platform locale, and
    ``allow_nan=False`` makes a non-finite parameter an error rather than a
    non-standard literal. Python's float repr is the shortest round-tripping form, so
    equal floats serialise identically.
    """
    subset = {key: content[key] for key in _HASHED_FIELDS}
    return json.dumps(
        subset, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _validate_content(content: Mapping[str, Any]) -> None:
    """Enforce every invariant the serving path relies on.

    Run identically at export and at load, so an artifact can never be written in a
    state the loader would reject, and a hand-edited artifact is caught before it
    reaches ``predict_live``.
    """
    missing = [key for key in _HASHED_FIELDS if key not in content]
    if missing:
        raise ArtifactValidationError(f"Artifact is missing required field(s): {missing}.")

    for key in ("model_id", "version"):
        value = content[key]
        if not isinstance(value, str) or not value.strip():
            raise ArtifactValidationError(f"'{key}' must be a non-empty string, got {value!r}.")

    link = content["link"]
    if link not in SUPPORTED_LINKS:
        raise ArtifactValidationError(
            f"Unsupported link function {link!r}. Supported: {list(SUPPORTED_LINKS)}. "
            f"An artifact whose training pipeline applied a different output transform "
            f"cannot be served here without reproducing that transform."
        )

    schema = content["feature_schema"]
    if not isinstance(schema, list) or not schema:
        raise ArtifactValidationError(
            "'feature_schema' must be a non-empty list of feature names."
        )
    if any(not isinstance(name, str) or not name.strip() for name in schema):
        raise ArtifactValidationError(
            f"'feature_schema' contains a non-string or blank name: {schema!r}."
        )
    duplicates = sorted({name for name in schema if schema.count(name) > 1})
    if duplicates:
        raise ArtifactValidationError(
            f"'feature_schema' contains duplicate feature name(s) {duplicates}; feature "
            f"positions would no longer map one-to-one onto weights."
        )

    weights = content["weights"]
    if not isinstance(weights, list):
        raise ArtifactValidationError(f"'weights' must be a list, got {type(weights).__name__}.")
    if len(weights) != len(schema):
        raise ArtifactValidationError(
            f"Weight/feature count mismatch: {len(weights)} weight(s) for {len(schema)} "
            f"feature(s). Export 'coef_[0]' for a binary scikit-learn model, not the "
            f"(1, n_features) 'coef_' matrix."
        )
    for index, weight in enumerate(weights):
        _require_finite(weight, f"weights[{index}] (feature '{schema[index]}')")
    _require_finite(content["intercept"], "'intercept'")

    preprocessing = content["preprocessing"]
    if not isinstance(preprocessing, dict):
        raise ArtifactValidationError(
            f"'preprocessing' must be a mapping of feature -> mean/std, "
            f"got {type(preprocessing).__name__}."
        )
    for name in schema:
        stats = preprocessing.get(name)
        if not isinstance(stats, dict):
            raise ArtifactValidationError(
                f"No scaling parameters exported for feature '{name}'. Serving it "
                f"unscaled would feed raw values into weights fitted on standardised "
                f"data -- the classic missing-scaler skew."
            )
        for stat_key in ("mean", "std"):
            if stat_key not in stats:
                raise ArtifactValidationError(
                    f"Scaling parameters for '{name}' are missing '{stat_key}'."
                )
        _require_finite(stats["mean"], f"preprocessing['{name}']['mean']")
        std = _require_finite(stats["std"], f"preprocessing['{name}']['std']")
        if std <= 0.0:
            raise ArtifactValidationError(
                f"preprocessing['{name}']['std'] is {std!r}; it must be strictly "
                f"positive. scikit-learn's StandardScaler stores scale_ = 1.0 for a "
                f"zero-variance feature -- export 'scaler.scale_', not a raw standard "
                f"deviation."
            )


def _sigmoid(z: float) -> float:
    """Numerically stable logistic function, exact for every finite input.

    Both branches evaluate ``exp`` only on non-positive arguments, so neither can
    raise ``OverflowError``; extreme scores saturate to ``1.0``/``0.0`` by float
    underflow. Clamping the score first (the previous approach) both distorts
    moderate scores and, because ``nan`` fails every comparison, lets a NaN score
    escape as ``1.0``.
    """
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def _atomic_write_json(payload: Mapping[str, Any], export_path: str) -> None:
    """Write ``payload`` so a reader never observes a partially written artifact.

    A temporary file in the destination directory is fsynced and then ``os.replace``d
    onto the target, which is atomic on POSIX and on Windows for same-volume renames.
    """
    directory = os.path.dirname(os.path.abspath(export_path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle_fd, temp_path = tempfile.mkstemp(
        prefix="." + os.path.basename(export_path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload, handle, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, export_path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


class ModelArtifactManager:
    """
    Exports, digest-verifies, schema-validates and evaluates linear model artifacts
    so that offline training and online serving cannot silently diverge.
    """

    @staticmethod
    def export_artifact(
        model_id: str,
        weights: Sequence[float],
        intercept: float,
        preprocessing_params: Mapping[str, Mapping[str, float]],
        feature_names: Sequence[str],
        export_path: str,
        version: str = "1.0.0",
        link: str = "logistic",
    ) -> str:
        """Validate and atomically write a model artifact; return its SHA-256 digest.

        The returned digest is 64 lowercase hex characters over the canonical
        serialisation of the model content (``_HASHED_FIELDS``), excluding the export
        timestamp. Record it wherever the deployment's intended model version is
        tracked, and compare it against what the bot logs at startup.

        Raises:
            ArtifactValidationError: the model content is unusable -- non-finite
                parameter, weight/feature count mismatch, duplicate feature name,
                missing or non-positive scaler, unsupported link.
        """
        content: Dict[str, Any] = {
            "model_id": model_id,
            "version": version,
            "feature_schema": list(feature_names),
            "preprocessing": {
                name: dict(stats) for name, stats in dict(preprocessing_params).items()
            },
            "weights": list(weights),
            "intercept": intercept,
            "link": link,
        }
        _validate_content(content)

        # Normalise to plain floats *after* validation so the hashed bytes do not
        # depend on whether the caller passed ints, numpy scalars or floats.
        content["weights"] = [float(weight) for weight in content["weights"]]
        content["intercept"] = float(content["intercept"])
        content["preprocessing"] = {
            name: {"mean": float(stats["mean"]), "std": float(stats["std"])}
            for name, stats in content["preprocessing"].items()
        }

        content_hash = hashlib.sha256(_canonical_bytes(content)).hexdigest()
        payload: Dict[str, Any] = dict(content)
        payload["exported_at"] = time.time()  # UTC epoch seconds; outside the digest
        payload["content_hash"] = content_hash

        _atomic_write_json(payload, export_path)

        logger.info(
            "Exported model artifact '%s' (v%s) [sha256 %s...] -> %s",
            model_id,
            version,
            content_hash[:12],
            export_path,
        )
        return content_hash

    @staticmethod
    def _read_verified_payload(export_path: str) -> Dict[str, Any]:
        """Read an artifact, reject NaN/Infinity literals, verify its digest and
        validate its content. Returns the raw payload dict."""
        with open(export_path, "r", encoding="utf-8") as handle:
            text = handle.read()

        try:
            payload = json.loads(text, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError(
                f"Artifact at {export_path} is not valid JSON ({exc}); it may be "
                f"truncated by an interrupted write. Re-export before serving."
            ) from exc
        if not isinstance(payload, dict):
            raise ArtifactIntegrityError(
                f"Artifact at {export_path} is a JSON {type(payload).__name__}, not an object."
            )

        recorded = payload.get("content_hash")
        if not isinstance(recorded, str) or not recorded:
            raise ArtifactIntegrityError(
                f"Artifact at {export_path} carries no 'content_hash'. An artifact whose "
                f"integrity cannot be checked must not drive live capital."
            )

        _validate_content(payload)
        actual = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        if not hmac.compare_digest(recorded.strip().lower(), actual):
            logger.error(
                "ARTIFACT DIGEST MISMATCH at %s: recorded %s..., computed %s.... "
                "Do not serve this artifact.",
                export_path,
                recorded[:12],
                actual[:12],
            )
            raise ArtifactIntegrityError(
                f"Content hash mismatch for {export_path}: recorded {recorded}, "
                f"computed {actual}. The artifact was modified or corrupted after export."
            )
        return payload

    @staticmethod
    def load_and_validate(export_path: str, live_feature_names: Sequence[str]) -> ModelArtifact:
        """Load an artifact for serving, verifying integrity then feature schema.

        Order matters: a corrupted artifact is diagnosed as corruption rather than as
        a schema disagreement.

        Args:
            export_path: path written by :meth:`export_artifact`.
            live_feature_names: the feature names, **in the order the live pipeline
                will emit them**, that this bot is about to compute.

        Raises:
            ArtifactIntegrityError: unparseable, digest-less or digest-mismatched file.
            ArtifactValidationError: structurally unusable artifact content.
            SchemaMismatchError: live schema differs from the training schema.
        """
        payload = ModelArtifactManager._read_verified_payload(export_path)

        expected_schema: List[str] = payload["feature_schema"]
        live_schema = list(live_feature_names)
        if expected_schema != live_schema:
            raise SchemaMismatchError(
                f"Train/serve skew detected! Model expects feature schema "
                f"{expected_schema}, but the live pipeline provided {live_schema}. "
                f"Names and order must match exactly."
            )

        artifact = ModelArtifact(
            model_id=payload["model_id"],
            version=payload["version"],
            content_hash=payload["content_hash"],
            exported_at=float(payload.get("exported_at", 0.0)),
            feature_schema=expected_schema,
            preprocessing_params=payload["preprocessing"],
            weights=[float(weight) for weight in payload["weights"]],
            intercept=float(payload["intercept"]),
            link=payload["link"],
        )
        logger.info(
            "Loaded model artifact '%s' (v%s) [sha256 %s...] link=%s, %d feature(s) verified.",
            artifact.model_id,
            artifact.version,
            artifact.content_hash[:12],
            artifact.link,
            len(artifact.feature_schema),
        )
        return artifact

    @staticmethod
    def predict_live(artifact: ModelArtifact, live_feature_dict: Mapping[str, float]) -> float:
        """Evaluate one live observation.

        Computes ``z = intercept + sum_i w_i * (x_i - mean_i) / std_i`` over the
        artifact's feature schema, then applies the artifact's link: ``logistic``
        returns ``expit(z)``, matching scikit-learn's binary
        ``LogisticRegression.predict_proba`` (User Guide 1.1.11.1,
        ``p(X) = expit(Xw + w0)``); ``identity`` returns ``z`` unchanged.

        Keys in ``live_feature_dict`` that are not in the schema are ignored, so one
        shared feature dict can serve several models. Schema features that are absent
        are an error -- never a default.

        Raises:
            SchemaMismatchError: a schema feature is absent from the observation.
            FeatureValidationError: a value is non-numeric or non-finite, or the
                accumulated score overflows.
            ArtifactValidationError: the artifact is internally inconsistent. Only
                reachable when a :class:`ModelArtifact` was constructed directly
                rather than through :meth:`load_and_validate`; the checks are O(1)
                so the live path pays nothing for them, and without them this
                function raises an untyped ``KeyError``/``IndexError`` that a caller
                following the documented contract would not catch.
        """
        if len(artifact.weights) != len(artifact.feature_schema):
            raise ArtifactValidationError(
                f"Artifact '{artifact.model_id}' has {len(artifact.weights)} weight(s) "
                f"for {len(artifact.feature_schema)} feature(s). Build artifacts with "
                f"load_and_validate(), which rejects this at load."
            )

        missing = [name for name in artifact.feature_schema if name not in live_feature_dict]
        if missing:
            raise SchemaMismatchError(
                f"Live observation is missing schema feature(s) {missing} for model "
                f"'{artifact.model_id}' (v{artifact.version}). Substituting a default "
                f"would standardise to a far out-of-distribution input and yield a "
                f"confident, wrong prediction."
            )

        score = _require_finite(artifact.intercept, "artifact intercept")
        for index, name in enumerate(artifact.feature_schema):
            value = _require_finite(
                live_feature_dict[name], f"live feature '{name}'", exc=FeatureValidationError
            )
            stats = artifact.preprocessing_params.get(name)
            if not stats:
                raise ArtifactValidationError(
                    f"Artifact '{artifact.model_id}' has no scaling parameters for "
                    f"schema feature '{name}'; serving it unscaled would feed raw "
                    f"values into weights fitted on standardised data."
                )
            score += (
                (value - float(stats["mean"])) / float(stats["std"])
            ) * artifact.weights[index]

        if not math.isfinite(score):
            raise FeatureValidationError(
                f"Linear score overflowed to {score!r} for model '{artifact.model_id}'; "
                f"the observation is far outside the training distribution."
            )

        return _sigmoid(score) if artifact.link == "logistic" else score

    @staticmethod
    def verify_train_serve_parity(
        offline_preds: Sequence[float], online_preds: Sequence[float], tolerance: float = 1e-6
    ) -> bool:
        """Gate a model promotion on offline and online predictions agreeing.

        Returns ``True`` only when both sequences are non-empty, equal length, wholly
        finite, and agree elementwise within ``tolerance``. An empty comparison
        returns ``False``: a gate that passes on zero samples verifies nothing. And
        because ``abs(nan - nan) > tol`` is ``False``, two all-NaN sequences would
        otherwise be reported as parity-verified.
        """
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError(f"tolerance must be a finite non-negative number, got {tolerance!r}.")

        if len(offline_preds) == 0 or len(online_preds) == 0:
            logger.error("Parity check has no samples to compare; treating as NOT verified.")
            return False
        if len(offline_preds) != len(online_preds):
            logger.error(
                "Parity check length mismatch: %d offline vs %d online prediction(s).",
                len(offline_preds),
                len(online_preds),
            )
            return False

        worst_index, worst_delta = 0, 0.0
        for index, (offline, online) in enumerate(zip(offline_preds, online_preds)):
            for label, sample in (("offline", offline), ("online", online)):
                if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                    logger.error(
                        "Parity sample %d (%s) is not a real number (%r); NOT verified.",
                        index,
                        label,
                        sample,
                    )
                    return False
                if not math.isfinite(sample):
                    logger.error(
                        "Parity sample %d (%s) is non-finite (%r); NaN compares equal to "
                        "nothing, so this is a failure, not a pass.",
                        index,
                        label,
                        sample,
                    )
                    return False
            delta = abs(float(offline) - float(online))
            if delta > worst_delta:
                worst_index, worst_delta = index, delta

        if worst_delta > tolerance:
            logger.error(
                "Parity mismatch at sample %d: offline=%r, online=%r (delta %.3e > tolerance %.3e).",
                worst_index,
                offline_preds[worst_index],
                online_preds[worst_index],
                worst_delta,
                tolerance,
            )
            return False

        logger.info(
            "Train/serve parity verified over %d sample(s); max delta %.3e <= tolerance %.3e.",
            len(offline_preds),
            worst_delta,
            tolerance,
        )
        return True


# --------------------------------------------------------------- compatibility API
# Thin wrappers over the hardened path so the two entry points cannot drift apart.
# SchemaMismatchError subclasses ValueError, so callers written against the original
# `raise ValueError` contract keep working unchanged.


def export_model_artifact(
    weights: Sequence[float],
    preprocessing_params: Mapping[str, Mapping[str, float]],
    feature_names: Sequence[str],
    path: str,
) -> str:
    """Export with a zero intercept and placeholder identity metadata.

    Prefer :meth:`ModelArtifactManager.export_artifact`, which records a real
    ``model_id`` and ``version`` -- without them a live anomaly cannot be traced back
    to the training run that produced the model.
    """
    return ModelArtifactManager.export_artifact(
        model_id="legacy_export",
        weights=weights,
        intercept=0.0,
        preprocessing_params=preprocessing_params,
        feature_names=feature_names,
        export_path=path,
        version="0.0.0",
    )


def load_and_validate(path: str, live_feature_names: Sequence[str]) -> Dict[str, Any]:
    """Load, digest-verify and schema-check an artifact, returning the raw payload dict.

    Prefer :meth:`ModelArtifactManager.load_and_validate`, which returns a typed
    :class:`ModelArtifact` that :meth:`ModelArtifactManager.predict_live` accepts.
    """
    payload = ModelArtifactManager._read_verified_payload(path)
    live_schema = list(live_feature_names)
    if payload["feature_schema"] != live_schema:
        raise SchemaMismatchError(
            f"Feature schema mismatch: model expects {payload['feature_schema']}, "
            f"live pipeline provides {live_schema}"
        )
    return payload
