"""
concept-drift-vs-staleness-differentiation:
Separates the three very different reasons a live ML trading signal starts
under-performing, so the remediation matches the actual root cause:

  - ``DATA_STALENESS``  : the feature pipeline is serving old values. The model
                          is fine. Retraining on stale data makes things worse.
  - ``COVARIATE_SHIFT`` : P(X) moved (a volatility regime change, a new tick
                          size, a corporate action). The learned relationship
                          P(Y|X) still holds; the model is simply being asked to
                          extrapolate. Refit on the new input distribution.
  - ``CONCEPT_DRIFT``   : P(Y|X) itself changed - the alpha decayed. No amount
                          of refitting on the same feature set recovers it.

The terminology follows Moreno-Torres et al. (2012), "A unifying view on dataset
shift in classification", Pattern Recognition 45(1): covariate shift is P(X)
changing with P(Y|X) fixed; concept shift is P(Y|X) changing.

The engine owns exactly one thing: **classifying an already-collected monitoring
snapshot into one operational regime and naming the remediation that regime
implies**. It does not fetch data, retrain, page anyone, or halt trading.

Six properties distinguish this from "compute PSI and compare it to 0.25", and
each exists because the naive version produces a *silent false negative* - a
monitor that reports STABLE while the strategy bleeds:

  1. **The outer PSI bins are unbounded.** ``numpy.histogram`` ignores values
     that fall outside the supplied edges. Building edges from reference
     percentiles and passing them straight to ``histogram`` therefore discards
     exactly the observations that matter most - the current values that have
     moved outside the historical support. Measured: a feature with 40% of its
     mass relocated far outside the reference range scored PSI 0.205 (below the
     0.25 rule of thumb, i.e. "no action") under bounded edges, versus 0.741
     with -inf/+inf outer edges. This module uses unbounded outer edges so the
     current sample's probability mass is conserved.
  2. **The trigger statistic is the per-feature maximum, not the mean.** One
     broken feature out of 100 is a broken pipeline. Averaging PSI across the
     universe dilutes it below any threshold: measured, 1 of 100 features fully
     relocated gave a mean PSI of 0.075 and a ``STABLE`` verdict. The mean is
     still reported, but it never decides anything.
  3. **Non-finite input is a diagnosis, not a value.** A single NaN residual
     makes the MSE ratio NaN; every ``>=`` comparison against NaN is ``False``,
     so a NaN-poisoned snapshot classified as ``STABLE`` under the v1 logic.
     Here it returns ``INSUFFICIENT_DATA``. This module deliberately does not
     impute or drop non-finite values - silently repairing the caller's data is
     how a broken feed keeps looking healthy.
  4. **A feature timestamp in the future is reported, not absorbed.** Under v1 a
     clock-skewed timestamp produced a negative age that sailed through the
     ``age > limit`` test and the snapshot was scored on data of unknown
     vintage. ``CLOCK_SKEW`` is a separate status because the fix is a clock,
     not a pipeline.
  5. **Unmeasured fields are ``None``.** A ``DATA_STALENESS`` verdict short-
     circuits before PSI is computed, so it reports ``None`` for PSI - not
     ``0.0``, which a dashboard would happily plot as "no drift observed".
  6. **The PSI threshold can be calibrated.** The 0.10/0.25 bands are a credit-
     scoring rule of thumb with no controlled error rate, and their *power
     decreases* as samples grow (Yurdakul and Naranjo 2020). Trading monitors
     run on large windows, which is precisely where the fixed threshold goes
     blind. Set ``psi_significance_level`` to use their chi-square benchmark
     instead. See ``references/standards.md``.

What this is not:

  - **Not a kill switch.** It returns a classification. Halting the strategy,
    cancelling working orders and flattening positions belong to
    ``kill-switch-and-drawdown-circuit-breakers``.
  - **Not a staleness monitor.** It compares two timestamps the caller supplies.
    Deciding what "the feature timestamp" means across a multi-source feed, and
    tracking it continuously, is ``model-staleness-detection``.
  - **Not a retraining trigger.** ``recommended_action`` is advisory text. The
    decision to retrain, and the governance around it, sit with the caller.
  - **Not a test of statistical significance for the error ratio.** The MSE
    ratio threshold is an operator-chosen heuristic, not a calibrated test - see
    ``error_ratio_threshold``.
  - **Not able to tell covariate shift from concept drift when both fire.** A
    large enough P(X) move pushes the model into extrapolation, which raises
    residuals on its own. When both signals breach, the result is
    ``CONCEPT_DRIFT`` (the more expensive hypothesis) and the shifted features
    are named so a human can adjudicate.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import chi2

logger = logging.getLogger(__name__)

# Proportion substituted for an empty bin so ln(p/q) stays finite. This is the
# conventional PSI treatment, but it means the *magnitude* of PSI for two
# distributions with disjoint support is an artefact of this constant, not a
# distance. Read a very large PSI as "disjoint", never as "n times worse".
_ZERO_PROPORTION_FLOOR = 1e-4


class DiagnosisStatus(str, Enum):
    """
    Operational regime. Subclasses ``str`` so existing callers comparing against
    the v1 string literals (``result.status == "STABLE"``) keep working.
    """

    STABLE = "STABLE"
    DATA_STALENESS = "DATA_STALENESS"
    COVARIATE_SHIFT = "COVARIATE_SHIFT"
    CONCEPT_DRIFT = "CONCEPT_DRIFT"
    CLOCK_SKEW = "CLOCK_SKEW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class DiagnosticResult:
    """
    One monitoring verdict.

    Every numeric field is ``Optional`` and is ``None`` when the quantity was
    not actually measured, because the diagnosis short-circuited earlier. A
    field is never populated with a placeholder that reads like a measurement.
    """

    status: DiagnosisStatus
    recommended_action: str
    detail: str
    staleness_age_sec: Optional[float] = None
    # Trigger statistic: the largest per-feature PSI. Named ``max_feature_psi``
    # rather than the v1 ``feature_psi_score`` because v1 held the *mean*, and
    # silently changing what a field means is worse than renaming it.
    max_feature_psi: Optional[float] = None
    mean_feature_psi: Optional[float] = None
    error_mse_ratio: Optional[float] = None
    # Threshold applied to the feature that produced ``max_feature_psi``. Under
    # chi-square calibration this varies per feature with its usable bin count.
    psi_threshold_applied: Optional[float] = None
    feature_psi_scores: Dict[str, float] = field(default_factory=dict)
    shifted_features: Tuple[str, ...] = ()
    # Features whose reference sample has fewer than two distinct values, so PSI
    # is undefined for them. Excluded from max/mean rather than scored as 0.0.
    degenerate_features: Tuple[str, ...] = ()


class DriftVsStalenessClassifier:
    """
    Classifies a monitoring snapshot into one :class:`DiagnosisStatus`.

    Evaluation order is fixed and deliberate:

      1. clock skew, 2. staleness, 3. data sufficiency, 4. residual error ratio,
      5. feature shift.

    Staleness is resolved before any distribution test because a frozen feed
    reproduces yesterday's distribution *exactly* - a stale snapshot scores a
    near-zero PSI and a near-1.0 error ratio, i.e. it looks pristine.
    ``references/standards.md`` records this as a hard ordering requirement.
    """

    def __init__(
        self,
        max_staleness_sec: float = 300.0,
        psi_threshold: float = 0.25,
        error_ratio_threshold: float = 1.50,
        num_bins: int = 10,
        min_samples: int = 100,
        clock_skew_tolerance_sec: float = 0.0,
        psi_significance_level: Optional[float] = None,
    ) -> None:
        """
        :param max_staleness_sec: feature age above which the snapshot is
            ``DATA_STALENESS``. Strict ``>``: an age exactly equal to the limit
            is not stale. Must be set from the strategy's own decision cadence -
            300s is a placeholder, and is far too loose for anything intraday.
        :param psi_threshold: fixed per-feature PSI trigger. The 0.25 default is
            the credit-scoring rule of thumb quoted in Yurdakul and Naranjo
            (2020); it has no controlled type I or type II error rate. Ignored
            when ``psi_significance_level`` is set.
        :param error_ratio_threshold: MSE(current)/MSE(reference) at or above
            which residuals count as degraded. This is a **heuristic**, not a
            test. No parametric test is offered here on purpose: financial
            residuals are fat-tailed and autocorrelated, so the F-ratio's
            nominal error rate does not hold. Calibrate this against the
            strategy's own historical distribution of rolling MSE ratios.
        :param num_bins: quantile bins used to build the PSI histogram from the
            reference sample.
        :param min_samples: minimum observations required in each of the four
            input arrays before a diagnosis is attempted. Yurdakul and Naranjo
            (2020) confirm the PSI chi-square approximation for sample sizes as
            low as 100, which is where this default comes from.
        :param clock_skew_tolerance_sec: how far the feature timestamp may lead
            the evaluation timestamp before the snapshot is ``CLOCK_SKEW``.
            Defaults to 0.0 (any future-dated feature is flagged); operators
            with distributed clocks should set this to their PTP/NTP bound.
        :param psi_significance_level: if set (e.g. ``0.05``), each feature is
            compared against the sample-size-aware benchmark
            ``(1/n + 1/m) * chi2.ppf(1 - alpha, B - 1)`` from Yurdakul and
            Naranjo (2020) eq. 4.1 instead of ``psi_threshold``.
        :raises ValueError: on any non-sensical configuration.
        """
        if not math.isfinite(max_staleness_sec) or max_staleness_sec < 0:
            raise ValueError("max_staleness_sec must be a finite, non-negative number of seconds")
        if not math.isfinite(psi_threshold) or psi_threshold <= 0:
            raise ValueError("psi_threshold must be a finite, positive number")
        if not math.isfinite(error_ratio_threshold) or error_ratio_threshold <= 0:
            raise ValueError("error_ratio_threshold must be a finite, positive number")
        if num_bins < 2:
            raise ValueError("num_bins must be at least 2")
        if min_samples < 2:
            raise ValueError("min_samples must be at least 2")
        if not math.isfinite(clock_skew_tolerance_sec) or clock_skew_tolerance_sec < 0:
            raise ValueError("clock_skew_tolerance_sec must be a finite, non-negative number of seconds")
        if psi_significance_level is not None and not 0.0 < psi_significance_level < 1.0:
            raise ValueError("psi_significance_level must lie strictly between 0 and 1")

        self.max_staleness_sec = max_staleness_sec
        self.psi_threshold = psi_threshold
        self.error_ratio_threshold = error_ratio_threshold
        self.num_bins = num_bins
        self.min_samples = min_samples
        self.clock_skew_tolerance_sec = clock_skew_tolerance_sec
        self.psi_significance_level = psi_significance_level

        if psi_significance_level is not None:
            logger.info(
                "PSI trigger is chi-square calibrated at alpha=%s; psi_threshold=%s is ignored.",
                psi_significance_level, psi_threshold,
            )

    # ------------------------------------------------------------------ PSI --

    def calculate_psi(
        self,
        ref_data: np.ndarray,
        curr_data: np.ndarray,
        num_bins: Optional[int] = None,
    ) -> float:
        """
        Population Stability Index of ``curr_data`` against ``ref_data``.

        Bin edges are the reference quantiles, de-duplicated, with the outer two
        edges replaced by -inf/+inf so that current observations outside the
        reference support are counted in the tail bins rather than dropped by
        ``numpy.histogram`` (which ignores out-of-range values).

        :returns: PSI, or ``nan`` when the reference sample has fewer than two
            distinct values, in which case PSI is undefined. ``nan`` is returned
            rather than ``0.0`` so a constant reference feature cannot be
            mistaken for a stable one.
        :raises ValueError: if either array is empty, not one-dimensional, or
            contains non-finite values.
        """
        ref = np.asarray(ref_data, dtype=float)
        curr = np.asarray(curr_data, dtype=float)
        if ref.ndim != 1 or curr.ndim != 1:
            raise ValueError("calculate_psi expects one-dimensional samples")
        if ref.size == 0 or curr.size == 0:
            raise ValueError("calculate_psi requires non-empty reference and current samples")
        if not np.all(np.isfinite(ref)) or not np.all(np.isfinite(curr)):
            raise ValueError("calculate_psi requires finite values; NaN/inf must be resolved upstream")

        bins = int(self.num_bins if num_bins is None else num_bins)
        if bins < 2:
            raise ValueError("num_bins must be at least 2")

        edges = self._bin_edges(ref, bins)
        if edges is None:
            return float("nan")

        ref_counts, _ = np.histogram(ref, bins=edges)
        curr_counts, _ = np.histogram(curr, bins=edges)

        ref_pcts = np.where(ref_counts == 0, _ZERO_PROPORTION_FLOOR, ref_counts / ref.size)
        curr_pcts = np.where(curr_counts == 0, _ZERO_PROPORTION_FLOOR, curr_counts / curr.size)

        return float(np.sum((curr_pcts - ref_pcts) * np.log(curr_pcts / ref_pcts)))

    def psi_benchmark(self, n_ref: int, n_curr: int, num_bins: int) -> float:
        """
        Sample-size-aware PSI trigger from Yurdakul and Naranjo (2020) eq. 4.1:
        reject population stability at level ``alpha`` when

            PSI > (1/n + 1/m) * chi2_{alpha, B-1}

        which follows from their Theorem 3.3, ``(1/n + 1/m)^-1 * PSI`` being
        approximately chi-square with ``B - 1`` degrees of freedom under the
        null of no shift.

        :param num_bins: the number of bins *actually used*, i.e. one fewer than
            the count of distinct edges.
        """
        if self.psi_significance_level is None:
            raise ValueError("psi_benchmark requires psi_significance_level to be configured")
        if n_ref < 1 or n_curr < 1 or num_bins < 2:
            raise ValueError("psi_benchmark requires positive sample sizes and at least 2 bins")
        return float(
            (1.0 / n_ref + 1.0 / n_curr) * chi2.ppf(1.0 - self.psi_significance_level, num_bins - 1)
        )

    # ----------------------------------------------------------- diagnosis --

    def diagnose(
        self,
        feature_timestamp_sec: float,
        current_timestamp_sec: float,
        X_ref: np.ndarray,
        X_curr: np.ndarray,
        residuals_ref: np.ndarray,
        residuals_curr: np.ndarray,
        feature_names: Optional[Sequence[str]] = None,
    ) -> DiagnosticResult:
        """
        Classify one monitoring snapshot.

        ``X_ref``/``X_curr`` are ``(observations, features)``; a 1-D array is
        read as a single feature. The two matrices must agree on feature count
        but need not have the same number of observations. Residuals are
        ``prediction - actual`` (only their squares are used, so the sign
        convention does not matter as long as it is consistent).

        Structural problems - wrong dimensionality, mismatched feature counts,
        non-numeric timestamps - raise. Data-condition problems - too few
        observations, non-finite values - return ``INSUFFICIENT_DATA``, because
        those are states a live monitor must be able to report and alert on
        rather than crash on.

        :raises ValueError: on structurally invalid input.
        """
        timestamps = {}
        for label, value in (("feature_timestamp_sec", feature_timestamp_sec),
                             ("current_timestamp_sec", current_timestamp_sec)):
            timestamps[label] = self._as_epoch_seconds(label, value)

        age_sec = timestamps["current_timestamp_sec"] - timestamps["feature_timestamp_sec"]

        # 1. Clock skew. Checked before staleness: a negative age is not a fresh
        #    feature, it is a timestamp of unknown provenance.
        if age_sec < -self.clock_skew_tolerance_sec:
            detail = (f"Feature timestamp leads evaluation timestamp by {-age_sec:.3f}s "
                      f"(tolerance {self.clock_skew_tolerance_sec:.3f}s).")
            logger.error("CLOCK SKEW: %s", detail)
            return DiagnosticResult(
                status=DiagnosisStatus.CLOCK_SKEW,
                recommended_action=(
                    "Do not trade on this snapshot. Reconcile host clocks (NTP/PTP) and the feed's "
                    "timestamp source before drawing any drift conclusion."),
                detail=detail,
                staleness_age_sec=round(age_sec, 6),
            )

        # 2. Staleness, before any distribution test: a frozen feed replays the
        #    reference distribution and therefore looks perfectly stable.
        if age_sec > self.max_staleness_sec:
            detail = (f"Feature age {age_sec:.3f}s exceeds max_staleness_sec "
                      f"{self.max_staleness_sec:.3f}s.")
            logger.error("DATA STALENESS: %s", detail)
            return DiagnosticResult(
                status=DiagnosisStatus.DATA_STALENESS,
                recommended_action=(
                    "Inspect the data ingestion pipeline and refresh the feature feed. Do NOT "
                    "retrain: the model has not been shown to be at fault."),
                detail=detail,
                staleness_age_sec=round(age_sec, 6),
            )

        X_ref_2d, X_curr_2d = self._as_feature_matrices(X_ref, X_curr)
        names = self._resolve_feature_names(feature_names, X_ref_2d.shape[1])
        res_ref = np.asarray(residuals_ref, dtype=float).ravel()
        res_curr = np.asarray(residuals_curr, dtype=float).ravel()

        # 3. Data sufficiency. Anything unmeasurable is reported as such; it is
        #    never allowed to fall through to STABLE.
        problem = self._data_condition_problem(X_ref_2d, X_curr_2d, res_ref, res_curr)
        if problem is not None:
            logger.error("INSUFFICIENT DATA: %s", problem)
            return DiagnosticResult(
                status=DiagnosisStatus.INSUFFICIENT_DATA,
                recommended_action=(
                    "No drift conclusion is possible. Repair the monitoring inputs; treat the model "
                    "as unverified, not as healthy, until they are repaired."),
                detail=problem,
                staleness_age_sec=round(age_sec, 6),
            )

        # 4. Residual error ratio - the P(Y|X) signal.
        mse_ref = float(np.mean(res_ref ** 2))
        mse_curr = float(np.mean(res_curr ** 2))
        if mse_ref == 0.0:
            detail = ("Reference MSE is exactly zero, so the error ratio is undefined. A zero-residual "
                      "reference window almost always means the residuals were mis-assembled.")
            logger.error("INSUFFICIENT DATA: %s", detail)
            return DiagnosticResult(
                status=DiagnosisStatus.INSUFFICIENT_DATA,
                recommended_action=(
                    "Rebuild the reference residual series before re-running the diagnosis."),
                detail=detail,
                staleness_age_sec=round(age_sec, 6),
            )
        error_ratio = mse_curr / mse_ref

        # 5. Feature shift - the P(X) signal. Triggered on the per-feature
        #    maximum; the mean is reported but decides nothing.
        psi_scores: Dict[str, float] = {}
        thresholds: Dict[str, float] = {}
        degenerate: List[str] = []
        for col, name in enumerate(names):
            ref_col = X_ref_2d[:, col]
            curr_col = X_curr_2d[:, col]
            psi = self.calculate_psi(ref_col, curr_col)
            if math.isnan(psi):
                degenerate.append(name)
                continue
            psi_scores[name] = psi
            thresholds[name] = self._threshold_for(ref_col, curr_col)

        if not psi_scores:
            detail = (f"Every one of the {len(names)} reference feature(s) is constant, so PSI is "
                      f"undefined for all of them.")
            logger.error("INSUFFICIENT DATA: %s", detail)
            return DiagnosticResult(
                status=DiagnosisStatus.INSUFFICIENT_DATA,
                recommended_action=(
                    "Repair the reference feature matrix before re-running the diagnosis."),
                detail=detail,
                staleness_age_sec=round(age_sec, 6),
                error_mse_ratio=round(error_ratio, 6),
                degenerate_features=tuple(degenerate),
            )

        if degenerate:
            logger.warning("PSI undefined for constant reference feature(s): %s", ", ".join(degenerate))

        shifted = tuple(name for name, psi in psi_scores.items() if psi > thresholds[name])
        max_name = max(psi_scores, key=psi_scores.__getitem__)
        max_psi = psi_scores[max_name]
        mean_psi = float(np.mean(list(psi_scores.values())))

        is_high_error_ratio = error_ratio >= self.error_ratio_threshold
        is_high_feature_shift = bool(shifted)

        if is_high_error_ratio:
            status = DiagnosisStatus.CONCEPT_DRIFT
            action = ("Alpha decay / structural shift: residual error is up while P(X) alone does not "
                      "explain it. Shorten the lookback window, re-specify features, or retire the "
                      "strategy. Retraining on the same feature set is unlikely to recover the edge.")
            if is_high_feature_shift:
                action += (f" NOTE: {len(shifted)} feature(s) also breached their PSI trigger "
                           f"({', '.join(shifted)}); a large P(X) move can inflate residuals through "
                           f"extrapolation alone, so confirm before concluding the edge is gone.")
        elif is_high_feature_shift:
            status = DiagnosisStatus.COVARIATE_SHIFT
            action = (f"P(X) shifted on {len(shifted)} feature(s) ({', '.join(shifted)}) while residual "
                      f"error held. The learned relationship still appears intact: refit on the updated "
                      f"input distribution and check those features for a pipeline or reference-data "
                      f"cause.")
        else:
            status = DiagnosisStatus.STABLE
            action = "Model operating within expected performance bounds."

        detail = (f"max PSI {max_psi:.4f} on '{max_name}' (trigger {thresholds[max_name]:.4f}), "
                  f"mean PSI {mean_psi:.4f} over {len(psi_scores)} feature(s), MSE ratio "
                  f"{error_ratio:.4f} (trigger {self.error_ratio_threshold:.4f}).")
        logger.info("Diagnosis %s: %s", status.value, detail)

        return DiagnosticResult(
            status=status,
            recommended_action=action,
            detail=detail,
            staleness_age_sec=round(age_sec, 6),
            max_feature_psi=round(max_psi, 6),
            mean_feature_psi=round(mean_psi, 6),
            error_mse_ratio=round(error_ratio, 6),
            psi_threshold_applied=round(thresholds[max_name], 6),
            feature_psi_scores={name: round(psi, 6) for name, psi in psi_scores.items()},
            shifted_features=shifted,
            degenerate_features=tuple(degenerate),
        )

    # ------------------------------------------------------------- helpers --

    @staticmethod
    def _as_epoch_seconds(label: str, value: object) -> float:
        """
        Coerce one timestamp to finite epoch seconds.

        Deliberately duck-typed rather than an ``isinstance`` check against
        ``numbers.Real``: a timestamp taken straight from a DataFrame column is
        an ``np.int64``, which is not a Python ``int``, and the ``numbers`` ABC
        registration NumPy performs is tied to the identity of the ``numbers``
        module object - a test runner that purges ``sys.modules`` between files
        can hand this module a freshly imported ``numbers`` whose ABCs NumPy
        never registered against. ``bool`` is excluded because ``True`` is a
        perfectly valid ``float()`` argument and a nonsensical timestamp;
        ``str``/``bytes`` because ``float("1000")`` would otherwise succeed and
        silently accept a text timestamp of unknown units.
        """
        if isinstance(value, (bool, str, bytes, bytearray)):
            raise ValueError(
                f"{label} must be a finite numeric epoch-seconds value, got {value!r}")
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{label} must be a finite numeric epoch-seconds value, got {value!r}") from exc
        if not math.isfinite(seconds):
            raise ValueError(
                f"{label} must be a finite numeric epoch-seconds value, got {value!r}")
        return seconds

    def _threshold_for(self, ref_col: np.ndarray, curr_col: np.ndarray) -> float:
        """PSI trigger for one feature: fixed, or chi-square calibrated."""
        if self.psi_significance_level is None:
            return self.psi_threshold
        edges = self._bin_edges(ref_col, self.num_bins)
        if edges is None:                      # constant features are filtered out upstream
            raise ValueError("cannot benchmark a constant reference feature")
        return self.psi_benchmark(ref_col.size, curr_col.size, num_bins=edges.size - 1)

    @staticmethod
    def _bin_edges(ref: np.ndarray, bins: int) -> Optional[np.ndarray]:
        """
        Bin edges for one reference sample, with unbounded extremes.

        Quantile edges collapse whenever the reference is concentrated on a few
        values - a halt flag, a regime indicator, a mostly-zero event count. A
        95/5 binary reference over 10 bins de-duplicates to *two* edges, i.e. a
        single bin spanning everything, and PSI is then identically 0.0 whatever
        the current sample does. Measured: a 95/5 indicator that flipped to
        50/50 scored PSI 0.0. When the quantile scheme collapses, fall back to
        midpoints between the distinct reference values.

        :returns: edges, or ``None`` if the reference has fewer than two
            distinct values, in which case no binning scheme can express a shift
            away from it.
        """
        distinct = np.unique(ref)
        if distinct.size < 2:
            return None

        edges = np.unique(np.percentile(ref, np.linspace(0.0, 100.0, bins + 1)))
        if edges.size >= 3:
            edges[0] = -np.inf
            edges[-1] = np.inf
            return edges

        if distinct.size > bins:
            # Concentrated but high-cardinality: keep an evenly spaced subset of
            # the distinct values so the sparse tail still gets its own bins.
            distinct = np.unique(distinct[np.linspace(0, distinct.size - 1, bins).astype(int)])
        # Midpoints as a + (b - a) / 2 rather than (a + b) / 2, which can
        # overflow for extreme magnitudes.
        mids = distinct[:-1] + (distinct[1:] - distinct[:-1]) / 2.0
        return np.concatenate(([-np.inf], mids, [np.inf]))

    @staticmethod
    def _as_feature_matrices(X_ref: np.ndarray, X_curr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Coerce both feature blocks to 2-D and check they describe the same features."""
        matrices = []
        for label, raw in (("X_ref", X_ref), ("X_curr", X_curr)):
            arr = np.asarray(raw, dtype=float)
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            elif arr.ndim != 2:
                raise ValueError(
                    f"{label} must be 1-D or 2-D (observations, features), got {arr.ndim}-D")
            matrices.append(arr)
        ref_2d, curr_2d = matrices
        if ref_2d.shape[1] != curr_2d.shape[1]:
            raise ValueError(
                f"X_ref has {ref_2d.shape[1]} feature(s) but X_curr has {curr_2d.shape[1]}; the two "
                f"matrices must describe the same feature set in the same order")
        if ref_2d.shape[1] == 0:
            raise ValueError("X_ref and X_curr must contain at least one feature column")
        return ref_2d, curr_2d

    @staticmethod
    def _resolve_feature_names(
        feature_names: Optional[Sequence[str]],
        n_features: int,
    ) -> Tuple[str, ...]:
        if feature_names is None:
            return tuple(f"feature_{i}" for i in range(n_features))
        names = tuple(str(n) for n in feature_names)
        if len(names) != n_features:
            raise ValueError(
                f"feature_names has {len(names)} entries but there are {n_features} feature columns")
        if len(set(names)) != len(names):
            raise ValueError("feature_names must be unique; per-feature PSI is reported by name")
        return names

    def _data_condition_problem(
        self,
        X_ref: np.ndarray,
        X_curr: np.ndarray,
        res_ref: np.ndarray,
        res_curr: np.ndarray,
    ) -> Optional[str]:
        """
        First reason the snapshot cannot be scored, or ``None``.

        Non-finite values are reported rather than dropped: imputing them here
        would let a partially broken feed keep producing confident verdicts.
        """
        for label, arr in (("X_ref", X_ref), ("X_curr", X_curr),
                           ("residuals_ref", res_ref), ("residuals_curr", res_curr)):
            observations = arr.shape[0]
            if observations < self.min_samples:
                return (f"{label} has {observations} observation(s), below "
                        f"min_samples={self.min_samples}; PSI and MSE ratio are not trustworthy on "
                        f"samples this small.")
            non_finite = int(np.count_nonzero(~np.isfinite(arr)))
            if non_finite:
                return (f"{label} contains {non_finite} non-finite value(s). This module does not "
                        f"impute or drop them, because a NaN-poisoned snapshot must not be scored "
                        f"as healthy.")
        return None
