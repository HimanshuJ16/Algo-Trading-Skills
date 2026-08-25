"""
ensemble-signal-combination-without-overfitting: causal multi-model signal normalizer,
regularized non-negative weight optimizer, 1/N shrinkage blender, and ensemble aggregator.

Design notes
------------
* Normalization is strictly causal (point `t` only ever sees observations
  `t' <= t`). A full-sample Z-score leaks the future into every historical bar
  and inflates backtest performance; see `normalize_zscore`.
* `INVERSE_VARIANCE` implements Bates & Granger (1969) inverse-forecast-error-
  variance combination, which is defined against a realized target series. It is
  NOT the variance of the signal itself: after standardization every signal has
  unit variance, so signal-variance weighting degenerates to 1/N.
* `SHRUNK_NNLS` solves the non-negative least squares problem
  `min_w ||Zw - y||^2  s.t.  w >= 0` and then shrinks toward 1/N. The
  non-negativity constraint itself acts as implicit shrinkage on the estimated
  weights (Jagannathan & Ma, 2003); the explicit 1/N blend follows the naive-
  diversification robustness result of DeMiguel, Garlappi & Uppal (2009).
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import statistics
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Numerical guards.
_MIN_STD = 1e-9          # Floor on the rolling standard deviation.
_MIN_MSE = 1e-12         # Floor on estimated forecast-error variance.
_NNLS_TOL = 1e-10        # Coordinate-descent convergence tolerance.
_NNLS_MAX_ITER = 500     # Coordinate-descent iteration cap.
_NNLS_RIDGE = 1e-6       # Relative Tikhonov damping for near-collinear sub-models.
_CAP_TOL = 1e-12         # Weight-cap water-filling convergence tolerance.


class EnsembleError(ValueError):
    """Raised on malformed ensemble inputs or infeasible configuration."""


class EnsembleMethod(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"            # 1/N robust baseline, needs no target
    INVERSE_VARIANCE = "INVERSE_VARIANCE"    # Bates-Granger inverse forecast-error variance
    SHRUNK_NNLS = "SHRUNK_NNLS"              # Non-negative least squares shrunk toward 1/N

    @property
    def requires_target(self) -> bool:
        """True when the method is fitted against a realized target series."""
        return self is not EnsembleMethod.EQUAL_WEIGHT


@dataclass
class SignalStream:
    model_name: str
    signals: List[float]


@dataclass
class EnsembleResult:
    ensemble_signals: List[float]
    weights: Dict[str, float]
    method: EnsembleMethod
    is_normalized: bool


def _require_finite(values: Sequence[float], label: str) -> None:
    """Rejects NaN/Inf before they can propagate silently into weights."""
    for idx, v in enumerate(values):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise EnsembleError(f"{label}: non-finite or non-numeric value {v!r} at index {idx}.")


class EnsembleSignalCombiner:
    """
    Combines sub-model alpha signals into a robust composite signal using causal
    Z-score normalization, non-negative weight constraints, 1/N shrinkage, and a
    per-model weight cap.
    """

    def __init__(
        self,
        method: EnsembleMethod = EnsembleMethod.SHRUNK_NNLS,
        shrinkage_lambda: float = 0.50,
        max_weight_cap: float = 0.40,
        lookback: Optional[int] = None,
        min_periods: int = 2,
        clip: float = 3.0,
    ):
        """
        Args:
            method: Weighting scheme. Everything except `EQUAL_WEIGHT` requires
                a realized target series in `combine_signals`.
            shrinkage_lambda: 1/N shrinkage intensity in [0, 1]. Values outside
                this range can produce negative weights and are rejected.
            max_weight_cap: Maximum weight for any single sub-model, in (0, 1].
                Raised to 1/N automatically when 1/N exceeds the cap, since no
                weight vector summing to 1 can respect a cap below 1/N.
            lookback: Rolling window for normalization. `None` uses an expanding
                causal window.
            min_periods: Observations required before a Z-score is emitted;
                earlier bars emit 0.0. Must be >= 2 (a standard deviation is
                undefined for a single observation).
            clip: Symmetric Z-score clip bound; must be > 0.
        """
        if not isinstance(method, EnsembleMethod):
            method = EnsembleMethod(method)
        if not (0.0 <= shrinkage_lambda <= 1.0):
            raise EnsembleError(f"shrinkage_lambda must be in [0, 1], got {shrinkage_lambda}.")
        if not (0.0 < max_weight_cap <= 1.0):
            raise EnsembleError(f"max_weight_cap must be in (0, 1], got {max_weight_cap}.")
        if lookback is not None and lookback < 2:
            raise EnsembleError(f"lookback must be None or >= 2, got {lookback}.")
        if min_periods < 2:
            raise EnsembleError(f"min_periods must be >= 2, got {min_periods}.")
        if clip <= 0.0:
            raise EnsembleError(f"clip must be > 0, got {clip}.")

        self.method = method
        self.shrinkage_lambda = shrinkage_lambda
        self.max_weight_cap = max_weight_cap
        self.lookback = lookback
        self.min_periods = min_periods
        self.clip = clip

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_zscore(
        raw_signals: Sequence[float],
        lookback: Optional[int] = None,
        min_periods: int = 2,
        clip: float = 3.0,
    ) -> List[float]:
        """
        Causal rolling Z-score, clipped to [-clip, +clip].

        The statistic at index `t` is computed from `raw_signals[:t + 1]` only
        (optionally truncated to the last `lookback` observations), so no future
        observation influences a historical value. Bars with fewer than
        `min_periods` observations available emit 0.0 rather than a Z-score
        estimated from an undefined standard deviation.

        Note: this differs from a full-sample `(x - mean) / stdev` transform,
        which is look-ahead biased and must not be used to build backtest inputs.
        """
        if lookback is not None and lookback < 2:
            raise EnsembleError(f"lookback must be None or >= 2, got {lookback}.")
        if min_periods < 2:
            raise EnsembleError(f"min_periods must be >= 2, got {min_periods}.")
        if clip <= 0.0:
            raise EnsembleError(f"clip must be > 0, got {clip}.")

        _require_finite(raw_signals, "signal")
        values = [float(v) for v in raw_signals]
        if not values:
            return []

        normalized: List[float] = []
        for t in range(len(values)):
            start = 0 if lookback is None else max(0, t + 1 - lookback)
            window = values[start:t + 1]
            if len(window) < min_periods:
                normalized.append(0.0)
                continue
            std_val = statistics.stdev(window)
            if std_val < _MIN_STD:
                # Constant window: no dispersion information, emit neutral.
                normalized.append(0.0)
                continue
            z = (values[t] - statistics.mean(window)) / std_val
            normalized.append(max(min(z, clip), -clip))
        return normalized

    # ------------------------------------------------------------------
    # Weighting
    # ------------------------------------------------------------------
    @staticmethod
    def _solve_symmetric(system: List[List[float]], rhs: List[float]) -> Optional[List[float]]:
        """
        Solves a small dense linear system by Gaussian elimination with partial
        pivoting. Returns None if the system is numerically singular, which the
        caller treats as "this active set is unusable".
        """
        size = len(rhs)
        aug = [list(system[i]) + [rhs[i]] for i in range(size)]
        for col in range(size):
            pivot_row = max(range(col, size), key=lambda r: abs(aug[r][col]))
            if abs(aug[pivot_row][col]) <= _MIN_MSE:
                return None
            aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
            pivot = aug[col][col]
            for row in range(col + 1, size):
                factor = aug[row][col] / pivot
                if factor == 0.0:
                    continue
                for k in range(col, size + 1):
                    aug[row][k] -= factor * aug[col][k]
        solution = [0.0] * size
        for row in range(size - 1, -1, -1):
            acc = aug[row][size] - sum(aug[row][k] * solution[k] for k in range(row + 1, size))
            solution[row] = acc / aug[row][row]
        return solution

    @staticmethod
    def _solve_nnls(
        matrix: List[List[float]],
        target: List[float],
        ridge: float = _NNLS_RIDGE,
    ) -> List[float]:
        """
        Solves `min_w ||Zw - y||^2 + ridge * ||w||^2` subject to `w >= 0` using
        the Lawson & Hanson (1974) active-set algorithm on the Gram system. The
        active-set method terminates in a finite number of steps at the exact
        optimum, rather than approaching it asymptotically -- important here
        because near-duplicate sub-models make iterative solvers creep and
        return a weight vector that depends on the iteration budget.

        Highly correlated sub-models are the normal case (momentum and
        trend-follow signals are near-duplicates), so the Gram matrix is
        routinely near-singular. `ridge` adds Tikhonov damping scaled by the
        mean Gram diagonal, which keeps it unit-free: large enough to make a
        collinear fit unique and well-conditioned, small enough to leave a
        well-conditioned fit essentially untouched.

        Args:
            matrix: Column-major design; `matrix[i]` is sub-model `i`'s series.
            target: Realized target series.
            ridge: Relative Tikhonov damping. Pass 0.0 for an undamped fit.
        """
        n_models = len(matrix)
        if n_models == 0:
            return []
        gram = [[sum(a * b for a, b in zip(matrix[i], matrix[j])) for j in range(n_models)]
                for i in range(n_models)]
        rhs = [sum(a * b for a, b in zip(matrix[i], target)) for i in range(n_models)]

        if ridge > 0.0:
            damping = ridge * (sum(gram[i][i] for i in range(n_models)) / n_models)
            for i in range(n_models):
                gram[i][i] += damping

        # Scale-relative optimality tolerance; a raw absolute tolerance is
        # meaningless because the Gram entries carry the signals' units.
        scale = max(1.0, max(abs(v) for v in rhs), max(gram[i][i] for i in range(n_models)))
        tol = _NNLS_TOL * scale

        weights = [0.0] * n_models
        passive: List[int] = []
        active = [j for j in range(n_models) if gram[j][j] > _MIN_MSE]

        for _ in range(_NNLS_MAX_ITER):
            # Negative gradient of the objective at the current point.
            gradient = [
                rhs[j] - sum(gram[j][k] * weights[k] for k in range(n_models))
                for j in range(n_models)
            ]
            candidates = [j for j in active if gradient[j] > tol]
            if not candidates:
                break
            entering = max(candidates, key=lambda j: gradient[j])
            active.remove(entering)
            passive.append(entering)

            # Inner loop: solve on the passive set, backing off any coordinate
            # the unconstrained solution would push negative.
            singular = False
            for _ in range(_NNLS_MAX_ITER):
                sub = [[gram[i][j] for j in passive] for i in passive]
                trial = EnsembleSignalCombiner._solve_symmetric(sub, [rhs[i] for i in passive])
                if trial is None:
                    # Numerically singular passive set: retire the coordinate we
                    # just admitted rather than expanding into a rank-deficient
                    # subproblem.
                    singular = True
                    passive.remove(entering)
                    weights[entering] = 0.0
                    break
                if all(v > 0.0 for v in trial):
                    for idx, j in enumerate(passive):
                        weights[j] = trial[idx]
                    for j in active:
                        weights[j] = 0.0
                    break
                ratios = [
                    weights[j] / (weights[j] - trial[idx])
                    for idx, j in enumerate(passive)
                    if trial[idx] <= 0.0 and weights[j] > trial[idx]
                ]
                step = min(ratios) if ratios else 0.0
                for idx, j in enumerate(passive):
                    weights[j] += step * (trial[idx] - weights[j])
                for j in list(passive):
                    if weights[j] <= tol:
                        weights[j] = 0.0
                        passive.remove(j)
                        active.append(j)
                if not passive:
                    break
            if singular:
                # `entering` stays out of `active`, so the outer loop cannot
                # re-admit it and spin forever.
                continue
        else:
            logger.warning(
                "NNLS active-set search hit the %d-iteration cap; returning the best "
                "feasible weight vector found.", _NNLS_MAX_ITER,
            )
        return weights

    def _apply_weight_cap(self, weights: Dict[str, float]) -> Dict[str, float]:
        """
        Enforces `w_i <= max_weight_cap` while preserving `sum(w) == 1` by
        water-filling: pin the offenders at the cap, rescale the remaining
        models pro rata to absorb the freed budget, and repeat until no model
        breaches the cap. Models already pinned are excluded from later
        redistribution, otherwise mass ping-pongs between them and never
        converges. The cap is raised to 1/N when 1/N > cap, because no point on
        the simplex can satisfy a tighter cap.
        """
        n_models = len(weights)
        if n_models == 0:
            return {}
        equal_weight = 1.0 / n_models
        cap = max(self.max_weight_cap, equal_weight)
        if cap >= 1.0 - _CAP_TOL:
            return dict(weights)

        pinned: Dict[str, float] = {}
        free_names = list(weights)
        for _ in range(n_models):
            budget = 1.0 - cap * len(pinned)
            free_sum = sum(weights[name] for name in free_names)
            if budget <= _CAP_TOL or free_sum <= _CAP_TOL:
                # Nothing left to distribute, or every free model is at zero:
                # split the remaining budget evenly across the free models.
                share = max(budget, 0.0) / len(free_names) if free_names else 0.0
                scaled = {name: min(share, cap) for name in free_names}
            else:
                scaled = {name: weights[name] * budget / free_sum for name in free_names}

            breaching = [name for name, w in scaled.items() if w > cap + _CAP_TOL]
            if not breaching:
                pinned.update(scaled)
                break
            for name in breaching:
                pinned[name] = cap
            free_names = [name for name in free_names if name not in pinned]
            if not free_names:
                break

        if len(pinned) != n_models:
            logger.warning("Weight cap water-filling did not converge; using equal weights.")
            return {name: equal_weight for name in weights}

        total = sum(pinned.values())
        if total <= 0.0:
            return {name: equal_weight for name in weights}
        return {name: pinned[name] / total for name in weights}

    def compute_weights(
        self,
        signal_streams: List[SignalStream],
        target_returns: Optional[Sequence[float]] = None,
    ) -> Dict[str, float]:
        """
        Calculates the regularized, non-negative, capped weight vector.

        `signal_streams` are expected to already be normalized (see
        `normalize_zscore`). `target_returns` is the realized forward return
        series the sub-models are forecasting; it is required by every method
        except `EQUAL_WEIGHT`.
        """
        n_models = len(signal_streams)
        if n_models == 0:
            return {}

        names = [s.model_name for s in signal_streams]
        if len(set(names)) != n_models:
            raise EnsembleError(f"Duplicate model_name in signal streams: {names}.")

        equal_weight = 1.0 / n_models
        if self.method == EnsembleMethod.EQUAL_WEIGHT:
            return {name: equal_weight for name in names}

        if target_returns is None:
            raise EnsembleError(
                f"method {self.method.value} is fitted against realized outcomes and requires "
                "target_returns; pass the forward return series, or use EQUAL_WEIGHT."
            )
        _require_finite(target_returns, "target_returns")
        target = [float(v) for v in target_returns]
        n_samples = len(signal_streams[0].signals)
        if len(target) != n_samples:
            raise EnsembleError(
                f"target_returns length {len(target)} does not match signal length {n_samples}."
            )
        if n_samples < n_models + 1:
            raise EnsembleError(
                f"Insufficient observations to fit {n_models} weights: {n_samples} samples. "
                "Use EQUAL_WEIGHT until enough history accumulates."
            )

        raw_weights: Dict[str, float]
        if self.method == EnsembleMethod.INVERSE_VARIANCE:
            # Bates-Granger: w_i proportional to 1 / MSE_i. Each standardized
            # signal is first rescaled onto the target's units by its
            # least-squares slope through the origin, otherwise the MSE would
            # compare incommensurable quantities.
            inv_mse: Dict[str, float] = {}
            for stream in signal_streams:
                denom = sum(z * z for z in stream.signals)
                beta = (sum(z * y for z, y in zip(stream.signals, target)) / denom
                        if denom > _MIN_MSE else 0.0)
                mse = sum((beta * z - y) ** 2 for z, y in zip(stream.signals, target)) / n_samples
                inv_mse[stream.model_name] = 1.0 / max(mse, _MIN_MSE)
            total_inv = sum(inv_mse.values())
            raw_weights = {name: v / total_inv for name, v in inv_mse.items()}
        else:  # SHRUNK_NNLS
            solved = self._solve_nnls([s.signals for s in signal_streams], target)
            total_solved = sum(solved)
            if total_solved <= _MIN_MSE:
                # NNLS legitimately zeroes every sub-model when none of them has
                # non-negative explanatory power. Degrade to the 1/N baseline
                # rather than emitting an undefined weight vector.
                logger.warning(
                    "NNLS produced an all-zero weight vector (no sub-model has non-negative "
                    "explanatory power); falling back to equal weighting."
                )
                raw_weights = {name: equal_weight for name in names}
            else:
                raw_weights = {name: w / total_solved for name, w in zip(names, solved)}

        # 1/N shrinkage: w_shrunk = (1 - lambda) * w_raw + lambda * (1/N).
        # Both terms are non-negative and lambda is validated to [0, 1], so the
        # blend cannot introduce a negative weight.
        shrunk = {
            name: (1.0 - self.shrinkage_lambda) * w + self.shrinkage_lambda * equal_weight
            for name, w in raw_weights.items()
        }
        total_w = sum(shrunk.values())
        if total_w <= 0.0:
            raise EnsembleError("Shrunk weight vector summed to zero; cannot normalize.")
        normalized = {name: w / total_w for name, w in shrunk.items()}
        return self._apply_weight_cap(normalized)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def combine_signals(
        self,
        signal_streams: List[SignalStream],
        target_returns: Optional[Sequence[float]] = None,
    ) -> EnsembleResult:
        """
        Causally normalizes sub-model signals, computes regularized weights, and
        aggregates the composite signal.

        Because the weights are non-negative and sum to 1 and each normalized
        signal is clipped to [-clip, +clip], the composite is bounded by that
        same interval.

        Args:
            signal_streams: Raw (un-normalized) sub-model signal series, all of
                equal length and time-aligned.
            target_returns: Realized forward returns aligned to the signals.
                Required unless `method` is `EQUAL_WEIGHT`.
        """
        if not signal_streams:
            raise EnsembleError("No signal streams provided for ensembling.")

        n_samples = len(signal_streams[0].signals)
        if n_samples == 0:
            raise EnsembleError("Signal streams contain no observations.")
        for s in signal_streams:
            if len(s.signals) != n_samples:
                raise EnsembleError(
                    f"Signal length mismatch in '{s.model_name}': {len(s.signals)} vs {n_samples}."
                )

        # 1. Causally normalize each sub-model signal.
        norm_streams: List[SignalStream] = [
            SignalStream(
                model_name=s.model_name,
                signals=self.normalize_zscore(
                    s.signals,
                    lookback=self.lookback,
                    min_periods=self.min_periods,
                    clip=self.clip,
                ),
            )
            for s in signal_streams
        ]

        # 2. Compute regularized weights from the normalized streams.
        weights = self.compute_weights(norm_streams, target_returns=target_returns)

        # 3. Aggregate the composite signal.
        ensemble_sigs = [
            round(sum(weights[s.model_name] * s.signals[i] for s in norm_streams), 4)
            for i in range(n_samples)
        ]

        logger.info(
            "Ensemble combined: %d models, %d samples, method=%s, max_weight=%.4f.",
            len(signal_streams), n_samples, self.method.value,
            max(weights.values()) if weights else 0.0,
        )
        return EnsembleResult(
            ensemble_signals=ensemble_sigs,
            weights=weights,
            method=self.method,
            is_normalized=True,
        )
