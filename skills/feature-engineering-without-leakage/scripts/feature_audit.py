"""
feature-engineering-without-leakage: feature leakage audit engine.

Implements three independent leakage screens, because no single one is sufficient:

1. Statistical association screen (`audit_dataframe`) -- Pearson and Spearman
   correlation of each feature against the target at lag 0 and at leads t+1..t+k,
   plus a perfect-separation test for categorical targets (rank AUC when binary,
   ordered class intervals for 3+ levels). Catches features that are copies
   (linear, monotone, or sign-preserving) of the target.
2. Structural causality screen (`audit_feature_causality`) -- prefix-invariance
   (truncation) test of a feature-construction function. Catches centred rolling
   windows, negative shifts, and whole-sample normalisation, none of which the
   association screen can see when the target is noisy.
3. Point-in-time join screens (`point_in_time_asof_merge`, `verify_asof_timing`)
   -- enforce and then verify that every joined record's publication timestamp
   precedes the decision timestamp.

All three are *candidate filters*, not proofs of absence: a clean report means no
screen fired, not that the feature set is leakage-free. See `references/standards.md`.
"""
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Callable, List, Optional, Sequence, Tuple, Union
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# A target with at most this many distinct values is treated as categorical and gets a
# separation test in addition to correlation; anything wider is screened by correlation
# only. Direction labels are the motivating case, and real return series produce three
# levels ({-1, 0, +1}) as soon as any bar closes unchanged.
_MAX_CATEGORICAL_TARGET_LEVELS = 10
_BINARY_TARGET_LEVELS = 2

# Truncation points for the prefix-invariance test, as fractions of the frame. Spread
# across the second half so each cut probes a different boundary: a forward-looking
# dependency is only exposed by a cut its reach actually crosses.
_DEFAULT_CUT_FRACTIONS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


class LeakageType(str, Enum):
    FUTURE_LOOKAHEAD = "FUTURE_LOOKAHEAD"
    SAME_BAR_CONTAMINATION = "SAME_BAR_CONTAMINATION"
    UNSHIFTED_ROLLING = "UNSHIFTED_ROLLING"
    ASOF_TIMING_VIOLATION = "ASOF_TIMING_VIOLATION"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class LeakageFinding:
    feature_name: str
    leakage_type: LeakageType
    max_correlation_lead: int
    correlation_value: float
    message: str
    method: str = "pearson"


def _rank_auc(feature: pd.Series, target: pd.Series) -> Optional[float]:
    """
    Rank-based AUC of ``feature`` as a score for a binary ``target``, via the
    Mann-Whitney U identity ``AUC = (R1 - n1(n1+1)/2) / (n1 * n2)`` where ``R1`` is
    the sum of the positive class's ranks. Returns None when the target is not
    binary or either class is empty.

    An AUC of exactly 1.0 (or 0.0) means the target is perfectly recoverable from
    this one feature by a single threshold -- a deterministic relationship, which on
    a noisy financial label is contamination rather than predictive edge.
    """
    mask = feature.notna() & target.notna()
    feature, target = feature[mask], target[mask]
    levels = pd.unique(target)
    if len(levels) != _BINARY_TARGET_LEVELS:
        return None

    positive = target == max(levels)
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    if n_pos == 0 or n_neg == 0:
        return None

    ranks = feature.rank(method="average")
    rank_sum_positive = float(ranks[positive].sum())
    return (rank_sum_positive - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _is_perfectly_separable(feature: pd.Series, target: pd.Series) -> Optional[bool]:
    """
    True when a multi-class target's classes occupy disjoint, ordered intervals of
    ``feature`` -- i.e. the label is exactly recoverable from this one feature by a
    set of thresholds.

    This is the 3+ class generalisation of ``AUC == 1``. It matters because the
    canonical direction label is ``sign(return)``, which has three levels
    ({-1, 0, +1}) the moment any bar closes unchanged; a binary-only separation test
    switches itself off on exactly the case this skill exists to catch.

    Returns None when the target is not categorical or a class is empty.
    """
    mask = feature.notna() & target.notna()
    feature, target = feature[mask], target[mask]
    levels = pd.unique(target)
    if not _BINARY_TARGET_LEVELS <= len(levels) <= _MAX_CATEGORICAL_TARGET_LEVELS:
        return None

    spans = []
    for level in levels:
        values = feature[target == level]
        if values.empty:
            return None
        spans.append((float(values.min()), float(values.max())))

    spans.sort()
    return all(
        spans[i][1] < spans[i + 1][0] for i in range(len(spans) - 1)
    )


def _categorical_target_levels(target: pd.Series) -> int:
    return int(target.dropna().nunique())


def _best_correlation(left: pd.Series, right: pd.Series) -> Tuple[float, str]:
    """
    Returns (correlation, method) for whichever of Pearson / Spearman has the larger
    absolute value. Spearman is required because a strictly monotone copy of the
    target (e.g. ``target ** 3``) has Spearman 1.0 but Pearson well below any
    sensible threshold.
    """
    best_value, best_method = float("nan"), "pearson"
    for method in ("pearson", "spearman"):
        value = left.corr(right, method=method)
        if pd.isna(value):
            continue
        if pd.isna(best_value) or abs(value) > abs(best_value):
            best_value, best_method = float(value), method
    return best_value, best_method


class FeatureLeakageAuditor:
    """
    Scans engineered features for target contamination, future lookahead, and
    non-causal construction.

    Args:
        correlation_threshold: |r| at which a feature/future-target association is
            reported as FUTURE_LOOKAHEAD.
        same_bar_threshold: |r| at lag 0 above which a feature is treated as a copy
            of the target rather than a predictor of it.
        separation_threshold: rank-AUC above which a binary target is treated as
            perfectly recoverable from a single feature. Targets with 3 or more
            levels use exact ordered-interval separation instead, which this
            threshold does not affect.
        min_observations: minimum overlapping non-null pairs required before a
            correlation is trusted. Below this the feature is reported as
            UNDETERMINED rather than silently omitted from the findings.
    """

    def __init__(
        self,
        correlation_threshold: float = 0.85,
        same_bar_threshold: float = 0.99,
        separation_threshold: float = 0.999,
        min_observations: int = 30,
    ) -> None:
        for name, value in (
            ("correlation_threshold", correlation_threshold),
            ("same_bar_threshold", same_bar_threshold),
            ("separation_threshold", separation_threshold),
        ):
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must lie in (0.0, 1.0], got {value!r}.")
        if min_observations < 2:
            raise ValueError(f"min_observations must be >= 2, got {min_observations!r}.")

        self.correlation_threshold = correlation_threshold
        self.same_bar_threshold = same_bar_threshold
        self.separation_threshold = separation_threshold
        self.min_observations = min_observations

    # ------------------------------------------------------------------ #
    # 1. Statistical association screen
    # ------------------------------------------------------------------ #

    def audit_dataframe(
        self,
        df: pd.DataFrame,
        target_col: str,
        feature_cols: Optional[List[str]] = None,
        max_lead_periods: int = 5,
        timestamp_col: Optional[str] = None,
    ) -> List[LeakageFinding]:
        """
        Audits feature columns against a target column at lag 0 and leads t+1..t+k.

        Row order IS the time axis: every lead is produced by ``shift()``, so an
        out-of-order frame makes every result meaningless. Pass ``timestamp_col`` so
        ordering can be verified; without it the (monotonic) index is checked and
        chronological order is assumed.

        Raises:
            KeyError: target_col or timestamp_col absent.
            ValueError: empty frame, invalid max_lead_periods, or non-monotonic
                row ordering (an unverifiable audit must not return a verdict).
        """
        if target_col not in df.columns:
            raise KeyError(f"Target column '{target_col}' not found in DataFrame.")
        if df.empty:
            raise ValueError("Cannot audit an empty DataFrame.")
        if max_lead_periods < 1:
            raise ValueError(f"max_lead_periods must be >= 1, got {max_lead_periods!r}.")

        self._verify_chronological_order(df, timestamp_col)

        target = pd.to_numeric(df[target_col], errors="coerce")
        if target.notna().sum() < self.min_observations:
            raise ValueError(
                f"Target column '{target_col}' has only {int(target.notna().sum())} numeric "
                f"observations, below min_observations={self.min_observations}."
            )
        target_levels = _categorical_target_levels(target)
        if target_levels < 2:
            # Every correlation against a constant target is NaN, so an audit would
            # report a clean result for a leaked feature set.
            raise ValueError(
                f"Target column '{target_col}' is constant. Correlation against it is "
                "undefined, so the audit would report every feature as clean."
            )

        requested = feature_cols if feature_cols is not None else [
            c for c in df.columns if c != target_col
        ]
        findings: List[LeakageFinding] = []

        for col in requested:
            if col not in df.columns:
                if feature_cols is not None:
                    raise KeyError(f"Feature column '{col}' not found in DataFrame.")
                continue
            if col == target_col:
                continue

            series = pd.to_numeric(df[col], errors="coerce")
            if series.notna().sum() == 0:
                # Non-numeric (timestamps, symbol strings, categoricals) cannot be
                # screened by correlation. Skipping is correct; silence is not.
                logger.info("Skipping non-numeric column '%s' -- correlation screen not applicable.", col)
                continue

            undetermined = self._undetermined_finding(col, series, target)
            if undetermined is not None:
                findings.append(undetermined)
                continue

            same_bar = self._screen_same_bar(col, series, target, target_levels)
            if same_bar is not None:
                findings.append(same_bar)

            lookahead = self._screen_leads(
                col, series, target, max_lead_periods, target_levels
            )
            if lookahead is not None:
                findings.append(lookahead)

        logger.info(
            "Audited %d feature column(s) against target '%s': %d finding(s).",
            len(requested), target_col, len(findings),
        )
        return findings

    def _verify_chronological_order(
        self, df: pd.DataFrame, timestamp_col: Optional[str]
    ) -> None:
        if timestamp_col is not None:
            if timestamp_col not in df.columns:
                raise KeyError(f"Timestamp column '{timestamp_col}' not found in DataFrame.")
            stamps = df[timestamp_col]
            if stamps.isna().any():
                raise ValueError(
                    f"Timestamp column '{timestamp_col}' contains null values; "
                    "row ordering cannot be verified."
                )
            if not stamps.is_monotonic_increasing:
                raise ValueError(
                    f"Rows are not sorted ascending by '{timestamp_col}'. Lead/lag analysis "
                    "shifts by row position, so an unsorted frame yields a meaningless "
                    "'clean' result. Sort before auditing."
                )
            if stamps.duplicated().any():
                logger.warning(
                    "Timestamp column '%s' contains duplicates; a one-row shift is not a "
                    "one-period lead for those rows.", timestamp_col,
                )
            return

        if not df.index.is_monotonic_increasing:
            raise ValueError(
                "DataFrame index is not monotonic increasing and no timestamp_col was "
                "supplied, so chronological order cannot be verified. Sort the frame and "
                "pass timestamp_col."
            )
        logger.warning(
            "No timestamp_col supplied: row order is ASSUMED chronological. A frame that "
            "was shuffled and then re-indexed will pass this check and audit clean."
        )

    def _undetermined_finding(
        self, col: str, series: pd.Series, target: pd.Series
    ) -> Optional[LeakageFinding]:
        overlap = int((series.notna() & target.notna()).sum())
        if overlap < self.min_observations:
            return LeakageFinding(
                feature_name=col,
                leakage_type=LeakageType.UNDETERMINED,
                max_correlation_lead=0,
                correlation_value=float("nan"),
                message=(
                    f"Feature '{col}' has only {overlap} overlapping non-null observations "
                    f"(min_observations={self.min_observations}). NOT screened -- do not read "
                    "the absence of a leakage finding for this column as clean."
                ),
                method="insufficient_data",
            )
        if series.nunique(dropna=True) < 2:
            return LeakageFinding(
                feature_name=col,
                leakage_type=LeakageType.UNDETERMINED,
                max_correlation_lead=0,
                correlation_value=float("nan"),
                message=(
                    f"Feature '{col}' is constant, so correlation is undefined. NOT screened "
                    "-- a constant column carries no signal and no leakage, but confirm it is "
                    "intentional."
                ),
                method="constant_column",
            )
        return None

    def _screen_same_bar(
        self, col: str, series: pd.Series, target: pd.Series, target_levels: int
    ) -> Optional[LeakageFinding]:
        correlation, method = _best_correlation(series, target)
        if not pd.isna(correlation) and abs(correlation) >= self.same_bar_threshold:
            return LeakageFinding(
                feature_name=col,
                leakage_type=LeakageType.SAME_BAR_CONTAMINATION,
                max_correlation_lead=0,
                correlation_value=round(correlation, 4),
                message=(
                    f"Feature '{col}' has near-identical {method} correlation "
                    f"({correlation:.4f}) with the target at t=0. Possible target contamination."
                ),
                method=method,
            )

        separation = self._separation_score(series, target, target_levels)
        if separation is not None:
            value, separation_method = separation
            return LeakageFinding(
                feature_name=col,
                leakage_type=LeakageType.SAME_BAR_CONTAMINATION,
                max_correlation_lead=0,
                correlation_value=round(value, 4),
                message=(
                    f"Feature '{col}' separates the {target_levels}-class target perfectly at "
                    f"t=0 ({separation_method} {value:.4f}). The label is exactly recoverable "
                    "from this one feature by thresholding, which a genuine predictor of a "
                    "noisy financial label cannot do."
                ),
                method=separation_method,
            )
        return None

    def _separation_score(
        self, series: pd.Series, target: pd.Series, target_levels: int
    ) -> Optional[Tuple[float, str]]:
        """
        Returns (score, method) when the target is exactly recoverable from this one
        feature by thresholding, else None.

        Binary targets use rank AUC, which also reports how close a non-separating
        feature came. Targets with 3..10 levels -- notably sign(return), which has
        three levels whenever a bar closes unchanged -- use the ordered-interval
        generalisation, since AUC is undefined for them.
        """
        if target_levels == _BINARY_TARGET_LEVELS:
            auc = _rank_auc(series, target)
            if auc is not None and max(auc, 1.0 - auc) >= self.separation_threshold:
                return float(auc), "rank_auc"
            return None

        if target_levels <= _MAX_CATEGORICAL_TARGET_LEVELS:
            if _is_perfectly_separable(series, target):
                return 1.0, "class_separation"
        return None

    def _screen_leads(
        self,
        col: str,
        series: pd.Series,
        target: pd.Series,
        max_lead_periods: int,
        target_levels: int,
    ) -> Optional[LeakageFinding]:
        """
        Reports the lead with the LARGEST absolute association, not merely the first
        one over the threshold, so ``max_correlation_lead`` means what it says.
        """
        best: Optional[Tuple[float, int, str]] = None

        for lead in range(1, max_lead_periods + 1):
            lead_target = target.shift(-lead)
            if int((series.notna() & lead_target.notna()).sum()) < self.min_observations:
                continue

            correlation, method = _best_correlation(series, lead_target)
            if not pd.isna(correlation) and abs(correlation) >= self.correlation_threshold:
                if best is None or abs(correlation) > abs(best[0]):
                    best = (correlation, lead, method)

            separation = self._separation_score(series, lead_target, target_levels)
            if separation is not None:
                value, separation_method = separation
                if best is None or max(value, 1.0 - value) > abs(best[0]):
                    best = (value, lead, separation_method)

        if best is None:
            return None

        value, lead, method = best
        descriptor = (
            f"separates the future target at t+{lead} perfectly ({method} {value:.4f})"
            if method in ("rank_auc", "class_separation")
            else f"correlates strongly with the future target at t+{lead} "
                 f"({method} r={value:.4f})"
        )
        return LeakageFinding(
            feature_name=col,
            leakage_type=LeakageType.FUTURE_LOOKAHEAD,
            max_correlation_lead=lead,
            correlation_value=round(value, 4),
            message=f"Feature '{col}' {descriptor}. Lookahead leakage suspected.",
            method=method,
        )

    # ------------------------------------------------------------------ #
    # 2. Structural causality screen
    # ------------------------------------------------------------------ #

    def audit_feature_causality(
        self,
        raw_df: pd.DataFrame,
        feature_fn: Callable[[pd.DataFrame], Union[pd.DataFrame, pd.Series]],
        cut_fractions: Sequence[float] = _DEFAULT_CUT_FRACTIONS,
        tolerance: float = 1e-9,
    ) -> List[LeakageFinding]:
        """
        Prefix-invariance (truncation) test of a feature-construction function.

        A causal pipeline computes each row from that row and earlier rows only, so
        truncating the raw input after row ``c`` must leave rows ``0..c`` of the
        output bit-identical. Any difference proves the pipeline read forward.

        This catches what correlation cannot: centred rolling windows, ``shift(-k)``,
        ``bfill``, and whole-sample normalisation (a scaler fitted on the full sample
        shifts every row when the tail is removed). Correlation misses these whenever
        the target is noisy enough to keep |r| below the threshold -- which, for
        next-period financial returns, is nearly always.

        LIMITATION: a cut only reveals a forward-looking dependency whose reach
        crosses that cut. A pipeline that looks forward only at sparse or irregular
        rows (e.g. ``bfill`` over occasional gaps) can be prefix-invariant at every
        cut tried and still be non-causal. Widen ``cut_fractions`` when the raw data
        has irregular gaps; a clean result is evidence, not proof.

        Cost is ``len(cut_fractions) + 1`` invocations of ``feature_fn``.

        Args:
            raw_df: raw inputs, sorted ascending in time.
            feature_fn: pure function mapping the raw frame to engineered features,
                preserving the input's index.
            cut_fractions: fractions of the frame to truncate at. Multiple cuts are
                used because a non-causal window may be invariant at one cut by chance.
            tolerance: relative tolerance for the numeric comparison.

        Returns:
            One UNSHIFTED_ROLLING finding per non-causal output column.

        Raises:
            ValueError: empty/non-monotonic input, invalid cut_fractions, or a
                feature_fn that does not preserve the input index.
        """
        if raw_df.empty:
            raise ValueError("Cannot audit causality on an empty DataFrame.")
        if not raw_df.index.is_monotonic_increasing:
            raise ValueError("raw_df must be sorted ascending in time before a truncation test.")
        if not cut_fractions:
            raise ValueError("cut_fractions must contain at least one fraction.")
        if any(not 0.0 < f < 1.0 for f in cut_fractions):
            raise ValueError(f"cut_fractions must lie strictly in (0.0, 1.0), got {cut_fractions!r}.")

        full = self._as_feature_frame(feature_fn(raw_df.copy()), raw_df.index, "raw_df")
        findings: List[LeakageFinding] = []
        flagged: set = set()

        for fraction in cut_fractions:
            cut = int(len(raw_df) * fraction)
            if cut < 2:
                logger.info("Skipping cut fraction %.2f: fewer than 2 rows before the cut.", fraction)
                continue

            prefix = raw_df.iloc[:cut].copy()
            truncated = self._as_feature_frame(
                feature_fn(prefix), prefix.index, f"raw_df.iloc[:{cut}]"
            )

            for col in full.columns:
                if col in flagged or col not in truncated.columns:
                    continue
                differing = self._count_differing(
                    full[col].iloc[:cut], truncated[col], tolerance
                )
                if differing == 0:
                    continue

                flagged.add(col)
                findings.append(
                    LeakageFinding(
                        feature_name=col,
                        leakage_type=LeakageType.UNSHIFTED_ROLLING,
                        max_correlation_lead=0,
                        correlation_value=float("nan"),
                        message=(
                            f"Feature '{col}' is not prefix-invariant: truncating the raw input "
                            f"after row {cut} changed {differing} of the {cut} preceding output "
                            "row(s). The construction reads forward in time (centred window, "
                            "negative shift, backfill, or whole-sample fit)."
                        ),
                        method="prefix_invariance",
                    )
                )

        missing = set(full.columns) - flagged
        logger.info(
            "Causality audit over %d cut(s): %d of %d output column(s) prefix-invariant.",
            len(cut_fractions), len(missing), len(full.columns),
        )
        return findings

    @staticmethod
    def _as_feature_frame(
        result: Union[pd.DataFrame, pd.Series], expected_index: pd.Index, label: str
    ) -> pd.DataFrame:
        if isinstance(result, pd.Series):
            result = result.to_frame(name=result.name or "feature")
        if not isinstance(result, pd.DataFrame):
            raise ValueError(
                f"feature_fn must return a DataFrame or Series; got {type(result).__name__}."
            )
        if not result.index.equals(expected_index):
            raise ValueError(
                f"feature_fn must preserve the input index; the output for {label} has a "
                "different index. Reindex to the input rather than dropping or resetting rows, "
                "otherwise the truncation test cannot align."
            )
        return result

    @staticmethod
    def _count_differing(left: pd.Series, right: pd.Series, tolerance: float) -> int:
        left_values, right_values = left.to_numpy(), right.to_numpy()
        if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
            same = np.isclose(
                left_values.astype(float), right_values.astype(float),
                rtol=tolerance, atol=tolerance, equal_nan=True,
            )
        else:
            both_null = pd.isna(left_values) & pd.isna(right_values)
            same = (left_values == right_values) | both_null
        return int((~same).sum())

    # ------------------------------------------------------------------ #
    # 3. Point-in-time join screens
    # ------------------------------------------------------------------ #

    @staticmethod
    def point_in_time_asof_merge(
        left_df: pd.DataFrame,
        right_df: pd.DataFrame,
        on_timestamp_col: str,
        by_col: Optional[str] = None,
        allow_exact_matches: bool = False,
    ) -> pd.DataFrame:
        """
        Backward as-of merge that attaches to each left row only right rows published
        STRICTLY earlier.

        ``pandas.merge_asof(direction='backward')`` defaults to
        ``allow_exact_matches=True``, i.e. "less than or equal to". This wrapper
        defaults it to False so a record stamped at exactly the decision timestamp --
        an economic release timed to the bar, a fundamental filed at the open -- is not
        treated as having been available for that decision. Set it to True only when
        the right frame's timestamps are genuinely receipt times already offset for
        dissemination latency.

        Raises:
            KeyError: on_timestamp_col or by_col absent from either frame.
        """
        for label, frame in (("left_df", left_df), ("right_df", right_df)):
            if on_timestamp_col not in frame.columns:
                raise KeyError(f"Timestamp column '{on_timestamp_col}' not found in {label}.")
            if by_col is not None and by_col not in frame.columns:
                raise KeyError(f"Group column '{by_col}' not found in {label}.")

        # merge_asof requires both keys sorted ascending.
        left_sorted = left_df.sort_values(on_timestamp_col)
        right_sorted = right_df.sort_values(on_timestamp_col)

        merged = pd.merge_asof(
            left_sorted,
            right_sorted,
            on=on_timestamp_col,
            by=by_col,
            direction="backward",
            allow_exact_matches=allow_exact_matches,
        )
        logger.info(
            "Point-in-time merge: %d left row(s), allow_exact_matches=%s.",
            len(merged), allow_exact_matches,
        )
        return merged

    @staticmethod
    def verify_asof_timing(
        df: pd.DataFrame,
        decision_time_col: str,
        publish_time_col: str,
        feature_name: str = "<as-of joined features>",
        allow_exact_matches: bool = False,
    ) -> List[LeakageFinding]:
        """
        Verifies, after a join performed by any means, that every attached record's
        publication timestamp precedes its decision timestamp.

        Use this on data joined upstream (a vendor extract, a SQL view, someone
        else's pipeline) where `point_in_time_asof_merge` was not the join mechanism
        and its guarantee therefore does not apply.

        Unmatched rows (null publication timestamp) are not violations; they are
        logged, because a silently unmatched join is its own defect.
        """
        for col in (decision_time_col, publish_time_col):
            if col not in df.columns:
                raise KeyError(f"Column '{col}' not found in DataFrame.")

        decision = pd.to_datetime(df[decision_time_col], errors="coerce")
        published = pd.to_datetime(df[publish_time_col], errors="coerce")

        # Comparing a tz-aware column against a naive one raises an opaque TypeError,
        # and silently mixing the two is itself a classic source of apparent lookahead.
        if (decision.dt.tz is None) != (published.dt.tz is None):
            raise ValueError(
                f"'{decision_time_col}' and '{publish_time_col}' mix timezone-aware and "
                "timezone-naive timestamps, so they cannot be compared. Localise both to "
                "the same timezone before verifying as-of timing."
            )

        unmatched = int(published.isna().sum())
        if unmatched:
            logger.warning(
                "%d row(s) have no publication timestamp and were not timing-checked.", unmatched,
            )

        violating = published > decision if allow_exact_matches else published >= decision
        violating = violating.fillna(False)
        count = int(violating.sum())
        if count == 0:
            return []

        first = df.index[violating][0]
        relation = "after" if allow_exact_matches else "at or after"
        return [
            LeakageFinding(
                feature_name=feature_name,
                leakage_type=LeakageType.ASOF_TIMING_VIOLATION,
                max_correlation_lead=0,
                correlation_value=float("nan"),
                message=(
                    f"{count} joined row(s) carry data published {relation} their decision "
                    f"timestamp (first at index {first!r}). These features were not knowable "
                    "at prediction time."
                ),
                method="timestamp_comparison",
            )
        ]

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #

    def run_intentional_leakage_calibration(
        self,
        df: pd.DataFrame,
        target_col: str,
        max_lead_periods: int = 5,
        timestamp_col: Optional[str] = None,
    ) -> Tuple[float, List[LeakageFinding]]:
        """
        Injects a known leak (the target shifted one period forward) and confirms the
        association screen catches it, calibrating detector sensitivity on THIS
        dataset's noise level before its clean verdicts are trusted.

        Returns:
            ``(detected_strength, findings)``. ``detected_strength`` is the largest
            absolute association the auditor actually reported for the injected
            feature, and is **0.0 when the known leak was missed** -- the calibration
            failing is the whole point of running it, so it must be observable in the
            return value and not just in the log.
        """
        leaked_col = "__leaked_feature__"
        if leaked_col in df.columns:
            raise ValueError(f"Column '{leaked_col}' already exists; cannot inject calibration leak.")

        temp_df = df.copy()
        temp_df[leaked_col] = pd.to_numeric(temp_df[target_col], errors="coerce").shift(-1)

        findings = self.audit_dataframe(
            temp_df,
            target_col=target_col,
            feature_cols=[leaked_col],
            max_lead_periods=max_lead_periods,
            timestamp_col=timestamp_col,
        )

        detected = [
            abs(f.correlation_value)
            for f in findings
            if f.leakage_type in (LeakageType.FUTURE_LOOKAHEAD, LeakageType.SAME_BAR_CONTAMINATION)
            and np.isfinite(f.correlation_value)
        ]
        if not detected:
            logger.error(
                "Leakage calibration FAILED: the injected known leak was NOT detected. The "
                "auditor is not sensitive enough for this dataset -- its clean verdicts on the "
                "real features carry no assurance."
            )
            return 0.0, findings

        strength = max(detected)
        logger.info("Leakage calibration passed: injected leak detected at strength %.4f.", strength)
        return strength, findings


def verify_shift_direction(
    series: pd.Series, shift_periods: int, expected_lag: bool = True
) -> pd.Series:
    """
    Applies ``Series.shift`` after asserting the sign matches intent.

    pandas' convention: ``shift(+k)`` moves values forward in position, so row t
    receives the value from row t-k -- a backward-looking LAG. ``shift(-k)`` pulls a
    future value back to row t -- a LEAD, legitimate only when constructing labels.

    Args:
        expected_lag: True when building a feature (requires positive periods),
            False when building a label/target (requires negative periods).

    Raises:
        ValueError: the sign of shift_periods contradicts expected_lag, or is zero.
    """
    if shift_periods == 0:
        raise ValueError("shift_periods of 0 is a no-op; state the intended lag or lead explicitly.")
    if expected_lag and shift_periods < 0:
        raise ValueError(
            "Expected a backward-looking (lag) shift but got shift_periods < 0 -- this likely "
            "leaks future data."
        )
    if not expected_lag and shift_periods > 0:
        raise ValueError(
            "Expected a forward-looking (lead) shift for label construction but got "
            "shift_periods > 0, which produces a lag."
        )
    return series.shift(shift_periods)
