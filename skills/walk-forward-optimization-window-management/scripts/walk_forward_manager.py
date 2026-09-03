"""
walk-forward-optimization-window-management: Production-grade Walk-Forward Optimization (WFO)
window slice generator, temporal isolation leakage validator, and Walk-Forward Efficiency (WFE) evaluator.

Window geometry produced for each slice (all bounds inclusive, calendar days):

    [warmup_start .. is_start-1]   indicator warm-up  (never scored)
    [is_start     .. is_end     ]  in-sample optimization interval
    [is_end+1     .. embargo_end]  purge/embargo gap  (never trained on, never scored)
    [oos_start    .. oos_end    ]  out-of-sample evaluation interval

The embargo gap is what makes "no lookahead leakage" true rather than merely adjacent: strict
chronological ordering alone does not prevent leakage when a feature has a multi-bar lookback
or a label spans multiple bars across the IS/OOS boundary (Lopez de Prado, "Advances in
Financial Machine Learning", Wiley 2018, ch. 7.4.1 Purging / 7.4.2 Embargo).
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
import math
from typing import List, Optional

logger = logging.getLogger(__name__)


class WalkForwardError(ValueError):
    """Raised when window configuration is invalid or temporal leakage occurs."""
    pass


class WindowMode(str, Enum):
    ROLLING = "ROLLING"    # Fixed-length sliding in-sample window
    ANCHORED = "ANCHORED"  # Expanding in-sample window anchored to start_date


@dataclass
class WindowSlice:
    index: int
    warmup_start: datetime.date
    is_start: datetime.date
    is_end: datetime.date
    oos_start: datetime.date
    oos_end: datetime.date
    # Inclusive bounds of the purge/embargo gap between IS and OOS. None when embargo_days == 0.
    embargo_start: Optional[datetime.date] = None
    embargo_end: Optional[datetime.date] = None


@dataclass
class WFEEvaluation:
    wfe_ratio: float
    is_sharpe: float
    oos_sharpe: float
    is_robust: bool  # True only if WFE >= min_wfe_threshold AND the ratio is well defined
    # "" when the ratio is well defined; otherwise why it is not (wfe_ratio is then NaN).
    undefined_reason: str = ""


class WalkForwardWindowManager:
    """
    Generates Walk-Forward Optimization time windows separated by an explicit purge/embargo
    gap, enforces strict temporal isolation to eliminate lookahead bias, and evaluates
    out-of-sample efficiency.

    Args:
        in_sample_days: Length of the in-sample optimization interval, in calendar days.
        out_of_sample_days: Length of the out-of-sample evaluation interval, in calendar days.
        step_days: Calendar days the window cursor advances between slices. Must be
            >= out_of_sample_days unless ``allow_overlapping_oos`` is set, otherwise the
            stitched OOS equity curve double-counts the overlapping periods.
        warmup_days: Calendar days of indicator warm-up preceding ``is_start``. Warm-up bars
            initialise indicator state only and must never be scored.
        embargo_days: Purge/embargo gap between ``is_end`` and ``oos_start``. Set this to at
            least the longest feature lookback or label horizon the strategy uses; leaving it
            at 0 makes IS and OOS merely adjacent, which does not prevent boundary leakage for
            lookback features or multi-bar labels.
        mode: ROLLING (fixed IS length) or ANCHORED (IS expands from ``start_date``).
        min_wfe_threshold: WFE at or above which a slice is reported robust. The >= 0.50
            convention comes from walk-forward analysis practice (Pardo; TradeStation Walk-
            Forward Optimizer) and is a heuristic, not a statistical guarantee.
        min_is_sharpe: The in-sample Sharpe must be strictly greater than this for the WFE
            ratio to be defined. A non-positive or near-zero denominator makes the ratio
            meaningless, so those cases are reported undefined rather than clamped.
        allow_overlapping_oos: Opt in to ``step_days < out_of_sample_days``. Overlapping OOS
            intervals must not be concatenated into a single equity curve.
    """

    def __init__(
        self,
        in_sample_days: int = 365,
        out_of_sample_days: int = 90,
        step_days: int = 90,
        warmup_days: int = 30,
        mode: WindowMode = WindowMode.ROLLING,
        min_wfe_threshold: float = 0.50,
        # Appended after the pre-existing parameters so positional calls stay compatible.
        embargo_days: int = 0,
        min_is_sharpe: float = 0.0,
        allow_overlapping_oos: bool = False,
    ):
        self._validate_positive_int("in_sample_days", in_sample_days)
        self._validate_positive_int("out_of_sample_days", out_of_sample_days)
        self._validate_positive_int("step_days", step_days)
        self._validate_non_negative_int("warmup_days", warmup_days)
        self._validate_non_negative_int("embargo_days", embargo_days)

        if not isinstance(mode, WindowMode):
            raise WalkForwardError(f"mode must be a WindowMode, got {type(mode).__name__}.")
        if not math.isfinite(min_wfe_threshold):
            raise WalkForwardError("min_wfe_threshold must be a finite number.")
        if not math.isfinite(min_is_sharpe):
            raise WalkForwardError("min_is_sharpe must be a finite number.")
        if not allow_overlapping_oos and step_days < out_of_sample_days:
            raise WalkForwardError(
                f"step_days ({step_days}) < out_of_sample_days ({out_of_sample_days}) produces "
                f"overlapping out-of-sample intervals, which double-count periods when the OOS "
                f"equity curves are concatenated. Set step_days >= out_of_sample_days, or pass "
                f"allow_overlapping_oos=True and do not stitch the resulting OOS curves."
            )

        self.in_sample_days = in_sample_days
        self.out_of_sample_days = out_of_sample_days
        self.step_days = step_days
        self.warmup_days = warmup_days
        self.embargo_days = embargo_days
        self.mode = mode
        self.min_wfe_threshold = min_wfe_threshold
        self.min_is_sharpe = min_is_sharpe
        self.allow_overlapping_oos = allow_overlapping_oos

    @staticmethod
    def _validate_positive_int(name: str, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise WalkForwardError(f"{name} must be an integer >= 1, got {value!r}.")

    @staticmethod
    def _validate_non_negative_int(name: str, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise WalkForwardError(f"{name} must be an integer >= 0, got {value!r}.")

    @staticmethod
    def _as_date(name: str, value: datetime.date) -> datetime.date:
        # datetime.datetime subclasses datetime.date; mixing the two yields timedeltas with a
        # time component and slice bounds that will not compare cleanly against plain dates.
        if isinstance(value, datetime.datetime):
            raise WalkForwardError(
                f"{name} must be a datetime.date, not a datetime.datetime - pass {name}.date()."
            )
        if not isinstance(value, datetime.date):
            raise WalkForwardError(f"{name} must be a datetime.date, got {type(value).__name__}.")
        return value

    def min_required_days(self) -> int:
        """Inclusive calendar-day span the dataset must cover to yield at least one slice."""
        return self.in_sample_days + self.embargo_days + self.out_of_sample_days

    def generate_windows(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> List[WindowSlice]:
        """Generates the sequence of In-Sample and Out-of-Sample window slices.

        ``start_date`` and ``end_date`` are both inclusive. Warm-up bars are drawn from before
        ``start_date``; the caller is responsible for having that history available.
        """
        start_date = self._as_date("start_date", start_date)
        end_date = self._as_date("end_date", end_date)
        if end_date < start_date:
            raise WalkForwardError(f"end_date ({end_date}) precedes start_date ({start_date}).")

        total_days = (end_date - start_date).days + 1  # inclusive of both endpoints
        min_required = self.min_required_days()
        if total_days < min_required:
            raise WalkForwardError(
                f"Date range ({total_days} days) is less than required minimum ({min_required} days)."
            )

        if self.embargo_days == 0:
            logger.warning(
                "embargo_days=0: IS and OOS intervals are merely adjacent. Set embargo_days to at "
                "least the longest feature lookback or label horizon to prevent boundary leakage."
            )

        slices: List[WindowSlice] = []
        slice_idx = 0
        cursor = start_date  # start of the current in-sample interval in ROLLING mode

        while True:
            is_start = cursor if self.mode == WindowMode.ROLLING else start_date
            is_end = cursor + datetime.timedelta(days=self.in_sample_days - 1)

            if self.embargo_days > 0:
                embargo_start = is_end + datetime.timedelta(days=1)
                embargo_end = is_end + datetime.timedelta(days=self.embargo_days)
            else:
                embargo_start = None
                embargo_end = None

            oos_start = is_end + datetime.timedelta(days=self.embargo_days + 1)
            oos_end = oos_start + datetime.timedelta(days=self.out_of_sample_days - 1)

            if oos_end > end_date:
                break

            slice_obj = WindowSlice(
                index=slice_idx,
                warmup_start=is_start - datetime.timedelta(days=self.warmup_days),
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
                embargo_start=embargo_start,
                embargo_end=embargo_end,
            )

            self.validate_window_isolation(slice_obj, min_embargo_days=self.embargo_days)
            slices.append(slice_obj)

            slice_idx += 1
            cursor = cursor + datetime.timedelta(days=self.step_days)

        if not self.allow_overlapping_oos:
            self.validate_slice_sequence(slices)

        logger.info(f"Generated {len(slices)} WFO window slices ({self.mode.value} mode).")
        return slices

    @staticmethod
    def validate_window_isolation(slice_obj: WindowSlice, min_embargo_days: int = 0) -> None:
        """Verifies strict temporal isolation between the IS and OOS windows of one slice.

        Checks interval well-formedness, that OOS begins strictly after IS ends, and that the
        realised gap covers ``min_embargo_days``. Raises WalkForwardError on any violation.
        """
        if slice_obj.is_start > slice_obj.is_end:
            raise WalkForwardError(f"Invalid IS window in Slice {slice_obj.index}: start > end.")
        if slice_obj.oos_start > slice_obj.oos_end:
            raise WalkForwardError(f"Invalid OOS window in Slice {slice_obj.index}: start > end.")
        if slice_obj.warmup_start > slice_obj.is_start:
            raise WalkForwardError(
                f"Invalid warm-up window in Slice {slice_obj.index}: "
                f"warmup_start ({slice_obj.warmup_start}) > is_start ({slice_obj.is_start})."
            )
        if slice_obj.is_end >= slice_obj.oos_start:
            raise WalkForwardError(
                f"LOOKAHEAD LEAKAGE DETECTED in Slice {slice_obj.index}! "
                f"IS end ({slice_obj.is_end}) >= OOS start ({slice_obj.oos_start})."
            )

        gap_days = (slice_obj.oos_start - slice_obj.is_end).days - 1
        if gap_days < min_embargo_days:
            raise WalkForwardError(
                f"EMBARGO VIOLATION in Slice {slice_obj.index}: gap between IS end "
                f"({slice_obj.is_end}) and OOS start ({slice_obj.oos_start}) is {gap_days} days, "
                f"below the required embargo of {min_embargo_days} days."
            )

        if (slice_obj.embargo_start is None) != (slice_obj.embargo_end is None):
            raise WalkForwardError(
                f"Slice {slice_obj.index} declares only one embargo bound; set both or neither."
            )
        if slice_obj.embargo_start is not None and slice_obj.embargo_end is not None:
            if slice_obj.embargo_start <= slice_obj.is_end:
                raise WalkForwardError(
                    f"Slice {slice_obj.index}: embargo_start ({slice_obj.embargo_start}) overlaps "
                    f"the in-sample window ending {slice_obj.is_end}."
                )
            if slice_obj.embargo_end >= slice_obj.oos_start:
                raise WalkForwardError(
                    f"Slice {slice_obj.index}: embargo_end ({slice_obj.embargo_end}) overlaps the "
                    f"out-of-sample window starting {slice_obj.oos_start}."
                )

    @staticmethod
    def validate_slice_sequence(slices: List[WindowSlice]) -> None:
        """Verifies the slice sequence is chronological with non-overlapping OOS intervals.

        This is the invariant that makes concatenating per-slice OOS equity curves into one
        continuous out-of-sample track record valid. Gaps between consecutive OOS intervals
        (step_days > out_of_sample_days) are legal but logged, since the untested days are
        absent from the stitched curve.
        """
        for previous, current in zip(slices, slices[1:]):
            if current.is_start < previous.is_start or current.oos_start <= previous.oos_start:
                raise WalkForwardError(
                    f"Slice {current.index} is not chronologically after slice {previous.index}."
                )
            if current.oos_start <= previous.oos_end:
                raise WalkForwardError(
                    f"OVERLAPPING OOS INTERVALS: slice {previous.index} ends {previous.oos_end} "
                    f"but slice {current.index} starts {current.oos_start}. Concatenating these "
                    f"equity curves would double-count the overlap."
                )
            gap_days = (current.oos_start - previous.oos_end).days - 1
            if gap_days > 0:
                logger.info(
                    f"{gap_days}-day untested gap between OOS slice {previous.index} and "
                    f"{current.index}; those days are absent from the stitched OOS curve."
                )

    def calculate_wfe(self, is_sharpe: float, oos_sharpe: float) -> WFEEvaluation:
        """
        Calculates Walk-Forward Efficiency, here the ratio OOS Sharpe / IS Sharpe.

        Both inputs must be computed on the same annualization basis; comparing an annualized
        figure against a per-period one silently rescales the ratio.

        The classical WFE of walk-forward analysis is the ratio of annualized *rates of return*
        (Pardo; TradeStation Walk-Forward Optimizer), with >= 50% treated as a successful walk-
        forward. This method applies that same ratio convention to the Sharpe ratio, which is
        the variant this skill standardises on.

        When the in-sample Sharpe is not strictly above ``min_is_sharpe``, or either input is
        not finite, the ratio is undefined: ``wfe_ratio`` is NaN, ``is_robust`` is False, and
        ``undefined_reason`` explains why. The denominator is never clamped - clamping turns a
        losing in-sample fit into a spuriously large "robust" ratio.
        """
        if not math.isfinite(is_sharpe) or not math.isfinite(oos_sharpe):
            return WFEEvaluation(
                wfe_ratio=float("nan"),
                is_sharpe=is_sharpe,
                oos_sharpe=oos_sharpe,
                is_robust=False,
                undefined_reason="Sharpe inputs must be finite numbers.",
            )

        if is_sharpe <= self.min_is_sharpe:
            reason = (
                f"In-sample Sharpe ({is_sharpe:.4f}) is not above min_is_sharpe "
                f"({self.min_is_sharpe:.4f}); the WFE ratio is undefined and the parameter set "
                f"has no in-sample edge to generalize."
            )
            logger.warning(f"WFE undefined: {reason}")
            return WFEEvaluation(
                wfe_ratio=float("nan"),
                is_sharpe=is_sharpe,
                oos_sharpe=oos_sharpe,
                is_robust=False,
                undefined_reason=reason,
            )

        wfe = oos_sharpe / is_sharpe
        is_robust = wfe >= self.min_wfe_threshold

        logger.info(
            f"WFE Evaluation: OOS Sharpe ({oos_sharpe:.2f}) / IS Sharpe ({is_sharpe:.2f}) = {wfe:.2f} (Robust: {is_robust})"
        )
        return WFEEvaluation(
            wfe_ratio=wfe,
            is_sharpe=is_sharpe,
            oos_sharpe=oos_sharpe,
            is_robust=is_robust,
        )
