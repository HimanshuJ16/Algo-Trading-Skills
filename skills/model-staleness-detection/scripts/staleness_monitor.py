"""
model-staleness-detection:
Continuous health monitoring for an ML signal model that is already live, so
that a model which quietly stopped working stops sizing positions.

The module owns exactly one thing: **turning an ongoing stream of realised
prediction outcomes and live feature values into an operational health state
and a position-sizing multiplier**. It does not fetch data, retrain, place or
cancel orders, or page anyone; ``alert_fn`` is a notification hook and the
sizing multiplier is advice the caller applies.

Two independent signals are tracked, because they fail at different times:

  - **Rolling realised accuracy** - what actually happened. Lags by the label
    horizon (a prediction cannot be scored until its outcome realises), so it
    is the *late* signal.
  - **Feature distribution drift (PSI)** - the input distribution moving away
    from the one the model was fitted on. Available immediately, so it is the
    *early* signal, at the cost of not proving the model is wrong.

Seven properties distinguish this from "track a hit rate and compare a mean to
a threshold". Each exists because the naive version produces a silent false
*negative* - a monitor reporting HEALTHY at 1.0x sizing while the model is
broken - or an unusable false positive:

  1. **PSI is the real Population Stability Index.** ``sum((a_i - e_i) *
     ln(a_i / e_i))`` over bins built from the reference sample, which is the
     J-divergence of Jeffreys (1946); see ``references/standards.md``. The v1
     helper reported ``0.5 * z**2`` from the standardised *mean* gap under the
     name ``psi_score``. That statistic is not PSI: it is one-directional KL
     for two equal-variance Gaussians, i.e. exactly **half** the Gaussian
     closed form of PSI, so every value was compared against the 0.10/0.25
     credit-scoring bands at the wrong scale. Worse, being a function of the
     mean gap alone it is identically 0.0 for any pure variance or shape
     change: a feature whose spread tripled with its mean unmoved scored
     ``psi = 0.0, is_drifting = False``.
  2. **Outer bins are unbounded.** Bin edges come from reference quantiles with
     -inf/+inf outer edges, so current observations that have left the
     historical support are counted rather than discarded - they are the
     observations that matter most.
  3. **Degenerate reference samples are detected, not silently collapsed.**
     Quantile edges de-duplicate: a 95/5 regime flag over 10 bins collapses to
     a single bin spanning everything, and PSI is then identically 0.0 whatever
     the live sample does. Halt flags and mostly-zero event counts are ordinary
     trading features, so the binner falls back to edges between the distinct
     reference values.
  4. **An empty window is not a healthy window.** ``get_rolling_accuracy()``
     returned ``1.0`` when no predictions had been recorded, so a freshly
     constructed or freshly restarted monitor reported ``HEALTHY`` with a 1.0x
     sizing multiplier on zero evidence, and one correct prediction kept it
     there. Insufficient evidence is its own status and, by default, sizes to
     0.0. ``restore_history()`` exists so a process restart can reload the
     window from the durable prediction log instead of starting blind.
  5. **A single-window accuracy breach is not evidence.** Realised accuracy on
     a 60-observation window carries roughly +/-12pp of sampling noise. A
     genuinely 55%-accurate model breaches a 52% threshold on **34.7%** of
     independent 60-observation windows by chance alone (exact binomial); the
     v1 logic halted the model and zeroed its size on the first such window.
     A halt requires the breach to persist for ``consecutive_breaches_to_halt``
     evaluations, and the report carries a Wilson score lower confidence bound
     so the operator can see how much of the point estimate is noise.
  6. **Unmeasurable is not healthy.** Non-finite live values, an empty live
     batch, and a feature with no registered baseline each returned
     ``psi = 0.0, is_drifting = False`` - "no drift observed" for a statistic
     never computed, on a feed that had stopped or was feeding the model NaN.
     Each is now a distinct ``FeatureDriftStatus``; non-finite input halts,
     missing baseline degrades and alerts.
  7. **State transitions are edge-triggered and a halt latches.** v1 re-fired
     the alert on every single evaluation while halted (an alert storm gets
     muted, which is how the next incident is missed) and let sizing flap
     1.0 -> 0.0 -> 1.0 as the estimate crossed back and forth. Alerts fire on
     transitions; recovery from DEGRADED requires ``recovery_evaluations``
     consecutive healthy evaluations; a halt stays latched until
     ``clear_halt()`` is called by a human, matching the retrain-then-shadow
     -validate workflow in ``SKILL.md``.

What this is not:

  - **Not a kill switch.** It returns a status and a multiplier. Cancelling
    working orders and flattening positions belong to
    ``kill-switch-and-drawdown-circuit-breakers``.
  - **Not a root-cause diagnosis.** It does not separate covariate shift from
    concept drift from a stalled feature pipeline; that is
    ``concept-drift-vs-staleness-differentiation``.
  - **Not a calibrated statistical test.** The 0.10/0.25 PSI bands are a credit
    -scoring rule of thumb with no controlled error rate, and their power
    decreases as the window grows. Treat them as operator-chosen defaults.
  - **Not a profitability monitor.** Directional accuracy can hold while P&L
    inverts, because accuracy is blind to the magnitude of the moves it gets
    right. Pair it with realised P&L attribution.
"""
import logging
import math
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist
from typing import Callable, Deque, Dict, Hashable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Proportion substituted for an empty bin so ln(a/e) stays finite. This is the
# conventional PSI treatment, but it means the *magnitude* of PSI for two
# distributions with disjoint support is an artefact of this constant, not a
# distance. Read a very large PSI as "disjoint", never as "n times worse".
_ZERO_PROPORTION_FLOOR = 1e-4

# Labels accepted by record_prediction (bool is an int subclass). float is
# deliberately excluded: int() coercion silently mapped 1.2 and 1.7 onto the
# same class.
_ALLOWED_LABEL_TYPES = (int, str)


class ModelHealthStatus(str, Enum):
    """
    Operational health of the live model. Subclasses ``str`` so callers
    comparing against the string literals (``report.status == "HEALTHY"``)
    keep working.
    """

    HEALTHY = "HEALTHY"
    DEGRADED_WARNING = "DEGRADED_WARNING"
    HALTED_STALE = "HALTED_STALE"
    #: Not enough realised outcomes in the window to judge the model at all.
    #: Distinct from HEALTHY: absence of evidence is not evidence of health.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FeatureDriftStatus(str, Enum):
    """Why a per-feature drift number is, or is not, trustworthy."""

    OK = "OK"
    #: No baseline registered for this feature name (typically a typo, or a
    #: feature added to the live model and never added to the baseline).
    NO_BASELINE = "NO_BASELINE"
    #: The live batch was empty - the feature stopped being produced.
    NO_LIVE_DATA = "NO_LIVE_DATA"
    #: The live batch contained NaN or +/-Inf. The model is being scored on it.
    NON_FINITE = "NON_FINITE"
    #: Fewer live observations than ``min_live_values``; PSI is not computed.
    INSUFFICIENT_LIVE_DATA = "INSUFFICIENT_LIVE_DATA"
    #: A baseline is registered for this feature but the live batch did not
    #: contain it. Silently monitoring 3 of 5 registered features looks
    #: identical to monitoring all 5 and finding nothing.
    MISSING_FROM_BATCH = "MISSING_FROM_BATCH"
    #: Baseline is a single constant value (or std == 0). Reported separately
    #: because "the training feature never varied" is a modelling problem.
    DEGENERATE_BASELINE = "DEGENERATE_BASELINE"


class DriftMethod(str, Enum):
    """Which statistic produced ``psi_score``."""

    #: True binned PSI against the reference sample. Sensitive to any change in
    #: the distribution: location, scale or shape.
    PSI_BINNED = "PSI_BINNED"
    #: Gaussian closed form, PSI = z**2, used when only mean/std were supplied
    #: as the baseline. Sensitive to a *location* shift only - it cannot see a
    #: variance or shape change. Register a reference sample to get PSI_BINNED.
    GAUSSIAN_JEFFREYS = "GAUSSIAN_JEFFREYS"
    #: Nothing was computed; read ``status`` for why.
    NOT_COMPUTED = "NOT_COMPUTED"


@dataclass
class FeatureDriftResult:
    """
    Per-feature drift measurement.

    ``psi_score`` and ``z_score_distance`` are ``Optional`` on purpose: when a
    measurement could not be taken they are ``None``, never ``0.0``. A
    dashboard plots 0.0 as "no drift observed", which is the opposite of what
    a dead feed means.
    """

    feature_name: str
    z_score_distance: Optional[float]
    psi_score: Optional[float]
    is_drifting: bool
    status: FeatureDriftStatus = FeatureDriftStatus.OK
    method: DriftMethod = DriftMethod.PSI_BINNED
    live_sample_size: int = 0

    @property
    def is_measurable(self) -> bool:
        """True when a drift statistic was actually computed."""
        return self.status is FeatureDriftStatus.OK


@dataclass
class ModelStalenessReport:
    """
    Health snapshot. ``sizing_multiplier`` is the number the caller multiplies
    its intended position size by; it is advice, not an executed action.
    """

    status: ModelHealthStatus
    rolling_accuracy: Optional[float]
    min_accuracy_threshold: float
    drifted_features_count: int
    sizing_multiplier: float
    action_required: str
    #: One-sided Wilson score lower confidence bound on the true accuracy at
    #: ``confidence_level``. The honest version of ``rolling_accuracy``.
    accuracy_lower_bound: Optional[float] = None
    #: Realised outcomes currently in the window.
    sample_size: int = 0
    #: Consecutive evaluations on which the point estimate was below threshold.
    consecutive_accuracy_breaches: int = 0
    #: Worst per-feature PSI in this batch, and the feature that produced it.
    #: The maximum decides; the mean would dilute one broken feature in a
    #: hundred below any threshold.
    max_psi: Optional[float] = None
    max_psi_feature: Optional[str] = None
    #: Features whose drift could not be measured, as (name, status) pairs.
    unevaluable_features: Tuple[Tuple[str, FeatureDriftStatus], ...] = ()
    #: True while a previous halt is still latched pending ``clear_halt()``.
    halt_latched: bool = False
    #: Realised precision on ``positive_label``, when it was predicted at all.
    rolling_precision: Optional[float] = None


def _is_finite_number(value: object) -> bool:
    """True for a real number that is neither NaN nor +/-Inf. Accepts anything
    that converts to float, so NumPy scalars, ``Decimal`` and boolean
    indicator features all pass; ``None``, strings and NaN do not."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _validate_label_pair(predicted: Hashable, actual: Hashable) -> None:
    """Reject anything that would make an outcome ambiguous. Floats in
    particular: v1 applied ``int()``, which mapped a regressor's 1.2 and 1.7
    onto the same class and made the string ``"1"`` equal to the integer 1."""
    for role, label in (("predicted", predicted), ("actual", actual)):
        if isinstance(label, float):
            raise ValueError(
                f"{role} label must be a discrete class, not a float ({label!r}); "
                "bucket continuous output into labels before recording"
            )
        if not isinstance(label, _ALLOWED_LABEL_TYPES):
            raise ValueError(
                f"{role} label must be one of {_ALLOWED_LABEL_TYPES}, "
                f"got {type(label).__name__}"
            )


def _quantile_edges(reference: Sequence[float], bins: int) -> List[float]:
    """
    Interior bin edges from reference quantiles, de-duplicated.

    Returns interior edges only; the caller treats the outer edges as
    -inf/+inf so that live values outside the historical support are counted
    rather than dropped.

    When the quantile edges collapse (a sparse indicator: a 95/5 flag over 10
    bins de-duplicates to a single edge, giving one bin that spans everything
    and PSI identically 0.0), fall back to midpoints between the distinct
    reference values so the flag's two states remain separable.
    """
    ordered = sorted(reference)
    n = len(ordered)
    edges: List[float] = []
    for i in range(1, bins):
        # The edge is the first value of the i-th bin, and ``bisect_right``
        # puts a value equal to an edge into the bin above it. With distinct
        # reference values this gives exactly equal bin counts, so the
        # reference proportions are 1/bins by construction - no interpolation
        # convention to argue about.
        idx = min(n - 1, max(0, i * n // bins))
        edge = ordered[idx]
        if not edges or edge > edges[-1]:
            edges.append(edge)

    distinct = sorted(set(ordered))
    if len(edges) < 2 and len(distinct) > 1:
        # Collapsed. Use midpoints between distinct values, capped at `bins`.
        step = max(1, len(distinct) // bins)
        edges = [
            (distinct[i - 1] + distinct[i]) / 2.0
            for i in range(step, len(distinct), step)
        ]
        edges = sorted(set(edges))
    return edges


def _bin_proportions(values: Sequence[float], edges: Sequence[float]) -> List[float]:
    """Fraction of ``values`` in each bin defined by ``edges`` with unbounded
    outer edges. Proportions sum to 1.0 by construction: nothing is discarded."""
    counts = [0] * (len(edges) + 1)
    for v in values:
        counts[bisect_right(edges, v)] += 1
    total = len(values)
    return [c / total for c in counts]


def population_stability_index(
    reference: Sequence[float], current: Sequence[float], bins: int = 10
) -> float:
    """
    Population Stability Index of ``current`` against ``reference``.

        PSI = sum_i (a_i - e_i) * ln(a_i / e_i)

    over bins, where ``e_i`` is the reference proportion and ``a_i`` the
    current proportion. This is the J-divergence of Jeffreys (1946); see
    ``references/standards.md`` for the conventional 0.10/0.25 bands and their
    limitations.

    Raises ``ValueError`` on an empty input, any non-finite value, or a
    constant reference sample (against which every bin scheme collapses, so
    PSI would be identically 0.0 whatever the current sample does). It does
    not impute or drop: silently repairing the caller's data is how a broken
    feed keeps looking healthy.
    """
    if not reference or not current:
        raise ValueError("population_stability_index requires non-empty samples")
    if not all(_is_finite_number(v) for v in reference):
        raise ValueError("reference sample contains non-finite values")
    if not all(_is_finite_number(v) for v in current):
        raise ValueError("current sample contains non-finite values")
    if bins < 2:
        raise ValueError(f"bins must be >= 2, got {bins}")

    ref = [float(v) for v in reference]
    cur = [float(v) for v in current]
    if len(set(ref)) < 2:
        raise ValueError(
            "reference sample is constant; PSI against it is undefined "
            "(every bin scheme collapses to a single bin)"
        )

    edges = _quantile_edges(ref, bins)

    expected = _bin_proportions(ref, edges)
    actual = _bin_proportions(cur, edges)
    psi = 0.0
    for e, a in zip(expected, actual):
        e = max(e, _ZERO_PROPORTION_FLOOR)
        a = max(a, _ZERO_PROPORTION_FLOOR)
        psi += (a - e) * math.log(a / e)
    return psi


def wilson_lower_bound(
    successes: int, trials: int, confidence_level: float = 0.95
) -> float:
    """
    One-sided Wilson (1927) score lower confidence bound on a binomial
    proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and
    keeps sensible coverage at the small window sizes and extreme proportions a
    trading monitor actually runs on (``successes = trials`` does not give an
    interval of zero width).
    """
    if trials <= 0:
        raise ValueError("trials must be > 0")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be within [0, trials]")
    z = NormalDist().inv_cdf(confidence_level)
    p_hat = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p_hat + z * z / (2 * trials)) / denominator
    half_width = (z / denominator) * math.sqrt(
        p_hat * (1.0 - p_hat) / trials + z * z / (4 * trials * trials)
    )
    return max(0.0, centre - half_width)


class ModelStalenessMonitor:
    """
    Rolling realised-performance and feature-drift monitor for a live model.

    Typical wiring::

        monitor = ModelStalenessMonitor(window=250, min_accuracy_threshold=0.52)
        monitor.set_training_baseline({
            "realised_vol": {"reference_sample": train_df["realised_vol"].tolist()},
        })
        monitor.restore_history(prediction_log.recent_realised_outcomes(250))
        ...
        monitor.record_prediction(predicted=signal, actual=realised_direction)
        report = monitor.evaluate_health({"realised_vol": live_window})
        size = intended_size * report.sizing_multiplier

    The window counts *realised outcomes*, not calendar days. A model scoring
    500 instruments a day fills a 60-entry window in minutes; size ``window``
    to the horizon you intend to monitor over.

    Not thread-safe. Serialise calls, or give each strategy its own instance.
    """

    def __init__(
        self,
        window: int = 60,
        min_accuracy_threshold: float = 0.52,
        psi_warning_threshold: float = 0.10,
        psi_halt_threshold: float = 0.25,
        alert_fn: Optional[Callable[[str], None]] = None,
        min_predictions: int = 30,
        warning_accuracy_margin: float = 0.05,
        consecutive_breaches_to_halt: int = 3,
        recovery_evaluations: int = 3,
        latch_halt: bool = True,
        warmup_sizing_multiplier: float = 0.0,
        psi_bins: int = 10,
        min_live_values: int = 20,
        confidence_level: float = 0.95,
        positive_label: Hashable = 1,
    ) -> None:
        """
        :param window: realised outcomes retained for the rolling metrics.
        :param min_accuracy_threshold: point-estimate accuracy below which the
            window counts as a breach.
        :param psi_warning_threshold: per-feature PSI at or above which a
            feature counts as drifting (credit-scoring rule of thumb: 0.10).
        :param psi_halt_threshold: per-feature PSI at or above which a *single*
            feature is enough to halt (rule of thumb: 0.25).
        :param alert_fn: called with a message on every status transition.
            Exceptions raised by it are logged and swallowed - a broken pager
            must not take down the risk gate.
        :param min_predictions: realised outcomes required before an accuracy
            verdict is given at all. Below this the status is
            ``INSUFFICIENT_DATA``.
        :param warning_accuracy_margin: width of the warning band above
            ``min_accuracy_threshold``.
        :param consecutive_breaches_to_halt: consecutive breaching evaluations
            required before halting. 1 reproduces the v1 trigger-happy
            behaviour and is not recommended - see the module docstring.
        :param recovery_evaluations: consecutive healthy evaluations required
            to leave ``DEGRADED_WARNING``. Suppresses sizing flap.
        :param latch_halt: keep ``HALTED_STALE`` until ``clear_halt()`` is
            called. A model that halted should be retrained and shadow-
            validated, not waved back in because the next window looked better.
        :param warmup_sizing_multiplier: multiplier while
            ``INSUFFICIENT_DATA``. Defaults to 0.0 (fail closed).
        :param psi_bins: bins used for the binned PSI.
        :param min_live_values: live observations required before PSI is
            computed for a feature.
        :param confidence_level: for the Wilson lower bound.
        :param positive_label: label ``rolling_precision`` is computed on.
        """
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        if not 0.0 < min_accuracy_threshold < 1.0:
            raise ValueError(
                f"min_accuracy_threshold must be in (0, 1), got {min_accuracy_threshold}"
            )
        if psi_warning_threshold <= 0 or psi_halt_threshold <= 0:
            raise ValueError("PSI thresholds must be > 0")
        if psi_halt_threshold < psi_warning_threshold:
            raise ValueError(
                f"psi_halt_threshold ({psi_halt_threshold}) must be >= "
                f"psi_warning_threshold ({psi_warning_threshold})"
            )
        if min_predictions < 1:
            raise ValueError(f"min_predictions must be >= 1, got {min_predictions}")
        if min_predictions > window:
            raise ValueError(
                f"min_predictions ({min_predictions}) exceeds window ({window}): "
                "the monitor could never leave INSUFFICIENT_DATA"
            )
        if warning_accuracy_margin < 0:
            raise ValueError("warning_accuracy_margin must be >= 0")
        if consecutive_breaches_to_halt < 1:
            raise ValueError("consecutive_breaches_to_halt must be >= 1")
        if recovery_evaluations < 1:
            raise ValueError("recovery_evaluations must be >= 1")
        if not 0.0 <= warmup_sizing_multiplier <= 1.0:
            raise ValueError("warmup_sizing_multiplier must be in [0, 1]")
        if psi_bins < 2:
            raise ValueError(f"psi_bins must be >= 2, got {psi_bins}")
        if min_live_values < 1:
            raise ValueError("min_live_values must be >= 1")
        if not 0.5 <= confidence_level < 1.0:
            raise ValueError("confidence_level must be in [0.5, 1)")

        self.window = window
        self.min_accuracy_threshold = min_accuracy_threshold
        self.psi_warning_threshold = psi_warning_threshold
        self.psi_halt_threshold = psi_halt_threshold
        self.alert_fn: Callable[[str], None] = alert_fn or (
            lambda msg: logger.warning(msg)
        )
        self.min_predictions = min_predictions
        self.warning_accuracy_margin = warning_accuracy_margin
        self.consecutive_breaches_to_halt = consecutive_breaches_to_halt
        self.recovery_evaluations = recovery_evaluations
        self.latch_halt = latch_halt
        self.warmup_sizing_multiplier = warmup_sizing_multiplier
        self.psi_bins = psi_bins
        self.min_live_values = min_live_values
        self.confidence_level = confidence_level
        self.positive_label = positive_label

        #: (predicted, actual) pairs, most recent last. Raw pairs rather than
        #: match flags so precision and class balance stay recoverable.
        self.predictions: Deque[Tuple[Hashable, Hashable]] = deque(maxlen=window)
        self.train_feature_stats: Dict[str, Dict[str, object]] = {}

        self._consecutive_breaches = 0
        self._consecutive_healthy = 0
        self._halt_latched = False
        self._last_status: Optional[ModelHealthStatus] = None

    # ------------------------------------------------------------------
    # Baseline
    # ------------------------------------------------------------------
    def set_training_baseline(self, train_stats: Dict[str, Dict[str, object]]) -> None:
        """
        Register the training-time distribution of each monitored feature.

        Two baseline forms are accepted per feature:

        - ``{"reference_sample": [...]}`` - the training values themselves.
          Gives true binned PSI, which sees location, scale and shape changes.
          **Prefer this.**
        - ``{"mean": m, "std": s}`` - summary statistics only. Falls back to
          the Gaussian closed form ``PSI = z**2``, which is blind to any change
          that leaves the mean where it was. ``FeatureDriftResult.method``
          records which was used, so a dashboard can show the difference.

        Both may be supplied; the reference sample wins. Replaces any previous
        baseline. Raises ``ValueError`` on a malformed entry rather than
        accepting a baseline that would silently produce meaningless numbers.
        """
        if not isinstance(train_stats, dict):
            raise ValueError("train_stats must be a dict of {feature: baseline}")

        validated: Dict[str, Dict[str, object]] = {}
        for name, spec in train_stats.items():
            if not isinstance(spec, dict):
                raise ValueError(f"baseline for {name!r} must be a dict")
            entry: Dict[str, object] = {}

            sample = spec.get("reference_sample")
            if sample is not None:
                raw = list(sample)
                values = [float(v) for v in raw if _is_finite_number(v)]
                dropped = len(raw) - len(values)
                if dropped:
                    raise ValueError(
                        f"reference_sample for {name!r} contains {dropped} non-finite "
                        "value(s); clean the training data rather than monitoring against it"
                    )
                if len(values) < self.min_live_values:
                    raise ValueError(
                        f"reference_sample for {name!r} has {len(values)} observations, "
                        f"fewer than min_live_values={self.min_live_values}"
                    )
                # mean/std are derived from the sample, so the two views of the
                # same baseline cannot disagree.
                entry["reference_sample"] = values
                entry["mean"] = sum(values) / len(values)
                entry["std"] = _sample_std(values)
            elif "mean" in spec and "std" in spec:
                mean, std = spec["mean"], spec["std"]
                if not _is_finite_number(mean) or not _is_finite_number(std):
                    raise ValueError(f"mean/std for {name!r} must be finite numbers")
                if float(std) < 0:
                    raise ValueError(f"std for {name!r} must be >= 0")
                entry["mean"] = float(mean)
                entry["std"] = float(std)

            if not entry:
                raise ValueError(
                    f"baseline for {name!r} must supply 'reference_sample' or "
                    "both 'mean' and 'std'"
                )
            validated[str(name)] = entry

        self.train_feature_stats = validated
        logger.info(
            "Training baseline registered for %d feature(s): %s",
            len(validated),
            ", ".join(sorted(validated)),
        )

    # ------------------------------------------------------------------
    # Realised outcomes
    # ------------------------------------------------------------------
    def record_prediction(self, predicted: Hashable, actual: Hashable) -> None:
        """
        Record one realised prediction outcome.

        Labels are compared with ``==`` and are **not** coerced. v1 applied
        ``int()``, which mapped a regressor's 1.2 and 1.7 onto the same class
        and made the string ``"1"`` equal to the integer ``1``. Floats are
        rejected outright: bucket them into labels yourself, where the
        thresholds are visible.

        Only record an outcome once it has actually realised. Scoring a
        prediction against a partially formed bar is look-ahead bias in the
        monitor itself - it will report health the strategy never had.
        """
        _validate_label_pair(predicted, actual)
        self.predictions.append((predicted, actual))

    def restore_history(
        self, outcomes: Sequence[Tuple[Hashable, Hashable]]
    ) -> None:
        """
        Reload the rolling window from the durable prediction log, oldest
        first, after a process restart.

        The window lives in memory. Without this, a restart leaves the monitor
        with no evidence and - by default - sizing at
        ``warmup_sizing_multiplier`` until it refills, which for a daily-label
        model is weeks. Restoring keeps the health gate continuous across
        deploys. Only the most recent ``window`` entries are retained.

        Atomic: a malformed entry raises and leaves the existing window intact
        rather than half-replaced. Pass an empty sequence to reset the window,
        which is what a *replacement* model needs - the outcomes already in it
        belong to the model that was retired.
        """
        staged: Deque[Tuple[Hashable, Hashable]] = deque(maxlen=self.window)
        for predicted, actual in outcomes:
            _validate_label_pair(predicted, actual)
            staged.append((predicted, actual))
        self.predictions = staged
        logger.info(
            "Restored %d realised outcome(s) into the rolling window", len(staged)
        )

    def export_history(self) -> List[Tuple[Hashable, Hashable]]:
        """The current window as (predicted, actual) pairs, oldest first."""
        return list(self.predictions)

    def get_rolling_accuracy(self) -> Optional[float]:
        """
        Realised accuracy over the window, or ``None`` when the window is
        empty.

        **Behaviour change from v1**, which returned ``1.0`` for an empty
        window - a fresh or freshly restarted monitor therefore reported a
        perfect model. ``None`` matches ``RollingAccuracy.accuracy()`` below
        and forces the caller to handle "not yet known".
        """
        if not self.predictions:
            return None
        matches = sum(1 for predicted, actual in self.predictions if predicted == actual)
        return matches / len(self.predictions)

    def get_rolling_precision(
        self, positive_label: Optional[Hashable] = None
    ) -> Optional[float]:
        """
        Precision on ``positive_label``: of the times the model called it, how
        often it was right. ``None`` when the label was never predicted in the
        window.

        Accuracy alone is misleading under class imbalance - a model that
        predicts the majority direction every time scores the base rate. When
        the strategy only acts on one side, precision on that side is the
        metric that matters.
        """
        label = self.positive_label if positive_label is None else positive_label
        predicted_positive = [
            (p, a) for p, a in self.predictions if p == label
        ]
        if not predicted_positive:
            return None
        return sum(1 for p, a in predicted_positive if p == a) / len(predicted_positive)

    def accuracy_lower_bound(self) -> Optional[float]:
        """
        One-sided Wilson lower confidence bound on the true accuracy, or
        ``None`` when the window is empty.

        Worth reading next to ``get_rolling_accuracy()``: 33 correct out of 60
        is a point estimate of 0.550 and a 95% lower bound of 0.444 - a window
        that looks comfortably above a 0.52 threshold is statistically
        consistent with a coin flip.
        """
        if not self.predictions:
            return None
        matches = sum(1 for predicted, actual in self.predictions if predicted == actual)
        return wilson_lower_bound(matches, len(self.predictions), self.confidence_level)

    # ------------------------------------------------------------------
    # Feature drift
    # ------------------------------------------------------------------
    def compute_feature_drift(
        self, feature_name: str, live_values: Sequence[float]
    ) -> FeatureDriftResult:
        """
        Drift of a live feature batch against its registered training baseline.

        Never raises on caller data and never guesses a baseline: an
        unmeasurable feature comes back with ``psi_score = None`` and a
        ``FeatureDriftStatus`` saying why. v1 fell back to an implicit
        ``mean=0.0, std=1.0`` baseline for an unregistered name, so a typo'd
        feature produced a confident number against a distribution nothing had
        been trained on.
        """
        stats = self.train_feature_stats.get(feature_name)
        if stats is None:
            logger.warning(
                "No training baseline registered for feature %r; drift is unmeasurable",
                feature_name,
            )
            return FeatureDriftResult(
                feature_name, None, None, False,
                FeatureDriftStatus.NO_BASELINE, DriftMethod.NOT_COMPUTED, 0,
            )

        values = list(live_values)
        if not values:
            return FeatureDriftResult(
                feature_name, None, None, False,
                FeatureDriftStatus.NO_LIVE_DATA, DriftMethod.NOT_COMPUTED, 0,
            )
        if not all(_is_finite_number(v) for v in values):
            logger.error(
                "Feature %r contains non-finite live values; the model is being "
                "scored on them", feature_name,
            )
            return FeatureDriftResult(
                feature_name, None, None, False,
                FeatureDriftStatus.NON_FINITE, DriftMethod.NOT_COMPUTED, len(values),
            )

        numeric = [float(v) for v in values]
        if len(numeric) < self.min_live_values:
            return FeatureDriftResult(
                feature_name, None, None, False,
                FeatureDriftStatus.INSUFFICIENT_LIVE_DATA,
                DriftMethod.NOT_COMPUTED, len(numeric),
            )

        train_mean = float(stats["mean"])
        train_std = float(stats["std"])
        live_mean = sum(numeric) / len(numeric)
        z_distance = (
            abs(live_mean - train_mean) / train_std if train_std > 0 else None
        )

        if train_std <= 0:
            # The training feature was constant: the standardised distance
            # divides by zero and every bin scheme collapses, so live variation
            # is unquantifiable against it. Checked *before* choosing a method,
            # because a constant reference sample would otherwise score PSI 0.0
            # - "no drift observed" - however far the live values had moved.
            logger.warning(
                "Feature %r has a degenerate (constant) training baseline", feature_name
            )
            return FeatureDriftResult(
                feature_name, None, None, False,
                FeatureDriftStatus.DEGENERATE_BASELINE,
                DriftMethod.NOT_COMPUTED, len(numeric),
            )

        reference = stats.get("reference_sample")
        if reference:
            psi = population_stability_index(reference, numeric, self.psi_bins)
            method = DriftMethod.PSI_BINNED
        else:
            # Gaussian closed form of the J-divergence for two equal-variance
            # normals separated by z standard deviations: PSI = z**2. (v1 used
            # 0.5 * z**2, which is the one-directional KL, i.e. half of it.)
            psi = z_distance * z_distance  # type: ignore[operator]
            method = DriftMethod.GAUSSIAN_JEFFREYS
        status = FeatureDriftStatus.OK

        return FeatureDriftResult(
            feature_name=feature_name,
            z_score_distance=None if z_distance is None else round(z_distance, 4),
            psi_score=round(psi, 6),
            is_drifting=psi >= self.psi_warning_threshold,
            status=status,
            method=method,
            live_sample_size=len(numeric),
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def clear_halt(self, operator: str, reason: str) -> None:
        """
        Release a latched halt. Intended to be called by a human after the
        model has been retrained and shadow-validated, per ``SKILL.md`` step 6.

        Deliberately requires an operator and a reason: an unattributed reset
        of a risk gate is not auditable, and a latch a scheduler can clear on
        its own is not a latch.

        Clearing the halt does **not** clear the rolling window. If the halt is
        being cleared because a *replacement* model was promoted, call
        ``restore_history([])`` as well - the outcomes still in the window
        belong to the model that was retired, and judging the new one on them
        will either halt it immediately or certify it on evidence that is not
        about it.
        """
        if not operator or not reason:
            raise ValueError("clear_halt requires a non-empty operator and reason")
        if not self._halt_latched:
            logger.info("clear_halt called by %s but no halt was latched", operator)
            return
        self._halt_latched = False
        self._consecutive_breaches = 0
        self._consecutive_healthy = 0
        self._last_status = None
        logger.warning("Latched model halt cleared by %s: %s", operator, reason)

    def evaluate_health(
        self, live_features_batch: Optional[Dict[str, Sequence[float]]] = None
    ) -> ModelStalenessReport:
        """
        Fold the rolling metrics and this batch's feature drift into one status
        and a sizing multiplier.

        Precedence, highest first:

        1. A latched halt (until ``clear_halt()``).
        2. Any feature carrying non-finite live values -> halt. The model's
           inputs are broken; its output is not a signal.
        3. Any single feature at or above ``psi_halt_threshold`` -> halt. The
           trigger is the per-feature *maximum*: one broken feature in a
           hundred is a broken pipeline, and the mean would hide it.
        4. Accuracy below ``min_accuracy_threshold`` for
           ``consecutive_breaches_to_halt`` consecutive evaluations -> halt.
        5. Too few realised outcomes -> ``INSUFFICIENT_DATA``.
        6. Warning band, any drifting feature, or a feature that could not be
           measured -> ``DEGRADED_WARNING``.

        Passing ``None`` monitors accuracy only. Passing a dict - even an empty
        one - asserts that the registered features are being monitored, so any
        registered feature missing from it is reported as
        ``MISSING_FROM_BATCH`` rather than silently skipped.

        Calling this more often than the window turns over does not make
        detection faster; it only inflates the consecutive-breach counter on
        what is substantially the same data. Evaluate on a cadence matched to
        the label horizon.
        """
        accuracy = self.get_rolling_accuracy()
        sample_size = len(self.predictions)
        lower_bound = self.accuracy_lower_bound()

        # ``None`` means accuracy-only monitoring. A supplied batch - including
        # an empty one - is a claim to be monitoring the registered features,
        # so any that are absent are reported rather than quietly skipped.
        drift_results: List[FeatureDriftResult] = []
        if live_features_batch is not None:
            for name, values in live_features_batch.items():
                drift_results.append(self.compute_feature_drift(name, values))
            for name in sorted(set(self.train_feature_stats) - set(live_features_batch)):
                logger.warning(
                    "Feature %r has a registered baseline but was absent from the "
                    "live batch", name,
                )
                drift_results.append(
                    FeatureDriftResult(
                        name, None, None, False,
                        FeatureDriftStatus.MISSING_FROM_BATCH,
                        DriftMethod.NOT_COMPUTED, 0,
                    )
                )
        measurable = [r for r in drift_results if r.is_measurable]
        unevaluable = tuple(
            (r.feature_name, r.status) for r in drift_results if not r.is_measurable
        )
        non_finite = [
            r for r in drift_results if r.status is FeatureDriftStatus.NON_FINITE
        ]
        drifted_count = sum(1 for r in measurable if r.is_drifting)

        worst = max(
            measurable, key=lambda r: r.psi_score or 0.0, default=None
        )
        max_psi = worst.psi_score if worst else None
        max_psi_feature = worst.feature_name if worst and worst.is_drifting else None

        # Accuracy breach streak. Only counted once there is enough evidence to
        # judge, so a warm-up window cannot accumulate towards a halt.
        has_evidence = sample_size >= self.min_predictions and accuracy is not None
        if has_evidence and accuracy < self.min_accuracy_threshold:
            self._consecutive_breaches += 1
        elif has_evidence:
            self._consecutive_breaches = 0

        status, sizing, action = self._classify(
            accuracy=accuracy,
            has_evidence=has_evidence,
            max_psi=max_psi,
            drifted_count=drifted_count,
            non_finite=[r.feature_name for r in non_finite],
            unevaluable=unevaluable,
            max_psi_feature=max_psi_feature,
        )

        if status is ModelHealthStatus.HALTED_STALE and self.latch_halt:
            self._halt_latched = True

        if status is not self._last_status:
            self._emit_alert(status, action)
            self._last_status = status

        return ModelStalenessReport(
            status=status,
            rolling_accuracy=None if accuracy is None else round(accuracy, 4),
            min_accuracy_threshold=self.min_accuracy_threshold,
            drifted_features_count=drifted_count,
            sizing_multiplier=sizing,
            action_required=action,
            accuracy_lower_bound=None if lower_bound is None else round(lower_bound, 4),
            sample_size=sample_size,
            consecutive_accuracy_breaches=self._consecutive_breaches,
            max_psi=max_psi,
            max_psi_feature=max_psi_feature,
            unevaluable_features=unevaluable,
            halt_latched=self._halt_latched,
            rolling_precision=self.get_rolling_precision(),
        )

    def _classify(
        self,
        accuracy: Optional[float],
        has_evidence: bool,
        max_psi: Optional[float],
        drifted_count: int,
        non_finite: List[str],
        unevaluable: Tuple[Tuple[str, FeatureDriftStatus], ...],
        max_psi_feature: Optional[str],
    ) -> Tuple[ModelHealthStatus, float, str]:
        """Status precedence. Split out so the ordering is testable in one place."""
        if self._halt_latched:
            return (
                ModelHealthStatus.HALTED_STALE,
                0.0,
                "HALT LATCHED: a previous halt has not been cleared. Retrain and "
                "shadow-validate, then call clear_halt(operator, reason).",
            )

        if non_finite:
            return (
                ModelHealthStatus.HALTED_STALE,
                0.0,
                "HALT MODEL: non-finite live values in feature(s) "
                f"{', '.join(sorted(non_finite))} - the model is being scored on "
                "NaN/Inf. Fix the feature pipeline before resuming.",
            )

        if max_psi is not None and max_psi >= self.psi_halt_threshold:
            return (
                ModelHealthStatus.HALTED_STALE,
                0.0,
                f"HALT MODEL: feature {max_psi_feature!r} PSI {max_psi:.4f} at or "
                f"above halt threshold {self.psi_halt_threshold}. The live input "
                "distribution has left the one the model was fitted on.",
            )

        if self._consecutive_breaches >= self.consecutive_breaches_to_halt:
            return (
                ModelHealthStatus.HALTED_STALE,
                0.0,
                f"HALT MODEL: rolling accuracy below {self.min_accuracy_threshold} "
                f"on {self._consecutive_breaches} consecutive evaluations.",
            )

        if not has_evidence:
            self._consecutive_healthy = 0
            return (
                ModelHealthStatus.INSUFFICIENT_DATA,
                self.warmup_sizing_multiplier,
                f"INSUFFICIENT DATA: {len(self.predictions)} of {self.min_predictions} "
                "realised outcomes required before the model can be judged. Sizing "
                f"held at {self.warmup_sizing_multiplier}x; restore_history() can "
                "reload the window from the prediction log.",
            )

        assert accuracy is not None  # implied by has_evidence
        degraded_reasons: List[str] = []
        if accuracy < self.min_accuracy_threshold:
            degraded_reasons.append(
                f"accuracy {accuracy:.4f} below {self.min_accuracy_threshold} "
                f"({self._consecutive_breaches} of {self.consecutive_breaches_to_halt} "
                "consecutive breaches)"
            )
        elif accuracy < self.min_accuracy_threshold + self.warning_accuracy_margin:
            degraded_reasons.append(
                f"accuracy {accuracy:.4f} inside the warning band "
                f"[{self.min_accuracy_threshold}, "
                f"{self.min_accuracy_threshold + self.warning_accuracy_margin})"
            )
        if drifted_count:
            degraded_reasons.append(
                f"{drifted_count} feature(s) at or above PSI "
                f"{self.psi_warning_threshold}"
            )
        if unevaluable:
            degraded_reasons.append(
                "unmeasurable feature(s): "
                + ", ".join(f"{n} ({s.value})" for n, s in unevaluable)
            )

        if degraded_reasons:
            self._consecutive_healthy = 0
            return (
                ModelHealthStatus.DEGRADED_WARNING,
                0.5,
                "DEGRADED WARNING: " + "; ".join(degraded_reasons)
                + ". Sizing reduced to 50%.",
            )

        self._consecutive_healthy += 1
        if (
            self._last_status is ModelHealthStatus.DEGRADED_WARNING
            and self._consecutive_healthy < self.recovery_evaluations
        ):
            return (
                ModelHealthStatus.DEGRADED_WARNING,
                0.5,
                f"DEGRADED WARNING (recovering): {self._consecutive_healthy} of "
                f"{self.recovery_evaluations} consecutive healthy evaluations "
                "required before restoring full size.",
            )
        return (
            ModelHealthStatus.HEALTHY,
            1.0,
            "Model performing within expected parameters.",
        )

    def _emit_alert(self, status: ModelHealthStatus, action: str) -> None:
        """Alerts fire on transitions only. A per-evaluation alert storm gets
        muted, and a muted channel is how the next incident is missed."""
        if status is ModelHealthStatus.HEALTHY and self._last_status is None:
            return
        message = f"MODEL STALENESS ALERT [{status.value}]: {action}"
        try:
            self.alert_fn(message)
        except Exception:  # noqa: BLE001 - a broken pager must not halt trading
            logger.exception("alert_fn raised while reporting status %s", status.value)


def _sample_std(values: Sequence[float]) -> float:
    """Sample standard deviation (n-1). 0.0 for fewer than two observations."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


# ----------------------------------------------------------------------
# Backward-compatibility helpers (kept for callers written against v1)
# ----------------------------------------------------------------------
class RollingAccuracy:
    """Minimal rolling hit-rate counter. Retained for v1 callers; new code
    should use ``ModelStalenessMonitor``, which adds the sample-size floor,
    the confidence bound and the drift signal."""

    def __init__(self, window: int = 60) -> None:
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        self.results: Deque[int] = deque(maxlen=window)

    def record(self, predicted: Hashable, actual: Hashable) -> None:
        self.results.append(1 if predicted == actual else 0)

    def accuracy(self) -> Optional[float]:
        """``None`` when nothing has been recorded - not 1.0, and not 0.0."""
        return sum(self.results) / len(self.results) if self.results else None


def feature_drift_score(
    live_mean: float, live_std: float, train_mean: float, train_std: float
) -> float:
    """
    Standardised distance between two distribution means, in training standard
    deviations. Retained for v1 callers.

    ``live_std`` is accepted and unused, as in v1. This is a *location*
    statistic: it is 0.0 whenever the means agree, however far apart the two
    distributions otherwise are. Use ``population_stability_index`` for a
    measure that sees scale and shape.
    """
    if train_std == 0:
        return float("inf") if live_mean != train_mean else 0.0
    return abs(live_mean - train_mean) / train_std
