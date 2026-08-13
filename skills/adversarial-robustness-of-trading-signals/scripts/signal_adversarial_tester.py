"""
adversarial-robustness-of-trading-signals
-----------------------------------------
Empirical adversarial-robustness tester for ML trading-signal models.

Injects epsilon-bounded perturbations into a feature matrix and measures the
rate at which the model's trading signal flips. Used as a **pre-deployment
governance gate**: a model whose signal can be flipped by perturbations smaller
than one bid-ask spread is not safe to trade against a noisy microstructure.

Three noise models
~~~~~~~~~~~~~~~~~~
``uniform``
    Random noise in ``[-epsilon, +epsilon] * scale`` per cell — a baseline
    *average-case* stress, roughly analogous to Gaussian microstructure noise.
``random_sign``
    ``+/- epsilon * scale`` with a random per-cell sign (full magnitude every
    time). This is the honest name for what the legacy code called
    ``worst_case_sign``; the signs are random, **not** worst-case, so the old
    name was misleading. Worst-case *directional* noise requires the loss
    gradient (see ``montecarlo_worst`` and FGSM below).
``montecarlo_worst``
    Run ``n_trials`` ``random_sign`` trials and report the **maximum** flip
    rate across trials. This is a one-sided *lower bound* on the true
    worst-case vulnerability when the model is a black box (e.g. sklearn
    trees with no exposed gradient). Increase ``n_trials`` to tighten the
    bound at the cost of compute.

For differentiable models that expose a loss gradient, prefer a real
FGSM/PGD tester (see ``references/standards.md`` §3). This engine targets the
common quant case where the model is an opaque ``predict()`` callable.

References
~~~~~~~~~~
- Goodfellow, Shlens & Szegedy (2014), "Explaining and Harnessing Adversarial
  Examples" — FGSM: ``adv_x = x + epsilon * sign(grad_x loss)``.
- Madry et al. (2017), "Towards Deep Learning Models Resistant to Adversarial
  Attacks" — PGD: iterative FGSM with projection to the L_inf ball.
- Cohen, Rosenfeld & Kolter (2019), "Certified Adversarial Robustness via
  Randomized Smoothing" — a *provable* L2 radius; the stronger guarantee this
  empirical test cannot provide.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# --- Noise model identifiers (exported for config validation and tests) -------
NOISE_UNIFORM = "uniform"
NOISE_RANDOM_SIGN = "random_sign"
NOISE_MONTECARLO_WORST = "montecarlo_worst"
# Legacy alias: the old "worst_case_sign" name is accepted and silently routed
# to random_sign, with a deprecation warning logged. New code should use the
# honest names above.
NOISE_LEGACY_WORST_CASE_SIGN = "worst_case_sign"
_VALID_NOISE_TYPES = frozenset(
    {NOISE_UNIFORM, NOISE_RANDOM_SIGN, NOISE_MONTECARLO_WORST, NOISE_LEGACY_WORST_CASE_SIGN}
)
_PUBLIC_NOISE_TYPES = frozenset(
    {NOISE_UNIFORM, NOISE_RANDOM_SIGN, NOISE_MONTECARLO_WORST}
)


@dataclasses.dataclass
class AdversarialRobustnessConfig:
    """Configuration for the adversarial robustness tester.

    All fields are validated in ``__post_init__`` so misconfiguration fails at
    construction rather than mid-evaluation.
    """

    # Maximum perturbation magnitude, expressed as a fraction of per-feature
    # scale. Default 0.01 == 1% of each feature's scale. Calibrate to one
    # average bid-ask spread of the underlying microstructure (standards.md §1).
    epsilon: float = 0.01
    # Max acceptable % of signals that flip before the model is rejected.
    flip_tolerance_pct: float = 5.0
    # Noise injection model. See module docstring.
    noise_type: str = NOISE_UNIFORM
    # Number of perturbation trials for ``montecarlo_worst`` (the max flip rate
    # across trials is reported). Ignored for single-shot models. Must be >= 1.
    n_trials: int = 25
    # RNG seed for reproducible perturbations. ``None`` => non-deterministic.
    # Production governance should always set a seed (reproducible-ml-pipelines).
    seed: Optional[int] = 42
    # Per-feature scale used to normalize epsilon. Shape (n_features,). If
    # ``None``, scale is computed from the validation set's ptp (range) per
    # feature — convenient but couples epsilon to the sample; prefer passing
    # training-set std or range so the test is independent of the sample drawn.
    feature_scales: Optional[np.ndarray] = None
    # Per-feature ``[min, max]`` bounds for clipping perturbed features back
    # onto the feasible manifold. Shape (n_features, 2). If ``None`` and
    # ``clip_to_clean_domain`` is True, clip to the validation set's observed
    # ``[min, max]``; if False, do not clip.
    feature_bounds: Optional[np.ndarray] = None
    clip_to_clean_domain: bool = True
    # Decoding of model output. If the model returns a 2D probability array,
    # ``argmax`` derives the signal; for a 1D float array, ``decision_threshold``
    # splits the signal (e.g. BUY vs SELL). Integer/bool 1D arrays are used as
    # the signal directly.
    decision_threshold: float = 0.5
    # Chunk size (rows) for evaluating large validation sets in bounded memory.
    # ``None`` => evaluate the whole array at once.
    batch_size: Optional[int] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.epsilon) or self.epsilon < 0.0:
            raise ValueError(f"epsilon must be a finite non-negative float, got {self.epsilon}")
        if not math.isfinite(self.flip_tolerance_pct) or self.flip_tolerance_pct < 0.0:
            raise ValueError(
                f"flip_tolerance_pct must be a finite non-negative float, "
                f"got {self.flip_tolerance_pct}"
            )
        if not math.isfinite(self.decision_threshold):
            raise ValueError("decision_threshold must be finite")
        if self.n_trials < 1:
            raise ValueError(f"n_trials must be >= 1, got {self.n_trials}")
        if self.noise_type not in _VALID_NOISE_TYPES:
            raise ValueError(
                f"Unknown noise_type {self.noise_type!r}; valid: "
                f"{sorted(_PUBLIC_NOISE_TYPES)}"
            )
        if self.batch_size is not None and self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1 or None, got {self.batch_size}")
        if self.feature_scales is not None:
            fs = np.asarray(self.feature_scales, dtype=float)
            if fs.ndim != 1:
                raise ValueError("feature_scales must be 1-D (n_features,)")
            if np.any(~np.isfinite(fs)):
                raise ValueError("feature_scales must be finite")
            if np.any(fs <= 0.0):
                raise ValueError("feature_scales must be strictly positive")
            self.feature_scales = fs
        if self.feature_bounds is not None:
            fb = np.asarray(self.feature_bounds, dtype=float)
            if fb.ndim != 2 or fb.shape[1] != 2:
                raise ValueError("feature_bounds must have shape (n_features, 2)")
            if np.any(~np.isfinite(fb)):
                raise ValueError("feature_bounds must be finite")
            if np.any(fb[:, 0] > fb[:, 1]):
                raise ValueError("feature_bounds[:, 0] (min) must be <= [:, 1] (max)")
            self.feature_bounds = fb


@dataclasses.dataclass
class RobustnessReport:
    """Result of an adversarial robustness evaluation."""

    total_samples: int
    flipped_signals: int
    vulnerability_score_pct: float
    is_robust: bool
    message: str
    noise_type: str = NOISE_UNIFORM
    n_trials: int = 1
    worst_trial_index: int = 0
    flipped_indices: Optional[np.ndarray] = None

    def as_dict(self) -> dict:
        """JSON-serializable view for metrics / governance pipelines."""
        return {
            "total_samples": self.total_samples,
            "flipped_signals": self.flipped_signals,
            "vulnerability_score_pct": self.vulnerability_score_pct,
            "is_robust": self.is_robust,
            "noise_type": self.noise_type,
            "n_trials": self.n_trials,
            "worst_trial_index": self.worst_trial_index,
            "flipped_indices": (
                None if self.flipped_indices is None else self.flipped_indices.tolist()
            ),
        }


class SignalAdversarialTester:
    """Tests an ML model's prediction stability under epsilon-bounded noise."""

    def __init__(self, config: AdversarialRobustnessConfig):
        self.config = config

    # -- feature scaling ----------------------------------------------------
    def _resolve_scales(self, X: np.ndarray) -> np.ndarray:
        if self.config.feature_scales is not None:
            scales = self.config.feature_scales
            if len(scales) != X.shape[1]:
                raise ValueError(
                    f"feature_scales length {len(scales)} != n_features {X.shape[1]}"
                )
            return np.asarray(scales, dtype=float)
        # Fallback: per-feature range of the validation set itself.
        ranges = np.ptp(X, axis=0).astype(float)
        ranges[ranges == 0.0] = 1.0  # guard zero-variance features
        return ranges

    def _resolve_bounds(self, X: np.ndarray) -> Optional[np.ndarray]:
        if self.config.feature_bounds is not None:
            if self.config.feature_bounds.shape[0] != X.shape[1]:
                raise ValueError(
                    f"feature_bounds has {self.config.feature_bounds.shape[0]} rows "
                    f"!= n_features {X.shape[1]}"
                )
            return self.config.feature_bounds
        if self.config.clip_to_clean_domain:
            return np.column_stack([X.min(axis=0), X.max(axis=0)])
        return None

    @staticmethod
    def _clip(X_adv: np.ndarray, bounds: Optional[np.ndarray]) -> np.ndarray:
        if bounds is None:
            return X_adv
        return np.clip(X_adv, bounds[:, 0], bounds[:, 1])

    # -- noise injection ----------------------------------------------------
    def _inject_noise_once(
        self, X: np.ndarray, scales: np.ndarray, rng: np.random.Generator
    ) -> np.ndarray:
        eps = self.config.epsilon
        if self.config.noise_type == NOISE_UNIFORM:
            noise = rng.uniform(-eps, eps, size=X.shape) * scales
        else:
            # random_sign, legacy worst_case_sign, or a single montecarlo trial.
            signs = rng.choice([-1.0, 1.0], size=X.shape)
            noise = signs * (eps * scales)
        return X + noise

    # -- model output decoding ---------------------------------------------
    def _signals(self, y: np.ndarray) -> np.ndarray:
        y = np.asarray(y)
        if y.ndim == 2:
            if y.shape[1] >= 2:
                return np.argmax(y, axis=1)  # probability matrix -> label
            y = y.ravel()  # (n, 1) probability column
        if y.dtype.kind == "f":
            return (y > self.config.decision_threshold).astype(int)
        return y.astype(int)

    def _predict(self, func: Callable[[np.ndarray], np.ndarray], X: np.ndarray) -> np.ndarray:
        bs = self.config.batch_size
        if bs is None or bs >= len(X):
            return self._signals(func(X))
        parts = [self._signals(func(X[i : i + bs])) for i in range(0, len(X), bs)]
        return np.concatenate(parts)

    # -- public API ---------------------------------------------------------
    def evaluate_model(
        self,
        model_predict_func: Callable[[np.ndarray], np.ndarray],
        X_clean: np.ndarray,
    ) -> RobustnessReport:
        """Evaluate model robustness by comparing clean vs adversarial signals.

        :param model_predict_func: callable taking X and returning predictions
            (integer labels, a 1D float score, or a 2D probability matrix).
        :param X_clean: 2D array of clean validation features.
        """
        X = np.asarray(X_clean, dtype=float)
        if X.ndim != 2:
            raise ValueError(f"X_clean must be 2-D, got {X.ndim}-D")

        cfg = self.config
        rng = np.random.default_rng(cfg.seed)
        scales = self._resolve_scales(X)
        bounds = self._resolve_bounds(X)

        y_clean = self._predict(model_predict_func, X)
        if len(y_clean) != X.shape[0]:
            raise ValueError(
                f"model returned {len(y_clean)} signals for {X.shape[0]} samples"
            )

        noise_type = cfg.noise_type
        if noise_type == NOISE_LEGACY_WORST_CASE_SIGN:
            logger.warning(
                "noise_type='worst_case_sign' is deprecated; the signs were random, "
                "not worst-case. Use 'random_sign' (honest) or 'montecarlo_worst'."
            )
            noise_type = NOISE_RANDOM_SIGN

        if noise_type == NOISE_MONTECARLO_WORST:
            worst_pct = -1.0
            worst_mask = np.zeros(X.shape[0], dtype=bool)
            worst_trial = 0
            for trial in range(cfg.n_trials):
                X_adv = self._clip(self._inject_noise_once(X, scales, rng), bounds)
                y_adv = self._predict(model_predict_func, X_adv)
                mask = y_clean != y_adv
                pct = float(mask.mean()) * 100.0
                if pct > worst_pct:
                    worst_pct, worst_mask, worst_trial = pct, mask, trial
            flips = int(worst_mask.sum())
            vuln = round(worst_pct, 4)
            flip_mask = worst_mask
            n_trials_used = cfg.n_trials
        else:
            X_adv = self._clip(self._inject_noise_once(X, scales, rng), bounds)
            y_adv = self._predict(model_predict_func, X_adv)
            mask = y_clean != y_adv
            flips = int(mask.sum())
            vuln = round(float(mask.mean()) * 100.0, 4)
            flip_mask = mask
            n_trials_used = 1
            worst_trial = 0

        is_robust = vuln <= cfg.flip_tolerance_pct
        flipped_idx = np.flatnonzero(flip_mask) if flips > 0 else np.array([], dtype=int)

        event = "adversarial_robustness_pass" if is_robust else "adversarial_robustness_fail"
        level = logging.INFO if is_robust else logging.WARNING
        verdict = "ROBUST" if is_robust else "VULNERABLE"
        msg = (
            f"{verdict}: {vuln:.2f}% of trading signals flipped under "
            f"{noise_type} perturbation (tolerance {cfg.flip_tolerance_pct:.2f}%, "
            f"epsilon={cfg.epsilon}, n_trials={n_trials_used})."
        )
        logger.log(
            level,
            msg,
            extra={
                "event": event,
                "vulnerability_score_pct": vuln,
                "flip_tolerance_pct": cfg.flip_tolerance_pct,
                "noise_type": noise_type,
                "epsilon": cfg.epsilon,
                "n_trials": n_trials_used,
                "flipped_signals": flips,
                "total_samples": X.shape[0],
            },
        )

        return RobustnessReport(
            total_samples=X.shape[0],
            flipped_signals=flips,
            vulnerability_score_pct=vuln,
            is_robust=is_robust,
            message=msg,
            noise_type=noise_type,
            n_trials=n_trials_used,
            worst_trial_index=worst_trial,
            flipped_indices=flipped_idx,
        )


__all__ = [
    "AdversarialRobustnessConfig",
    "RobustnessReport",
    "SignalAdversarialTester",
    "NOISE_UNIFORM",
    "NOISE_RANDOM_SIGN",
    "NOISE_MONTECARLO_WORST",
    "NOISE_LEGACY_WORST_CASE_SIGN",
]
