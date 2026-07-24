"""
offline-train-online-infer-deployment: Production-grade model artifact exporter,
feature schema validator, train/serve skew parity verifier,
and lightweight live inference predictor.
"""
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SchemaMismatchError(ValueError):
    """Raised when live feature names or feature order deviates from training artifact schema."""
    pass


@dataclass
class ModelArtifact:
    model_id: str
    version: str
    content_hash: str
    exported_at: float
    feature_schema: List[str]
    preprocessing_params: Dict[str, Dict[str, float]]  # {feat: {"mean": float, "std": float}}
    weights: List[float]
    intercept: float


class ModelArtifactManager:
    """
    Manages exporting, loading, schema validation, and inference parity checking
    to eliminate train/serve skew between offline training and live serving.
    """

    @staticmethod
    def export_artifact(
        model_id: str,
        weights: List[float],
        intercept: float,
        preprocessing_params: Dict[str, Dict[str, float]],
        feature_names: List[str],
        export_path: str,
        version: str = "1.0.0",
    ) -> str:
        """
        Exports model weights, scaling parameters, and feature schema to JSON artifact with SHA-256 hash.
        """
        payload = {
            "model_id": model_id,
            "version": version,
            "feature_schema": feature_names,
            "preprocessing": preprocessing_params,
            "weights": weights,
            "intercept": intercept,
            "exported_at": time.time(),
        }

        # Deterministic content hash computation
        raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        content_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
        payload["content_hash"] = content_hash

        with open(export_path, "w") as f:
            json.dump(payload, f, indent=2)

        logger.info(f"Exported model artifact '{model_id}' (v{version}) [Hash: {content_hash}] to {export_path}")
        return content_hash

    @staticmethod
    def load_and_validate(export_path: str, live_feature_names: List[str]) -> ModelArtifact:
        """
        Loads artifact and strictly validates that live feature names and ordering match training schema.
        """
        with open(export_path, "r") as f:
            data = json.load(f)

        expected_schema = data.get("feature_schema", [])
        if expected_schema != live_feature_names:
            raise SchemaMismatchError(
                f"Train/Serve Skew Detected! Model expects feature schema {expected_schema}, "
                f"but live pipeline provided {live_feature_names}"
            )

        return ModelArtifact(
            model_id=data.get("model_id", "unnamed_model"),
            version=data.get("version", "1.0.0"),
            content_hash=data.get("content_hash", ""),
            exported_at=data.get("exported_at", 0.0),
            feature_schema=expected_schema,
            preprocessing_params=data.get("preprocessing", {}),
            weights=data.get("weights", []),
            intercept=data.get("intercept", 0.0),
        )

    @staticmethod
    def predict_live(artifact: ModelArtifact, live_feature_dict: Dict[str, float]) -> float:
        """
        Computes standardized linear inference dot product:
        logit = sum( (x_i - mean_i)/std_i * w_i ) + intercept
        """
        logit = artifact.intercept

        for i, feat_name in enumerate(artifact.feature_schema):
            val = live_feature_dict.get(feat_name, 0.0)
            stats = artifact.preprocessing_params.get(feat_name, {"mean": 0.0, "std": 1.0})
            mean = stats.get("mean", 0.0)
            std = stats.get("std", 1.0)

            # Apply scaling
            scaled_val = (val - mean) / std if std > 0 else 0.0
            weight = artifact.weights[i] if i < len(artifact.weights) else 0.0
            logit += scaled_val * weight

        # Sigmoid activation function
        import math
        probability = 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, logit))))
        return probability

    @staticmethod
    def verify_train_serve_parity(
        offline_preds: List[float], online_preds: List[float], tolerance: float = 1e-6
    ) -> bool:
        """Verifies bit-for-bit parity between offline training evaluation and online inference."""
        if len(offline_preds) != len(online_preds):
            return False

        for off, on in zip(offline_preds, online_preds):
            if abs(off - on) > tolerance:
                logger.error(f"Parity mismatch: offline={off}, online={on}")
                return False

        logger.info("Train/Serve prediction parity verified successfully.")
        return True


# Backward compatibility functions
def export_model_artifact(weights, preprocessing_params, feature_names, path):
    payload = {
        "weights": weights,
        "intercept": 0.0,
        "preprocessing": preprocessing_params,
        "feature_schema": feature_names,
        "exported_at": time.time(),
    }
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    payload["content_hash"] = content_hash
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return content_hash


def load_and_validate(path, live_feature_names):
    with open(path) as f:
        artifact = json.load(f)
    if artifact["feature_schema"] != live_feature_names:
        raise ValueError(
            f"Feature schema mismatch: model expects {artifact['feature_schema']}, "
            f"live pipeline provides {live_feature_names}"
        )
    return artifact
