"""
online-learning-for-adaptive-signal-models: incremental estimators that keep a
linear trading signal model current on a live stream, without a batch refit.

Design notes
------------
* **Three update rules, one interface.** ``"lms"`` is the plain Widrow-Hoff /
  SGD rule this module has always implemented. ``"nlms"`` divides the step by
  the instantaneous input energy. ``"rls"`` is recursive least squares with an
  exponential forgetting factor. They differ in exactly one place -- how the
  weight increment is computed from the a priori error -- and share the
  validation, projection, drift and persistence machinery.

* **Why the normalised rule exists.** After an LMS step the a posteriori error
  is ``e_post = e * (1 - eta * ||x||^2)``: a single step reduces the error only
  while ``0 < eta * ||x||^2 < 2``. ``eta`` is fixed at construction, ``||x||^2``
  is a property of the live feature vector, so on unscaled or heavy-tailed
  market features that product is not bounded by anything the caller chose.
  NLMS makes the product identically ``mu`` (``mu * ||x||^2 / (eps + ||x||^2)``),
  which is why its stability region ``0 < mu < 2`` is independent of the signal
  statistics -- Haykin, *Adaptive Filter Theory*; see `references/standards.md`.
  Under ``"lms"`` the ratio is measured on every update and reported, never
  silently ignored.

* **RLS with exponential forgetting** (``0 < lambda <= 1``)::

      k = P x / (lambda + x' P x)
      w = w + k (y - x'w)
      P = (P - k x' P) / lambda

  The weight on an observation ``j`` steps in the past is ``lambda^j``, so the
  estimator's effective memory is ``1 / (1 - lambda)`` observations.
  ``lambda < 1`` also means old information is discarded whether or not new
  information arrives: under poor excitation ``P`` grows without bound
  ("covariance windup"). A trace limit freezes the covariance update rather
  than letting it blow the estimator up.

* **Look-ahead is structurally refused, not documented away.** A forward-return
  label for time ``t`` is not observable until ``t + h``. ``update()`` refuses a
  label whose ``label_ready_time`` is after ``now``, and ``LabelHorizonBuffer``
  holds feature vectors until their labels are actually due.

* **Non-finite input is rejected before it can touch the weights.** A single NaN
  tick used to convert every weight to NaN permanently and silently, because
  ``nan > max_norm`` is ``False`` and the norm projection therefore never fired.
  Every scalar is checked on the way in, and every updated weight vector is
  checked before it is installed.

* **Bounded memory.** Nothing here grows with the length of the stream. A live
  model runs for months; the error history is a fixed baseline sample plus a
  fixed-length recent window.

* **Nothing is simulated.** Every number in ``OnlineModelAuditReport`` is
  computed from observations the caller actually supplied.
"""
from collections import deque
from dataclasses import dataclass
import logging
import math
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Plain Widrow-Hoff / stochastic gradient rule. The stable step size depends on
#: the input energy; see ``step_ratio`` on :class:`OnlinePredictionResult`.
LMS = "lms"
#: Energy-normalised gradient rule. Stability region ``0 < mu < 2``.
NLMS = "nlms"
#: Recursive least squares with exponential forgetting.
RLS = "rls"

UPDATE_RULES = (LMS, NLMS, RLS)

#: ``eta * ||x||^2 >= 2`` magnifies the a posteriori error instead of reducing
#: it. Derived, not tuned -- see the module docstring.
DIVERGENT_STEP_RATIO = 2.0

_STATE_VERSION = 2


class OnlineLearningError(ValueError):
    """Raised on malformed configuration, non-finite data, a look-ahead
    violation, or a numerically diverged update."""


def _finite(value: Any, name: str) -> float:
    """Coerce ``value`` to a finite ``float`` or raise :class:`OnlineLearningError`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OnlineLearningError(f"{name} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise OnlineLearningError(f"{name} must be finite, got {value!r}.")
    return numeric


def _positive(value: Any, name: str) -> float:
    numeric = _finite(value, name)
    if numeric <= 0.0:
        raise OnlineLearningError(f"{name} must be strictly positive, got {numeric}.")
    return numeric


def _non_negative(value: Any, name: str) -> float:
    numeric = _finite(value, name)
    if numeric < 0.0:
        raise OnlineLearningError(f"{name} must be non-negative, got {numeric}.")
    return numeric


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OnlineLearningError(f"{name} must be an int, got {value!r}.")
    if value <= 0:
        raise OnlineLearningError(f"{name} must be strictly positive, got {value}.")
    return value


@dataclass(frozen=True)
class DriftSignal:
    """Outcome of one Page-Hinkley observation."""

    drift_detected: bool
    statistic: float
    samples_seen: int
    running_mean: float


class PageHinkleyDetector:
    """One-sided Page-Hinkley test for an **increase** in the mean of a stream.

    Sequential change detection on the model's own absolute error
    (Page, *Biometrika* 41, 1954; Gama et al., *ACM Computing Surveys* 46(4),
    2014, Article 44)::

        mean_t       <- mean_{t-1} + (x_t - mean_{t-1}) / t
        cumulative_t <- cumulative_{t-1} + (x_t - mean_t - delta)
        minimum_t    <- min(minimum_{t-1}, cumulative_t)
        PH_t          = cumulative_t - minimum_t          -> drift if > threshold

    ``delta`` and ``threshold`` carry **the units of the monitored quantity** and
    deliberately have no defaults here. Absolute errors on a 5-minute forward
    return live around 1e-3; a ``threshold`` copied from a library whose defaults
    were chosen for classification error rates would never fire, and a detector
    that never fires is worse than no detector because it reads as reassurance.

    A decrease in error is never signalled -- this is a one-sided test.
    """

    def __init__(self, delta: float, threshold: float, min_samples: int = 30) -> None:
        self.delta = _non_negative(delta, "delta")
        self.threshold = _positive(threshold, "threshold")
        self.min_samples = _positive_int(min_samples, "min_samples")
        self._n = 0
        self._mean = 0.0
        self._cumulative = 0.0
        self._minimum = 0.0

    def reset(self) -> None:
        """Discard accumulated evidence and restart the test."""
        self._n = 0
        self._mean = 0.0
        self._cumulative = 0.0
        self._minimum = 0.0

    @property
    def statistic(self) -> float:
        return self._cumulative - self._minimum

    @property
    def samples_seen(self) -> int:
        return self._n

    def observe(self, value: float) -> DriftSignal:
        """Feed one observation.

        Auto-resets on detection so that a single change produces a single
        signal rather than one signal per subsequent sample.
        """
        observation = _finite(value, "value")
        self._n += 1
        self._mean += (observation - self._mean) / self._n
        self._cumulative += observation - self._mean - self.delta
        if self._cumulative < self._minimum:
            self._minimum = self._cumulative
        statistic = self._cumulative - self._minimum
        detected = self._n >= self.min_samples and statistic > self.threshold
        signal = DriftSignal(detected, statistic, self._n, self._mean)
        if detected:
            self.reset()
        return signal

    def to_state(self) -> Dict[str, Any]:
        return {
            "delta": self.delta,
            "threshold": self.threshold,
            "min_samples": self.min_samples,
            "n": self._n,
            "mean": self._mean,
            "cumulative": self._cumulative,
            "minimum": self._minimum,
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "PageHinkleyDetector":
        detector = cls(state["delta"], state["threshold"], state["min_samples"])
        seen = state["n"]
        if isinstance(seen, bool) or not isinstance(seen, int) or seen < 0:
            raise OnlineLearningError(f"n must be a non-negative int, got {seen!r}.")
        detector._n = seen
        detector._mean = _finite(state["mean"], "mean")
        detector._cumulative = _finite(state["cumulative"], "cumulative")
        detector._minimum = _finite(state["minimum"], "minimum")
        return detector


@dataclass(frozen=True)
class PendingSample:
    """A feature vector whose forward label has not been realised yet."""

    features: List[float]
    feature_time: float
    label_ready_time: float


class LabelHorizonBuffer:
    """FIFO of feature vectors held until their forward labels are observable.

    This is the mechanical form of workflow step 2. Times are opaque
    monotonically increasing numbers -- epoch seconds, bar indices, anything the
    caller compares consistently. ``label_ready_time`` must be non-decreasing
    across ``enqueue`` calls; out-of-order labels would make FIFO release wrong,
    so they raise rather than silently reorder the training stream.
    """

    def __init__(self, max_pending: int = 10_000) -> None:
        self.max_pending = _positive_int(max_pending, "max_pending")
        self._queue: Deque[PendingSample] = deque()
        self._last_label_time: Optional[float] = None

    def __len__(self) -> int:
        return len(self._queue)

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def oldest_label_ready_time(self) -> Optional[float]:
        return self._queue[0].label_ready_time if self._queue else None

    def enqueue(
        self,
        features: Sequence[float],
        feature_time: float,
        label_ready_time: float,
    ) -> PendingSample:
        observed = _finite(feature_time, "feature_time")
        ready = _finite(label_ready_time, "label_ready_time")
        if ready < observed:
            raise OnlineLearningError(
                f"label_ready_time ({ready}) precedes feature_time ({observed}); "
                "a forward label cannot be realised before its features exist."
            )
        if self._last_label_time is not None and ready < self._last_label_time:
            raise OnlineLearningError(
                f"label_ready_time ({ready}) is earlier than the previously "
                f"enqueued label ({self._last_label_time}); the buffer releases "
                "in FIFO order and requires non-decreasing label times."
            )
        if len(self._queue) >= self.max_pending:
            raise OnlineLearningError(
                f"label horizon buffer is full ({self.max_pending} pending). "
                "Labels are not being released -- check the outcome feed rather "
                "than letting the queue grow without bound."
            )
        sample = PendingSample(
            [_finite(x, "features") for x in features], observed, ready
        )
        self._queue.append(sample)
        self._last_label_time = ready
        return sample

    def release_due(self, now: float) -> List[PendingSample]:
        """Pop every sample whose label is realised at or before ``now``."""
        current = _finite(now, "now")
        due: List[PendingSample] = []
        while self._queue and self._queue[0].label_ready_time <= current:
            due.append(self._queue.popleft())
        return due

    def clear(self) -> None:
        self._queue.clear()
        self._last_label_time = None


@dataclass
class OnlinePredictionResult:
    """One inference-plus-update cycle.

    Values are unrounded on purpose: a 5-minute forward return lives near 1e-4,
    and rounding a returned prediction to 6 decimals discards significant digits
    a position sizer needs.
    """

    sample_index: int
    predicted_y: float
    actual_y: Optional[float]
    prediction_error: Optional[float]
    weights_norm: float
    update_rule: str = LMS
    effective_step: Optional[float] = None
    step_ratio: Optional[float] = None
    weights_clipped: bool = False
    drift_detected: bool = False
    drift_statistic: Optional[float] = None


@dataclass
class OnlineModelAuditReport:
    """Comparison of a fixed early baseline against the current rolling window.

    ``is_converged`` is a point comparison of two MAEs with **no** significance
    test and no confidence bound; it says the recent window scored better than
    the baseline window, and nothing more. For a bounded accuracy monitor wired
    to position sizing, use `model-staleness-detection`.
    """

    total_samples_processed: int
    initial_mae: float
    final_mae: float
    mae_improvement_pct: float
    weights: List[float]
    is_converged: bool
    message: str
    update_rule: str = LMS
    baseline_sample_count: int = 0
    recent_sample_count: int = 0
    weights_norm: float = 0.0
    clipped_update_count: int = 0
    unstable_step_count: int = 0
    drift_detection_count: int = 0
    effective_memory_samples: Optional[float] = None
    covariance_trace: Optional[float] = None
    sufficient_samples: bool = False


class OnlineAdaptiveSignalModel:
    """Incremental linear signal model with LMS / NLMS / RLS update rules,
    L2-ball weight projection, Page-Hinkley drift detection and bounded memory.

    The model has **no intercept**: predictions pass through the origin. Centre
    the target (a forward return already is) or append a constant feature.

    Args:
        num_features: Length of every feature vector. Must be >= 1.
        learning_rate: ``eta`` for ``"lms"``, ``mu`` for ``"nlms"`` (which
            requires ``0 < mu < 2``). Ignored by ``"rls"``.
        l2_penalty: Weight-leakage coefficient for the gradient rules; the
            update becomes ``w <- (1 - eta * l2) * w + step * e * x``. Ignored by
            ``"rls"``, whose only regularisation is the ``P`` initialisation.
        max_weight_norm: Radius of the L2 ball the weights are projected onto
            after every update (projected online gradient descent).
        update_rule: One of :data:`UPDATE_RULES`.
        nlms_epsilon: Denominator floor for ``"nlms"``; prevents division by
            zero on an all-zero feature vector.
        forgetting_factor: ``lambda`` for ``"rls"``, in ``(0, 1]``. Effective
            memory is ``1 / (1 - lambda)`` observations.
        rls_initial_covariance: ``P_0 = c * I``. Large ``c`` means a diffuse
            prior and fast initial adaptation. It is equivalent to a ridge prior
            of weight ``1 / c`` whose influence decays as ``lambda^t``; it is not
            a standing penalty.
        rls_max_covariance_trace: Trace limit that freezes the ``P`` update
            during poor excitation instead of letting covariance windup blow the
            estimator up.
        baseline_window: Number of early absolute errors retained as the audit
            baseline. Conventional, not derived.
        recent_window: Length of the rolling absolute-error window.
        drift_detector: Optional :class:`PageHinkleyDetector` fed the absolute
            error of every applied update.
        reset_covariance_on_drift: For ``"rls"``, restore ``P_0`` when drift is
            signalled, recovering plasticity without discarding the estimate.
    """

    def __init__(
        self,
        num_features: int,
        learning_rate: float = 0.01,
        l2_penalty: float = 0.001,
        max_weight_norm: float = 10.0,
        *,
        update_rule: str = LMS,
        nlms_epsilon: float = 1e-8,
        forgetting_factor: float = 0.99,
        rls_initial_covariance: float = 1e4,
        rls_max_covariance_trace: float = 1e8,
        baseline_window: int = 50,
        recent_window: int = 100,
        drift_detector: Optional[PageHinkleyDetector] = None,
        reset_covariance_on_drift: bool = False,
    ) -> None:
        if update_rule not in UPDATE_RULES:
            raise OnlineLearningError(
                f"update_rule must be one of {UPDATE_RULES}, got {update_rule!r}."
            )
        self.num_features = _positive_int(num_features, "num_features")
        self.update_rule = update_rule
        self.eta = _positive(learning_rate, "learning_rate")
        if update_rule == NLMS and not self.eta < DIVERGENT_STEP_RATIO:
            raise OnlineLearningError(
                f"nlms requires 0 < learning_rate < {DIVERGENT_STEP_RATIO}; the "
                "normalised step ratio equals learning_rate exactly, so "
                f"{self.eta} diverges regardless of feature scale."
            )
        self.l2 = _non_negative(l2_penalty, "l2_penalty")
        if update_rule != RLS and self.eta * self.l2 >= 1.0:
            # retention = 1 - eta*l2 would be <= 0, so the leakage term alone
            # would flip the sign of every weight on every update.
            raise OnlineLearningError(
                f"learning_rate * l2_penalty must be < 1 for the gradient rules "
                f"(got {self.eta * self.l2}); at or above 1 the leakage factor "
                "1 - eta*l2 inverts the weights on every update."
            )
        self.max_norm = _positive(max_weight_norm, "max_weight_norm")
        self.nlms_epsilon = _positive(nlms_epsilon, "nlms_epsilon")

        self.forgetting_factor = _finite(forgetting_factor, "forgetting_factor")
        if not 0.0 < self.forgetting_factor <= 1.0:
            raise OnlineLearningError(
                f"forgetting_factor must lie in (0, 1], got {self.forgetting_factor}."
            )
        self.rls_initial_covariance = _positive(
            rls_initial_covariance, "rls_initial_covariance"
        )
        self.rls_max_covariance_trace = _positive(
            rls_max_covariance_trace, "rls_max_covariance_trace"
        )
        initial_trace = self.num_features * self.rls_initial_covariance
        if update_rule == RLS and self.rls_max_covariance_trace <= initial_trace:
            # Otherwise the windup guard fires on sample 1 and freezes P forever,
            # silently degrading RLS into a fixed-gain filter that never adapts.
            raise OnlineLearningError(
                f"rls_max_covariance_trace ({self.rls_max_covariance_trace}) must "
                f"exceed the initial trace num_features * rls_initial_covariance "
                f"({initial_trace}); otherwise the windup guard freezes P before "
                "the first update."
            )

        self.baseline_window = _positive_int(baseline_window, "baseline_window")
        self.recent_window = _positive_int(recent_window, "recent_window")
        self.reset_covariance_on_drift = bool(reset_covariance_on_drift)
        if drift_detector is not None and not isinstance(drift_detector, PageHinkleyDetector):
            raise OnlineLearningError(
                "drift_detector must be a PageHinkleyDetector or None, got "
                f"{type(drift_detector).__name__}."
            )
        self.drift_detector = drift_detector

        if update_rule == RLS and self.l2 > 0.0:
            logger.warning(
                "l2_penalty=%s is ignored by the rls update rule; RLS "
                "regularisation enters through rls_initial_covariance "
                "(P_0 = c*I) and decays as lambda^t.",
                self.l2,
            )

        self.weights: List[float] = [0.0] * self.num_features
        self._covariance: Optional[List[List[float]]] = (
            self._fresh_covariance() if update_rule == RLS else None
        )

        self.total_samples = 0
        self.clipped_update_count = 0
        self.unstable_step_count = 0
        self.drift_detection_count = 0
        self.covariance_frozen_count = 0
        self._baseline_errors: List[float] = []
        self._recent_errors: Deque[float] = deque(maxlen=self.recent_window)

    # ---------------------------------------------------------------- helpers

    def _fresh_covariance(self) -> List[List[float]]:
        c = self.rls_initial_covariance
        n = self.num_features
        return [[c if i == j else 0.0 for j in range(n)] for i in range(n)]

    def _validate_features(self, features: Sequence[float]) -> List[float]:
        try:
            length = len(features)
        except TypeError as exc:
            raise OnlineLearningError(
                f"features must be a sized sequence, got {type(features).__name__}."
            ) from exc
        if length != self.num_features:
            raise OnlineLearningError(
                f"Expected {self.num_features} features, got {length}."
            )
        return [_finite(x, f"features[{i}]") for i, x in enumerate(features)]

    @property
    def effective_memory_samples(self) -> Optional[float]:
        """``1 / (1 - lambda)`` for RLS; ``None`` for the gradient rules, whose
        memory is not expressible as a sample count."""
        if self.update_rule != RLS:
            return None
        if self.forgetting_factor >= 1.0:
            return math.inf
        return 1.0 / (1.0 - self.forgetting_factor)

    @property
    def covariance_trace(self) -> Optional[float]:
        if self._covariance is None:
            return None
        return sum(self._covariance[i][i] for i in range(self.num_features))

    def weights_norm(self) -> float:
        # math.hypot uses the scaled algorithm: it does not overflow for weights
        # whose squares would, so a diverged-but-finite vector still yields a
        # finite norm the projection can act on.
        return math.hypot(*self.weights)

    # -------------------------------------------------------------- inference

    def predict(self, features: Sequence[float]) -> float:
        """Pure inference. Never mutates the model."""
        vector = self._validate_features(features)
        return sum(w * x for w, x in zip(self.weights, vector))

    # ----------------------------------------------------------------- update

    def update(
        self,
        features: Sequence[float],
        target: float,
        *,
        label_ready_time: Optional[float] = None,
        now: Optional[float] = None,
    ) -> OnlinePredictionResult:
        """Apply one online update from a **realised** label.

        Pass ``label_ready_time`` and ``now`` together to have the horizon
        enforced: a label dated after ``now`` has not happened yet and the update
        is refused. Supplying only one of the two is a configuration mistake and
        raises, rather than silently skipping the check.
        """
        if (label_ready_time is None) != (now is None):
            raise OnlineLearningError(
                "label_ready_time and now must be supplied together; passing one "
                "alone silently disables the look-ahead check."
            )
        if label_ready_time is not None and now is not None:
            ready = _finite(label_ready_time, "label_ready_time")
            current = _finite(now, "now")
            if ready > current:
                raise OnlineLearningError(
                    f"label is not realised yet: label_ready_time={ready} > "
                    f"now={current}. Updating on it would train the live model on "
                    "the future. Hold the sample in a LabelHorizonBuffer instead."
                )

        vector = self._validate_features(features)
        y = _finite(target, "target")

        prediction = sum(w * x for w, x in zip(self.weights, vector))
        error = y - prediction
        energy = sum(x * x for x in vector)

        if self.update_rule == RLS:
            new_weights, effective_step, step_ratio = self._rls_step(vector, error)
        else:
            new_weights, effective_step, step_ratio = self._gradient_step(
                vector, error, energy
            )

        for i, w in enumerate(new_weights):
            if not math.isfinite(w):
                raise OnlineLearningError(
                    f"update diverged: weight[{i}] became {w}. The previous weights "
                    "are retained. Reduce learning_rate, switch update_rule to "
                    f"{NLMS!r}, or scale the features."
                )
        self.weights = new_weights

        clipped = self._project_onto_norm_ball()
        if clipped:
            self.clipped_update_count += 1

        if step_ratio is not None and step_ratio >= DIVERGENT_STEP_RATIO:
            self.unstable_step_count += 1
            if self.unstable_step_count == 1 or self.unstable_step_count % 100 == 0:
                logger.warning(
                    "step ratio %.4g >= %.1f on sample %d (%d occurrences): this "
                    "update magnified the error instead of reducing it. Lower "
                    "learning_rate or use the %r rule.",
                    step_ratio,
                    DIVERGENT_STEP_RATIO,
                    self.total_samples + 1,
                    self.unstable_step_count,
                    NLMS,
                )

        self.total_samples += 1
        absolute_error = abs(error)
        if len(self._baseline_errors) < self.baseline_window:
            self._baseline_errors.append(absolute_error)
        self._recent_errors.append(absolute_error)

        drift_detected = False
        drift_statistic: Optional[float] = None
        if self.drift_detector is not None:
            signal = self.drift_detector.observe(absolute_error)
            drift_detected = signal.drift_detected
            drift_statistic = signal.statistic
            if drift_detected:
                self.drift_detection_count += 1
                logger.warning(
                    "Page-Hinkley drift signalled at sample %d "
                    "(statistic=%.6g, threshold=%.6g).",
                    self.total_samples,
                    signal.statistic,
                    self.drift_detector.threshold,
                )
                if self.reset_covariance_on_drift and self.update_rule == RLS:
                    self.reset_covariance()

        return OnlinePredictionResult(
            sample_index=self.total_samples,
            predicted_y=prediction,
            actual_y=y,
            prediction_error=error,
            weights_norm=self.weights_norm(),
            update_rule=self.update_rule,
            effective_step=effective_step,
            step_ratio=step_ratio,
            weights_clipped=clipped,
            drift_detected=drift_detected,
            drift_statistic=drift_statistic,
        )

    def _gradient_step(
        self, vector: List[float], error: float, energy: float
    ) -> Tuple[List[float], float, float]:
        """LMS / NLMS: ``w <- (1 - eta*l2) * w + step * error * x``.

        With ``l2 = 0`` the a posteriori error is ``error * (1 - step * energy)``,
        so ``step_ratio = step * energy`` must stay below
        :data:`DIVERGENT_STEP_RATIO`. Under NLMS that product is ``mu`` up to
        ``nlms_epsilon``, which is why NLMS is scale-free.
        """
        if self.update_rule == NLMS:
            step = self.eta / (self.nlms_epsilon + energy)
        else:
            step = self.eta
        retention = 1.0 - self.eta * self.l2
        step_ratio = step * energy
        return (
            [retention * w + step * error * x for w, x in zip(self.weights, vector)],
            step,
            step_ratio,
        )

    def _rls_step(
        self, vector: List[float], error: float
    ) -> Tuple[List[float], Optional[float], Optional[float]]:
        """One RLS-with-forgetting recursion, with a covariance windup guard."""
        covariance = self._covariance
        if covariance is None:  # pragma: no cover - guaranteed by __init__
            raise OnlineLearningError("rls update rule requires an initialised covariance.")
        n = self.num_features
        lam = self.forgetting_factor

        # Px = P @ x. P is symmetric, so x' P == (P x)'.
        px = [sum(covariance[i][j] * vector[j] for j in range(n)) for i in range(n)]
        denominator = lam + sum(vector[i] * px[i] for i in range(n))
        if denominator <= 0.0 or not math.isfinite(denominator):
            logger.error(
                "RLS gain denominator is %r; the covariance has lost positive "
                "definiteness. Skipping this weight update and resetting P.",
                denominator,
            )
            self._covariance = self._fresh_covariance()
            return list(self.weights), None, None

        gain = [value / denominator for value in px]
        new_weights = [w + g * error for w, g in zip(self.weights, gain)]

        # P <- (P - gain * (x' P)) / lambda, then symmetrise to contain the
        # asymmetry finite-precision arithmetic accumulates in P.
        new_covariance = [
            [(covariance[i][j] - gain[i] * px[j]) / lam for j in range(n)]
            for i in range(n)
        ]
        for i in range(n):
            for j in range(i + 1, n):
                averaged = 0.5 * (new_covariance[i][j] + new_covariance[j][i])
                new_covariance[i][j] = averaged
                new_covariance[j][i] = averaged

        if not all(math.isfinite(v) for row in new_covariance for v in row):
            logger.error(
                "RLS covariance became non-finite; resetting P to its initial value."
            )
            self._covariance = self._fresh_covariance()
            return new_weights, None, None

        trace = sum(new_covariance[i][i] for i in range(n))
        if trace > self.rls_max_covariance_trace:
            # Covariance windup: with lambda < 1, old information is discounted
            # every step whether or not new information arrives. Freezing P is
            # the trace-limited remedy -- see references/standards.md.
            self.covariance_frozen_count += 1
            if (
                self.covariance_frozen_count == 1
                or self.covariance_frozen_count % 100 == 0
            ):
                logger.warning(
                    "RLS covariance trace %.6g exceeds the limit %.6g on sample %d "
                    "(%d occurrences): excitation is insufficient for "
                    "forgetting_factor=%.4f. Freezing P.",
                    trace,
                    self.rls_max_covariance_trace,
                    self.total_samples + 1,
                    self.covariance_frozen_count,
                    lam,
                )
        else:
            self._covariance = new_covariance

        return new_weights, None, None

    def _project_onto_norm_ball(self) -> bool:
        """Project the weights onto ``||w||_2 <= max_norm`` (projected online
        gradient descent). Returns whether the projection was active.

        This bounds the whole vector, not each component, and it is not gradient
        clipping -- the gradient is applied in full and the *result* is pulled
        back onto the ball.
        """
        norm = math.hypot(*self.weights)
        if not math.isfinite(norm):
            raise OnlineLearningError(
                f"weight norm is {norm}; the model has diverged numerically."
            )
        if norm <= self.max_norm or norm == 0.0:
            return False
        scale = self.max_norm / norm
        self.weights = [w * scale for w in self.weights]
        return True

    # ------------------------------------------------------------ maintenance

    def reset_covariance(self) -> None:
        """Restore ``P_0`` for RLS, recovering plasticity after a drift signal
        without discarding the current weight estimate. No-op for other rules."""
        if self.update_rule != RLS:
            return
        self._covariance = self._fresh_covariance()
        logger.info(
            "RLS covariance reset to P_0 = %g * I.", self.rls_initial_covariance
        )

    # ------------------------------------------------------------------ audit

    def audit_performance(self) -> OnlineModelAuditReport:
        """Compare the fixed early-baseline MAE against the rolling recent MAE.

        The two windows are disjoint by construction, so the report is refused
        until ``baseline_window + recent_window`` samples have been applied. The
        pre-2.0 audit compared the first quarter of the *entire* history against
        the last quarter, which meant a different quantity at every sample count
        and required retaining every error ever seen.
        """
        n = self.total_samples
        required = self.baseline_window + self.recent_window
        weights_snapshot = list(self.weights)

        if n < required or not self._baseline_errors or not self._recent_errors:
            message = (
                f"Insufficient samples for a disjoint-window audit: {n} seen, "
                f"{required} required (baseline_window={self.baseline_window} + "
                f"recent_window={self.recent_window})."
            )
            logger.info(message)
            return OnlineModelAuditReport(
                total_samples_processed=n,
                initial_mae=0.0,
                final_mae=0.0,
                mae_improvement_pct=0.0,
                weights=weights_snapshot,
                is_converged=False,
                message=message,
                update_rule=self.update_rule,
                baseline_sample_count=len(self._baseline_errors),
                recent_sample_count=len(self._recent_errors),
                weights_norm=self.weights_norm(),
                clipped_update_count=self.clipped_update_count,
                unstable_step_count=self.unstable_step_count,
                drift_detection_count=self.drift_detection_count,
                effective_memory_samples=self.effective_memory_samples,
                covariance_trace=self.covariance_trace,
                sufficient_samples=False,
            )

        initial_mae = sum(self._baseline_errors) / len(self._baseline_errors)
        final_mae = sum(self._recent_errors) / len(self._recent_errors)

        if initial_mae > 0.0:
            improvement = ((initial_mae - final_mae) / initial_mae) * 100.0
        else:
            # A zero baseline MAE makes a percentage change undefined. The
            # pre-2.0 code divided by a 1e-4 floor and returned a large
            # fabricated number instead.
            improvement = 0.0

        converged = final_mae < initial_mae
        message = (
            f"Online adaptation audit ({n} samples, rule={self.update_rule}): "
            f"baseline MAE={initial_mae:.6g} over {len(self._baseline_errors)} "
            f"samples -> recent MAE={final_mae:.6g} over "
            f"{len(self._recent_errors)} samples ({improvement:.1f}% change). "
            f"clipped={self.clipped_update_count}, "
            f"unstable_steps={self.unstable_step_count}, "
            f"drift_signals={self.drift_detection_count}."
        )
        logger.info(message)

        return OnlineModelAuditReport(
            total_samples_processed=n,
            initial_mae=initial_mae,
            final_mae=final_mae,
            mae_improvement_pct=improvement,
            weights=weights_snapshot,
            is_converged=converged,
            message=message,
            update_rule=self.update_rule,
            baseline_sample_count=len(self._baseline_errors),
            recent_sample_count=len(self._recent_errors),
            weights_norm=self.weights_norm(),
            clipped_update_count=self.clipped_update_count,
            unstable_step_count=self.unstable_step_count,
            drift_detection_count=self.drift_detection_count,
            effective_memory_samples=self.effective_memory_samples,
            covariance_trace=self.covariance_trace,
            sufficient_samples=True,
        )

    # ------------------------------------------------------------ persistence

    def to_state(self) -> Dict[str, Any]:
        """JSON-serialisable snapshot.

        A restarted process that reloads this resumes the adaptation it had; one
        that does not silently restarts from zero weights and trades a signal it
        has not learned yet.
        """
        return {
            "state_version": _STATE_VERSION,
            "num_features": self.num_features,
            "update_rule": self.update_rule,
            "learning_rate": self.eta,
            "l2_penalty": self.l2,
            "max_weight_norm": self.max_norm,
            "nlms_epsilon": self.nlms_epsilon,
            "forgetting_factor": self.forgetting_factor,
            "rls_initial_covariance": self.rls_initial_covariance,
            "rls_max_covariance_trace": self.rls_max_covariance_trace,
            "baseline_window": self.baseline_window,
            "recent_window": self.recent_window,
            "reset_covariance_on_drift": self.reset_covariance_on_drift,
            "weights": list(self.weights),
            "covariance": (
                [list(row) for row in self._covariance]
                if self._covariance is not None
                else None
            ),
            "total_samples": self.total_samples,
            "clipped_update_count": self.clipped_update_count,
            "unstable_step_count": self.unstable_step_count,
            "drift_detection_count": self.drift_detection_count,
            "covariance_frozen_count": self.covariance_frozen_count,
            "baseline_errors": list(self._baseline_errors),
            "recent_errors": list(self._recent_errors),
            "drift_detector": (
                self.drift_detector.to_state()
                if self.drift_detector is not None
                else None
            ),
        }

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "OnlineAdaptiveSignalModel":
        """Rebuild a model from :meth:`to_state`, validating every field."""
        if not isinstance(state, dict):
            raise OnlineLearningError(
                f"state must be a dict, got {type(state).__name__}."
            )
        version = state.get("state_version")
        if version != _STATE_VERSION:
            raise OnlineLearningError(
                f"unsupported state_version {version!r}; this build writes and "
                f"reads version {_STATE_VERSION}."
            )
        try:
            detector_state = state["drift_detector"]
            model = cls(
                num_features=state["num_features"],
                learning_rate=state["learning_rate"],
                l2_penalty=state["l2_penalty"],
                max_weight_norm=state["max_weight_norm"],
                update_rule=state["update_rule"],
                nlms_epsilon=state["nlms_epsilon"],
                forgetting_factor=state["forgetting_factor"],
                rls_initial_covariance=state["rls_initial_covariance"],
                rls_max_covariance_trace=state["rls_max_covariance_trace"],
                baseline_window=state["baseline_window"],
                recent_window=state["recent_window"],
                drift_detector=(
                    PageHinkleyDetector.from_state(detector_state)
                    if detector_state is not None
                    else None
                ),
                reset_covariance_on_drift=bool(state["reset_covariance_on_drift"]),
            )
            weights = state["weights"]
            covariance = state["covariance"]
            counters = {
                key: state[key]
                for key in (
                    "total_samples",
                    "clipped_update_count",
                    "unstable_step_count",
                    "drift_detection_count",
                    "covariance_frozen_count",
                )
            }
            baseline_errors = state["baseline_errors"]
            recent_errors = state["recent_errors"]
        except KeyError as exc:
            raise OnlineLearningError(f"state is missing required key {exc}.") from exc

        if len(weights) != model.num_features:
            raise OnlineLearningError(
                f"state has {len(weights)} weights but num_features="
                f"{model.num_features}; refusing to load a mismatched model."
            )
        model.weights = [_finite(w, "weights") for w in weights]

        if model.update_rule == RLS:
            if covariance is None or len(covariance) != model.num_features:
                raise OnlineLearningError(
                    "rls state must carry a square covariance of side num_features."
                )
            for row in covariance:
                if len(row) != model.num_features:
                    raise OnlineLearningError(
                        "covariance rows must all be num_features long."
                    )
            model._covariance = [
                [_finite(v, "covariance") for v in row] for row in covariance
            ]

        for key, value in counters.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise OnlineLearningError(
                    f"{key} must be a non-negative int, got {value!r}."
                )
            setattr(model, key, value)

        model._baseline_errors = [
            _finite(e, "baseline_errors") for e in baseline_errors
        ][: model.baseline_window]
        model._recent_errors = deque(
            (_finite(e, "recent_errors") for e in recent_errors),
            maxlen=model.recent_window,
        )
        return model
