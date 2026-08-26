"""
lookahead-bias-elimination: backtest execution-timing audit engine.

This module audits the *timing* of a backtest: when a decision was taken, which
bar it was filled on, and whether any value fed into it was knowable at that
moment. It deliberately does not screen features for statistical target
contamination -- that is `feature-engineering-without-leakage`.

Four independent screens, none of which subsumes another:

1. Execution-timing screen (`audit_backtest_timing`) -- flags active signals whose
   recorded fill price matches the *same bar's* Close, High, or Low, plus signals
   taken before an indicator was warmed. Operates on a delivered backtest frame.
2. Signal alignment (`align_signal_execution`) -- constructs the causal execution
   column: a signal raised on bar T is executed `execution_lag` bars later at that
   bar's Open, grouped per instrument so a stacked panel cannot leak one symbol's
   signal into another's first bar.
3. Detector calibration (`run_timing_calibration`) -- injects a known same-bar fill
   into every active signal and reports the fraction the auditor actually caught.
   A clean audit means nothing until this returns 1.0 on the same frame.
4. Timestamp causality (`audit_feature_timestamps`) and delivered-column causality
   (`audit_indicator_causality`) -- the first compares declared availability
   timestamps against the decision timestamp; the second looks for the structural
   signature of a centred rolling window or a negative shift in an already-computed
   indicator column.

Every screen is a *candidate filter*. A report with no findings means no screen
fired, not that the backtest is causal. See `references/standards.md`.
"""
from dataclasses import dataclass
from enum import Enum
import logging
from typing import List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Fill prices and bar prices originate from the same source, so a genuine same-bar
# fill is equal to the bar price up to float representation only. The tolerance is
# *relative* because an absolute one cannot serve both a 95,000-quoted crypto pair
# and a 0.00012-quoted FX cross with the same constant.
_DEFAULT_REL_TOLERANCE = 1e-9

# Frame-level findings (a column was absent, a whole class of rows was unauditable)
# have no single originating row. -1 distinguishes them from row 0.
FRAME_LEVEL_ROW_INDEX = -1

# Window aggregations `audit_indicator_causality` tries when fingerprinting how a
# delivered indicator column was constructed. Covers SMA, Bollinger mid/width, and
# the rolling extrema that Donchian/ATR-style indicators are built from.
_DEFAULT_AGGREGATIONS = ("mean", "sum", "max", "min", "median", "std")

# A window fingerprint claimed on a handful of rows is coincidence, not evidence.
_MIN_FINGERPRINT_OBSERVATIONS = 20


class LookaheadViolationType(str, Enum):
    SAME_BAR_FILL_CONTAMINATION = "SAME_BAR_FILL_CONTAMINATION"
    INDICATOR_UNWARMED_EXECUTION = "INDICATOR_UNWARMED_EXECUTION"
    CENTERED_ROLLING_WINDOW = "CENTERED_ROLLING_WINDOW"
    TIMESTAMP_CAUSALITY_BREACH = "TIMESTAMP_CAUSALITY_BREACH"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class LookaheadAuditFinding:
    """
    One audit result.

    Attributes:
        violation_type: which screen fired.
        row_index: **positional** offset of the offending row (0-based, as
            counted down the frame), or `FRAME_LEVEL_ROW_INDEX` (-1) when the
            finding concerns the frame as a whole. This is deliberately not the
            index *label*: a backtest frame is normally datetime-indexed, and a
            label cannot be compared against a warm-up bar count.
        timestamp: string form of the row's timestamp column value, or of its
            index label when no timestamp column was supplied.
        details: human-readable explanation, naming the columns involved.
    """

    violation_type: LookaheadViolationType
    row_index: int
    timestamp: str
    details: str


def _require_columns(df: pd.DataFrame, columns: Sequence[str], purpose: str) -> None:
    """
    Raises when a column the audit cannot proceed without is absent.

    An auditor that returns an empty finding list because it could not find its
    inputs is indistinguishable from one that found nothing wrong, and the caller
    reads the second meaning. Failing loudly is the only safe behaviour.
    """
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(
            f"Cannot {purpose}: column(s) {missing} absent from the frame "
            f"(available: {list(df.columns)}). Refusing to report a clean audit "
            f"for a check that did not run."
        )


def _row_labels(df: pd.DataFrame, timestamp_col: Optional[str]) -> List[str]:
    """
    String timestamp per row, from `timestamp_col` when present, else the index.

    Built element-wise rather than with `Index.astype(str)`, which raises on a
    MultiIndex -- an ordinary shape for a (symbol, date) backtest frame.
    """
    if timestamp_col and timestamp_col in df.columns:
        return [str(value) for value in df[timestamp_col]]
    return [str(label) for label in df.index]


def _numeric_column(df: pd.DataFrame, column: str) -> np.ndarray:
    """
    Column as float64 with unparseable entries coerced to NaN.

    Coercion rather than a raise, because NaN entries are then reported as an
    explicit UNDETERMINED finding instead of silently comparing as unequal (which
    is what `abs(nan - x) < tol` does -- it returns False and passes the row).
    """
    return pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)


def _matches(left: np.ndarray, right: np.ndarray, rel_tolerance: float) -> np.ndarray:
    """Elementwise closeness with a relative tolerance; NaN never matches."""
    return np.isclose(left, right, rtol=rel_tolerance, atol=0.0, equal_nan=False)


class LookaheadBiasAuditor:
    """
    Audits a backtest frame for execution-timing lookahead.

    The rule enforced throughout: a signal raised from bar T's information may
    first be executed on bar T+1 (or later), at that bar's Open. Both backtrader
    and backtesting.py implement this as their default and expose same-bar-close
    execution only as an explicit opt-in -- see `references/standards.md`.

    Args:
        warmup_periods: number of leading bars during which an indicator is
            assumed not yet formed. Signals inside this span are reported as
            INDICATOR_UNWARMED_EXECUTION. Pass 0 to disable the positional check
            and rely on `indicator_cols` NaN detection instead.

    Raises:
        ValueError: if `warmup_periods` is negative or not an integer.
    """

    def __init__(self, warmup_periods: int = 30) -> None:
        if isinstance(warmup_periods, bool) or not isinstance(warmup_periods, (int, np.integer)):
            raise ValueError(f"warmup_periods must be an int, got {warmup_periods!r}.")
        if warmup_periods < 0:
            raise ValueError(f"warmup_periods must be >= 0, got {warmup_periods!r}.")
        self.warmup_periods = int(warmup_periods)

    def audit_backtest_timing(
        self,
        df: pd.DataFrame,
        signal_col: str = "signal",
        close_col: str = "close",
        fill_price_col: str = "fill_price",
        timestamp_col: Optional[str] = None,
        high_col: str = "high",
        low_col: str = "low",
        indicator_cols: Optional[Sequence[str]] = None,
        rel_tolerance: float = _DEFAULT_REL_TOLERANCE,
    ) -> List[LookaheadAuditFinding]:
        """
        Flags active signals filled at the same bar's Close, High, or Low, and
        signals taken before their indicators were warmed.

        Rows are addressed **positionally**, so the audit behaves identically on a
        datetime-indexed frame, a sliced frame whose integer index no longer starts
        at zero, and a default RangeIndex.

        Args:
            df: the backtest frame, one row per bar, in chronological order.
            signal_col: column holding the raised signal. Rows where it is 0 or
                NaN are not audited; NaN rows are reported once, in aggregate,
                because a NaN signal is neither clearly active nor clearly flat.
            close_col, high_col, low_col: same-bar reference prices. Whichever are
                present are compared against the fill; whichever are absent are
                reported as an UNDETERMINED finding so the gap is visible in the
                report rather than invisible.
            fill_price_col: the price the backtest recorded as the fill.
            timestamp_col: optional column used for the `timestamp` field of each
                finding. Falls back to the index label.
            indicator_cols: columns whose NaN span defines the real warm-up. This
                is stricter than `warmup_periods` and does not require guessing a
                bar count: `rolling(n)` yields NaN for the first n-1 bars, so a
                signal on a NaN indicator is a signal on nothing.
            rel_tolerance: relative tolerance for "the fill equals the bar price".

        Returns:
            Findings in frame order, frame-level ones first. An empty list means no
            screen fired -- see the module docstring on what that does not prove.

        Raises:
            ValueError: if `signal_col` or `fill_price_col` is absent, if none of
                the three reference price columns is present, if `rel_tolerance`
                is not positive, or if a named `indicator_cols` entry is absent.
        """
        if rel_tolerance <= 0:
            raise ValueError(f"rel_tolerance must be > 0, got {rel_tolerance!r}.")

        _require_columns(df, [signal_col, fill_price_col], "audit backtest timing")
        indicator_cols = list(indicator_cols or [])
        if indicator_cols:
            _require_columns(df, indicator_cols, "audit indicator warm-up")

        candidate_price_cols = [column for column in (close_col, high_col, low_col) if column]
        reference_cols = [column for column in candidate_price_cols if column in df.columns]
        if not reference_cols:
            raise ValueError(
                f"None of the reference price columns {candidate_price_cols} is present, "
                f"so no same-bar fill comparison is possible. Refusing to report a clean audit."
            )

        findings: List[LookaheadAuditFinding] = []
        absent_cols = [column for column in candidate_price_cols if column not in df.columns]
        if absent_cols:
            findings.append(
                LookaheadAuditFinding(
                    violation_type=LookaheadViolationType.UNDETERMINED,
                    row_index=FRAME_LEVEL_ROW_INDEX,
                    timestamp="",
                    details=(
                        f"Same-bar fill comparison could not cover absent column(s) {absent_cols}; "
                        f"a clean result here covers {reference_cols} only."
                    ),
                )
            )

        signal = df[signal_col]
        is_active = (signal.notna() & (signal != 0)).to_numpy(dtype=bool)
        nan_signal_count = int(signal.isna().sum())
        if nan_signal_count:
            findings.append(
                LookaheadAuditFinding(
                    violation_type=LookaheadViolationType.UNDETERMINED,
                    row_index=FRAME_LEVEL_ROW_INDEX,
                    timestamp="",
                    details=(
                        f"{nan_signal_count} row(s) have a NaN '{signal_col}' and were treated as "
                        f"inactive. A NaN signal is not evidence of a flat position -- resolve it "
                        f"upstream rather than relying on this audit."
                    ),
                )
            )

        labels = _row_labels(df, timestamp_col)
        fill_prices = _numeric_column(df, fill_price_col)
        reference_arrays = {column: _numeric_column(df, column) for column in reference_cols}
        match_masks = {
            column: _matches(fill_prices, values, rel_tolerance)
            for column, values in reference_arrays.items()
        }
        indicator_values = {column: _numeric_column(df, column) for column in indicator_cols}

        unpriced_fill_count = 0
        unpriced_reference_count = 0
        for position in np.flatnonzero(is_active):
            position = int(position)
            label = labels[position]
            fill_price = fill_prices[position]

            if np.isnan(fill_price):
                unpriced_fill_count += 1
            else:
                if all(np.isnan(values[position]) for values in reference_arrays.values()):
                    unpriced_reference_count += 1
                matched = [column for column, mask in match_masks.items() if mask[position]]
                if matched:
                    findings.append(
                        LookaheadAuditFinding(
                            violation_type=LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION,
                            row_index=position,
                            timestamp=label,
                            details=(
                                f"Signal at position {position} ({label}) filled at "
                                f"{fill_price} == same-bar {', '.join(sorted(matched))}. "
                                f"Bar T's Close/High/Low are not knowable when the decision is "
                                f"taken; execute at bar T+1's Open instead."
                            ),
                        )
                    )

            if position < self.warmup_periods:
                findings.append(
                    LookaheadAuditFinding(
                        violation_type=LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION,
                        row_index=position,
                        timestamp=label,
                        details=(
                            f"Signal at position {position} ({label}) precedes the declared "
                            f"warm-up of {self.warmup_periods} bars."
                        ),
                    )
                )

            unwarmed = [
                column
                for column, values in indicator_values.items()
                if np.isnan(values[position])
            ]
            if unwarmed:
                findings.append(
                    LookaheadAuditFinding(
                        violation_type=LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION,
                        row_index=position,
                        timestamp=label,
                        details=(
                            f"Signal at position {position} ({label}) taken while indicator(s) "
                            f"{sorted(unwarmed)} are NaN, i.e. not yet formed."
                        ),
                    )
                )

        if unpriced_fill_count:
            findings.append(
                LookaheadAuditFinding(
                    violation_type=LookaheadViolationType.UNDETERMINED,
                    row_index=FRAME_LEVEL_ROW_INDEX,
                    timestamp="",
                    details=(
                        f"{unpriced_fill_count} active signal(s) have a non-numeric or NaN "
                        f"'{fill_price_col}' and could not be compared against any bar price."
                    ),
                )
            )
        if unpriced_reference_count:
            findings.append(
                LookaheadAuditFinding(
                    violation_type=LookaheadViolationType.UNDETERMINED,
                    row_index=FRAME_LEVEL_ROW_INDEX,
                    timestamp="",
                    details=(
                        f"{unpriced_reference_count} active signal(s) have a non-numeric or NaN "
                        f"value in every one of {reference_cols}, so their fill matched nothing "
                        f"for want of a comparison, not for want of a violation."
                    ),
                )
            )

        logger.debug(
            "audit_backtest_timing: %d finding(s) over %d active signal(s).",
            len(findings),
            int(is_active.sum()),
        )
        return findings

    @staticmethod
    def align_signal_execution(
        df: pd.DataFrame,
        signal_col: str = "signal",
        open_col: str = "open",
        executed_signal_col: str = "executed_signal",
        fill_price_col: str = "fill_price",
        execution_lag: int = 1,
        symbol_col: Optional[str] = None,
        timestamp_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Builds the causal execution column: a signal raised on bar T executes
        `execution_lag` bars later, at that bar's Open.

        Args:
            df: the backtest frame.
            signal_col: raised-signal column. Its dtype is preserved -- a boolean
                signal comes back boolean, an integer signal integer -- because
                `shift` alone upcasts and would silently change downstream
                comparisons.
            open_col: execution price column. If absent, `fill_price_col` is not
                written and a warning is logged.
            executed_signal_col: name of the column written with the shifted signal.
            fill_price_col: name of the column written with the execution price.
                It is populated **only on bars that actually execute**; every other
                bar is NaN, so a later same-bar audit cannot match a fill price on
                a bar where nothing was filled.
            execution_lag: bars between the decision and the fill. 1 is the
                convention both backtrader and backtesting.py default to. Raise it
                to model a decision that cannot reach the venue within one bar.
            symbol_col: instrument column for a stacked (long-format) panel. Without
                it the shift runs down the whole frame, so the last signal of one
                symbol becomes the first executed signal of the next.
            timestamp_col: when supplied, chronological order is verified (per
                symbol) before shifting, since the shift is purely positional.

        Returns:
            A copy of `df` with `executed_signal_col` and, when `open_col` exists,
            `fill_price_col`.

        Raises:
            ValueError: if `execution_lag` < 1 (0 would reinstate the same-bar
                execution this function exists to prevent, and a negative lag
                would execute on future information), if `signal_col` or a named
                `symbol_col`/`timestamp_col` is absent, or if the frame is not in
                ascending timestamp order.
        """
        if isinstance(execution_lag, bool) or not isinstance(execution_lag, (int, np.integer)):
            raise ValueError(f"execution_lag must be an int, got {execution_lag!r}.")
        if execution_lag < 1:
            raise ValueError(
                f"execution_lag must be >= 1, got {execution_lag}. A lag of 0 executes on the "
                f"same bar that produced the signal, which is the defect this function prevents."
            )

        _require_columns(df, [signal_col], "align signal execution")
        if symbol_col:
            _require_columns(df, [symbol_col], "group alignment by instrument")
        if timestamp_col:
            _require_columns(df, [timestamp_col], "verify chronological order")

        out = df.copy()

        if timestamp_col:
            groups = out.groupby(symbol_col, sort=False)[timestamp_col] if symbol_col else None
            unordered = (
                [key for key, values in groups if not values.is_monotonic_increasing]
                if groups is not None
                else ([] if out[timestamp_col].is_monotonic_increasing else ["<frame>"])
            )
            if unordered:
                raise ValueError(
                    f"'{timestamp_col}' is not ascending for {unordered}. The execution shift is "
                    f"positional, so an out-of-order frame would align signals to the wrong bars."
                )
        elif symbol_col:
            logger.warning(
                "align_signal_execution: symbol_col=%r given without timestamp_col; chronological "
                "order within each instrument is assumed, not verified.",
                symbol_col,
            )

        original = out[signal_col]
        shifted = (
            out.groupby(symbol_col, sort=False)[signal_col].shift(execution_lag)
            if symbol_col
            else original.shift(execution_lag)
        )

        if pd.api.types.is_bool_dtype(original):
            executed = shifted.fillna(False).astype(bool)
        elif pd.api.types.is_integer_dtype(original):
            executed = shifted.fillna(0).astype(original.dtype)
        else:
            executed = shifted.fillna(0)
        out[executed_signal_col] = executed

        if open_col in out.columns:
            if fill_price_col in df.columns:
                logger.warning(
                    "align_signal_execution: overwriting existing '%s' column with %s.",
                    fill_price_col,
                    open_col,
                )
            out[fill_price_col] = out[open_col].where(executed != 0)
        else:
            logger.warning(
                "align_signal_execution: open_col=%r absent; '%s' was not written and the "
                "returned frame carries no execution price.",
                open_col,
                fill_price_col,
            )

        return out

    def run_leak_calibration(
        self, df: pd.DataFrame, target_col: str, feature_name: str = "leaked_feature"
    ) -> pd.DataFrame:
        """
        Adds a column holding the target shifted one bar back, i.e. a known
        one-bar-forward leak.

        This is the *strategy-level* calibration: run the backtest on the returned
        frame and confirm performance inflates dramatically. If it does not, the
        backtest is not sensitive enough for its clean results to mean anything.
        To calibrate the *auditor* rather than the backtest, use
        `run_timing_calibration`.

        Raises:
            ValueError: if `target_col` is absent.
        """
        _require_columns(df, [target_col], "inject a calibration leak")
        if feature_name in df.columns:
            logger.warning("run_leak_calibration: overwriting existing '%s' column.", feature_name)
        out = df.copy()
        out[feature_name] = out[target_col].shift(-1)
        return out

    def run_timing_calibration(
        self,
        df: pd.DataFrame,
        signal_col: str = "signal",
        close_col: str = "close",
        fill_price_col: str = "fill_price",
        **audit_kwargs: object,
    ) -> float:
        """
        Measures whether `audit_backtest_timing` can actually see a same-bar fill
        on *this* frame.

        Overwrites the fill price of every active signal with that bar's Close --
        a deliberate, known violation on every one of them -- then reports the
        fraction the auditor flagged.

        Returns:
            Detected fraction in [0.0, 1.0]. **1.0 is the only acceptable result.**
            Anything lower means the auditor is partly blind here (typically NaN or
            non-numeric prices), and its clean verdict on the real frame carries
            exactly that much less assurance.

        Raises:
            ValueError: if `close_col` is absent or the frame has no active signal
                to contaminate, since neither permits a calibration.
        """
        _require_columns(df, [signal_col, close_col], "calibrate the timing auditor")

        probe = df.copy()
        signal = probe[signal_col]
        is_active = (signal.notna() & (signal != 0)).to_numpy(dtype=bool)
        injected = int(is_active.sum())
        if injected == 0:
            raise ValueError(
                f"No active '{signal_col}' rows to contaminate; calibration would be vacuous."
            )

        probe[fill_price_col] = probe[close_col].where(pd.Series(is_active, index=probe.index))
        findings = self.audit_backtest_timing(
            probe,
            signal_col=signal_col,
            close_col=close_col,
            fill_price_col=fill_price_col,
            **audit_kwargs,  # type: ignore[arg-type]
        )
        detected = len(
            {
                finding.row_index
                for finding in findings
                if finding.violation_type == LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION
            }
        )
        ratio = detected / injected
        if ratio < 1.0:
            logger.warning(
                "run_timing_calibration: auditor detected %d of %d injected same-bar fills (%.2f). "
                "Clean audits on this frame are not trustworthy.",
                detected,
                injected,
                ratio,
            )
        return ratio

    def audit_indicator_causality(
        self,
        df: pd.DataFrame,
        price_col: str,
        indicator_col: str,
        max_window: int = 60,
        max_lead: int = 5,
        aggregations: Sequence[str] = _DEFAULT_AGGREGATIONS,
        rel_tolerance: float = _DEFAULT_REL_TOLERANCE,
    ) -> List[LookaheadAuditFinding]:
        """
        Fingerprints how an already-computed indicator column was constructed, and
        reports it when the fit is a forward-looking one.

        Two structural signatures are tested against `price_col`:

        - **Centred window** -- `rolling(w, center=True).<agg>()` reproduces the
          indicator exactly while the trailing `rolling(w).<agg>()` does not.
          pandas documents `center` as defaulting to `False`, so a centred window is
          always a deliberate argument, and it averages `(w-1)/2` bars of the future
          into every value. Reported as CENTERED_ROLLING_WINDOW.
        - **Negative shift** -- the indicator equals `price_col.shift(-k)` for some
          `k` in 1..`max_lead`: the column literally holds a future bar's price.
          Reported as TIMESTAMP_CAUSALITY_BREACH.

        A trailing match is *not* reported: it is the causal construction. Note
        separately that pandas rolling windows are right-closed by default
        (`closed=None` behaves as `'right'`), so a trailing window includes bar T's
        own value -- legitimate for a decision taken after bar T closes, same-bar
        lookahead for one taken during it.

        Args:
            max_window: largest window tried. Cost is O(max_window x len(aggregations))
                rolling passes over the frame; lower it on very long series.
            max_lead: largest forward shift tried.

        Returns:
            Frame-level findings (`row_index == FRAME_LEVEL_ROW_INDEX`). **An empty
            list is not evidence of causality**: this screen recognises only the two
            signatures above, applied to a single price column, and any indicator
            built some other way fits neither. Use it to catch a specific, common
            mistake, not to certify a column.

        Raises:
            ValueError: if either column is absent, or `max_window` < 2, or
                `max_lead` < 1.
        """
        _require_columns(df, [price_col, indicator_col], "audit indicator causality")
        if max_window < 2:
            raise ValueError(f"max_window must be >= 2, got {max_window!r}.")
        if max_lead < 1:
            raise ValueError(f"max_lead must be >= 1, got {max_lead!r}.")

        price = pd.to_numeric(df[price_col], errors="coerce")
        indicator = pd.to_numeric(df[indicator_col], errors="coerce").to_numpy(dtype=float)
        findings: List[LookaheadAuditFinding] = []

        def fits(candidate: pd.Series) -> bool:
            values = candidate.to_numpy(dtype=float)
            comparable = ~np.isnan(values) & ~np.isnan(indicator)
            if int(comparable.sum()) < _MIN_FINGERPRINT_OBSERVATIONS:
                return False
            return bool(
                _matches(values[comparable], indicator[comparable], rel_tolerance).all()
            )

        for window in range(2, max_window + 1):
            trailing = price.rolling(window)
            centered = price.rolling(window, center=True)
            for aggregation in aggregations:
                if fits(getattr(centered, aggregation)()) and not fits(
                    getattr(trailing, aggregation)()
                ):
                    findings.append(
                        LookaheadAuditFinding(
                            violation_type=LookaheadViolationType.CENTERED_ROLLING_WINDOW,
                            row_index=FRAME_LEVEL_ROW_INDEX,
                            timestamp="",
                            details=(
                                f"'{indicator_col}' matches "
                                f"{price_col}.rolling({window}, center=True).{aggregation}() but not "
                                f"the trailing equivalent, so each value averages about "
                                f"{(window - 1) // 2} bar(s) of the future."
                            ),
                        )
                    )
                    return findings

        # A degenerate price series -- constant, or one the indicator simply copies --
        # matches its own shifts trivially. Reporting that as a forward leak would be a
        # false positive, so the lead search is skipped when the unshifted price already
        # fits.
        if fits(price):
            logger.debug(
                "audit_indicator_causality: '%s' equals '%s' unshifted; skipping the lead search "
                "because a degenerate series matches every shift.",
                indicator_col,
                price_col,
            )
            return findings

        for lead in range(1, max_lead + 1):
            if fits(price.shift(-lead)):
                findings.append(
                    LookaheadAuditFinding(
                        violation_type=LookaheadViolationType.TIMESTAMP_CAUSALITY_BREACH,
                        row_index=FRAME_LEVEL_ROW_INDEX,
                        timestamp="",
                        details=(
                            f"'{indicator_col}' equals {price_col}.shift(-{lead}): the column holds "
                            f"bar T+{lead}'s price on bar T."
                        ),
                    )
                )
                return findings

        logger.debug(
            "audit_indicator_causality: no forward-looking signature matched '%s'; "
            "this is not proof of causality.",
            indicator_col,
        )
        return findings


def _late_features(
    feature_timestamps: Mapping[str, object],
    decision_ts: object,
    allow_exact_matches: bool,
) -> List[Tuple[str, object]]:
    """
    (name, timestamp) for every feature not observable at `decision_ts`.

    With `allow_exact_matches` False, a timestamp equal to the decision timestamp
    counts as a breach; with it True, only strictly later timestamps do.
    """
    late: List[Tuple[str, object]] = []
    for name, timestamp in feature_timestamps.items():
        try:
            is_late = timestamp > decision_ts or (
                not allow_exact_matches and timestamp == decision_ts
            )
        except TypeError as exc:
            raise ValueError(
                f"Feature {name!r} has timestamp {timestamp!r}, which is not comparable with "
                f"decision_ts {decision_ts!r} ({exc}). Mixing tz-aware and tz-naive timestamps, "
                f"or strings with datetimes, makes the causality check meaningless."
            ) from exc
        if is_late:
            late.append((name, timestamp))
    return late


def audit_feature_timestamps(
    feature_timestamps: Mapping[str, object],
    decision_ts: object,
    allow_exact_matches: bool = False,
) -> List[LookaheadAuditFinding]:
    """
    Reports every feature whose declared availability timestamp is later than the
    decision timestamp.

    Args:
        feature_timestamps: feature name -> the moment that feature's value became
            *observable*. This is the publication/receipt time, not the period the
            value describes: a quarter-end fundamental is observable on its filing
            date, not on the quarter end.
        decision_ts: the moment the trading decision is taken.
        allow_exact_matches: whether a feature stamped at exactly `decision_ts` is
            legitimate. Defaults to **False**, matching
            `feature-engineering-without-leakage`'s as-of merge: a bar stamped at
            its own close time is not observable to a decision taken at that
            instant. Set it True only when the timestamps are already receipt times
            that include dissemination latency.

    Returns:
        TIMESTAMP_CAUSALITY_BREACH findings, one per offending feature, ordered by
        the mapping's iteration order. `row_index` is FRAME_LEVEL_ROW_INDEX since
        these findings concern features, not rows.

    Raises:
        ValueError: if any timestamp is not comparable with `decision_ts` -- most
            often a tz-aware value compared against a tz-naive one, which would
            otherwise surface as an opaque TypeError.
    """
    relation = "after" if allow_exact_matches else "at or after"
    return [
        LookaheadAuditFinding(
            violation_type=LookaheadViolationType.TIMESTAMP_CAUSALITY_BREACH,
            row_index=FRAME_LEVEL_ROW_INDEX,
            timestamp=str(timestamp),
            details=(
                f"Feature {name!r} becomes observable at {timestamp}, which is {relation} "
                f"the decision timestamp {decision_ts}."
            ),
        )
        for name, timestamp in _late_features(
            feature_timestamps, decision_ts, allow_exact_matches
        )
    ]


def inject_forward_leak(
    df: pd.DataFrame, target_col: str, feature_name: str = "leaked_feature"
) -> pd.DataFrame:
    """Module-level alias of `LookaheadBiasAuditor.run_leak_calibration`."""
    return LookaheadBiasAuditor().run_leak_calibration(df, target_col, feature_name)


def check_feature_timestamps(
    feature_timestamps: Mapping[str, object],
    decision_ts: object,
    allow_exact_matches: bool = False,
) -> List[str]:
    """
    Names of the features that are not observable at `decision_ts`.

    Thin wrapper over `audit_feature_timestamps` for callers that want names
    rather than findings. Note that `allow_exact_matches` defaults to False, so a
    feature stamped at exactly the decision timestamp is reported.
    """
    return [
        name
        for name, _ in _late_features(feature_timestamps, decision_ts, allow_exact_matches)
    ]
