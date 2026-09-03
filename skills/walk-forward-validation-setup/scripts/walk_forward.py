"""
walk-forward-validation-setup: chronological time-series fold generation with an explicit
purge/embargo gap, expanding vs rolling training windows, and per-fold plus cross-fold
out-of-sample performance aggregation.

Purging and embargoing are defined in Marcos Lopez de Prado, *Advances in Financial Machine
Learning* (Wiley, 2018), ch. 7 "Cross-Validation in Finance", section 7.4.1 (purging the
training set) and section 7.4.2 (embargo).

Sign and annualization conventions follow `backtest-reporting-standardized-tearsheet`:
Sharpe is (arithmetic annualized excess return) / (annualized sample volatility), and
`max_drawdown_pct` is a NON-POSITIVE fraction (-0.05 is a 5% drawdown).
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Arbitrary starting point only. It is not a safe default: the gap that actually prevents
# leakage is max(feature lookback, label horizon), which this module cannot infer. Pass
# max_feature_lookback / label_horizon to have that checked rather than assumed.
DEFAULT_EMBARGO = 20

# A sample standard deviation, and therefore a Sharpe ratio, does not exist below this.
_MIN_SHARPE_OBS = 2


class WalkForwardError(ValueError):
    """Raised when a split configuration or an evaluation input is invalid."""


class SplitMode(str, Enum):
    EXPANDING = "EXPANDING"  # Training window anchored at row 0 and grows each fold
    ROLLING = "ROLLING"      # Training window is fixed length and slides forward


@dataclass
class WalkForwardSplit:
    """Half-open row-index bounds for one fold: [start, end).

    `train_indices`, `embargo_indices` and `test_indices` are contiguous and ordered:
    train_end == embargo_start, embargo_end == test_start.
    """

    fold_index: int
    train_indices: Tuple[int, int]
    test_indices: Tuple[int, int]
    embargo_indices: Tuple[int, int]


@dataclass
class FoldMetrics:
    """Out-of-sample metrics for one fold.

    `sharpe_ratio`, `max_drawdown_pct` and `win_rate` are `None` unless a `returns_fn` was
    supplied to `evaluate_walk_forward`; they are derived from realised strategy returns and
    cannot be inferred from classification accuracy. `max_drawdown_pct` is non-positive.
    """

    fold_index: int
    train_period: str
    test_period: str
    sample_count: int
    accuracy: float
    sharpe_ratio: Optional[float]
    max_drawdown_pct: Optional[float]
    win_rate: Optional[float]


@dataclass
class WalkForwardAggregate:
    """Cross-fold summary. The dispersion and the worst fold are the point of it.

    A strategy is judged on the whole distribution across folds, not on the best or the most
    recent one, so `min_accuracy` / `min_sharpe` / `worst_max_drawdown_pct` are reported
    alongside the means.
    """

    fold_count: int
    mean_accuracy: float
    std_accuracy: float
    min_accuracy: float
    max_accuracy: float
    folds_with_returns: int
    mean_sharpe: Optional[float]
    min_sharpe: Optional[float]
    worst_max_drawdown_pct: Optional[float]


def _require_int(value: Any, name: str, minimum: int) -> int:
    """Validates an integer bound. `bool` is rejected: True would silently mean 1."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise WalkForwardError(f"{name} must be an int, got {type(value).__name__}.")
    value = int(value)
    if value < minimum:
        raise WalkForwardError(f"{name} must be >= {minimum}, got {value}.")
    return value


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    """Ratio, or None when the denominator is zero. Never clamps to an epsilon."""
    if denominator == 0.0:
        return None
    return float(numerator / denominator)


def _validated_returns(
    returns: Union[np.ndarray, Sequence[float]], expected_len: int
) -> np.ndarray:
    """Coerces per-period simple returns to a clean 1-D float array.

    NaN is the dangerous case: it propagates through mean, std and min while the count-based
    win rate stays plausible, producing a fold whose metrics are half valid.
    """
    try:
        r = np.asarray(returns, dtype=float)
    except (TypeError, ValueError) as exc:
        raise WalkForwardError(f"returns_fn must return a float array: {exc}") from exc

    if r.ndim != 1:
        raise WalkForwardError(f"returns_fn must return a 1-D array, got shape {r.shape}.")
    if r.size != expected_len:
        raise WalkForwardError(
            f"returns_fn returned {r.size} value(s) for a test window of {expected_len} row(s). "
            f"A length mismatch silently misaligns returns with the periods they belong to."
        )
    if not np.all(np.isfinite(r)):
        bad = int(np.count_nonzero(~np.isfinite(r)))
        raise WalkForwardError(f"returns_fn returned {bad} non-finite value(s).")
    if np.any(r < -1.0):
        raise WalkForwardError(
            "returns_fn returned a value below -1.0 (worse than -100%), which drives the "
            "equity curve negative and leaves drawdown undefined."
        )
    return r


class WalkForwardSplitter:
    """Chronological walk-forward splitter with an enforced purge/embargo gap.

    Strict chronological ordering (train_end < test_start) is necessary but not sufficient for
    a leak-free split. Two channels cross an adjacent boundary:

    * **Label horizon `H`** - a label with an H-bar forward horizon attached to the last H
      training rows is realised from out-of-sample bars. Removing those rows is *purging*.
    * **Feature lookback `L`** - a feature with an L-bar lookback evaluated on the first L test
      rows is computed partly from training bars.

    A gap of `max(L, H)` covers both. Pass `max_feature_lookback` and `label_horizon` so that
    the gap is checked against them instead of taken on trust.
    """

    def __init__(
        self,
        mode: SplitMode = SplitMode.EXPANDING,
        periods_per_year: int = 252,
        risk_free_rate: float = 0.0,
    ) -> None:
        """
        Args:
            mode: EXPANDING (anchored) or ROLLING (fixed-length) training window.
            periods_per_year: Observations per year in the return series supplied by
                `returns_fn`. 252 for daily bars, 12 for monthly, 78 for 5-minute RTH bars.
            risk_free_rate: Annualized risk-free rate as a decimal, on the same basis as
                `periods_per_year`. Subtracted from the annualized return in the Sharpe
                numerator.
        """
        if not isinstance(mode, SplitMode):
            raise WalkForwardError(f"mode must be a SplitMode, got {type(mode).__name__}.")
        self.mode = mode
        self.periods_per_year = _require_int(periods_per_year, "periods_per_year", 1)
        if isinstance(risk_free_rate, bool) or not isinstance(
            risk_free_rate, (int, float, np.floating)
        ):
            raise WalkForwardError("risk_free_rate must be a float.")
        if not math.isfinite(float(risk_free_rate)):
            raise WalkForwardError("risk_free_rate must be finite.")
        self.risk_free_rate = float(risk_free_rate)

    # ------------------------------------------------------------------ splitting

    @staticmethod
    def min_required_rows(train_size: int, test_size: int, embargo_size: int) -> int:
        """Rows needed for exactly one fold."""
        return (
            _require_int(train_size, "train_size", 1)
            + _require_int(embargo_size, "embargo_size", 0)
            + _require_int(test_size, "test_size", 1)
        )

    @staticmethod
    def generate_splits(
        n_rows: int,
        train_size: int,
        test_size: int,
        embargo_size: int = DEFAULT_EMBARGO,
        mode: SplitMode = SplitMode.EXPANDING,
        max_feature_lookback: Optional[int] = None,
        label_horizon: Optional[int] = None,
    ) -> List[WalkForwardSplit]:
        """Generates purged/embargoed walk-forward fold indices in chronological order.

        Test windows are contiguous and non-overlapping in both modes, so per-fold results can
        be concatenated into a single out-of-sample series without double-counting rows.

        Args:
            n_rows: Row count of the chronologically sorted dataset.
            train_size: Training rows in the first fold. Under EXPANDING this is the anchored
                window's initial length; under ROLLING it is its fixed length.
            test_size: Out-of-sample rows per fold, and the step by which each fold advances.
            embargo_size: Rows discarded between train_end and test_start.
            mode: EXPANDING or ROLLING.
            max_feature_lookback: Longest backward feature window `L`, in rows, if known.
            label_horizon: Longest forward label horizon `H`, in rows, if known.

        Returns:
            One `WalkForwardSplit` per fold; empty if the dataset is too short.

        Raises:
            WalkForwardError: On a non-integer or out-of-range bound, an unknown mode, or an
                `embargo_size` smaller than `max(max_feature_lookback, label_horizon)`.
        """
        n_rows = _require_int(n_rows, "n_rows", 0)
        train_size = _require_int(train_size, "train_size", 1)
        # test_size >= 1 is a termination condition, not a style preference: the loop advances
        # by test_size, so 0 would spin forever emitting identical folds.
        test_size = _require_int(test_size, "test_size", 1)
        embargo_size = _require_int(embargo_size, "embargo_size", 0)
        if not isinstance(mode, SplitMode):
            raise WalkForwardError(f"mode must be a SplitMode, got {type(mode).__name__}.")

        WalkForwardSplitter._check_embargo(embargo_size, max_feature_lookback, label_horizon)

        splits: List[WalkForwardSplit] = []
        start = 0
        current_train_size = train_size
        fold_idx = 0

        while start + current_train_size + embargo_size + test_size <= n_rows:
            train_start = 0 if mode == SplitMode.EXPANDING else start
            train_end = start + current_train_size

            embargo_start = train_end
            embargo_end = train_end + embargo_size

            test_start = embargo_end
            test_end = test_start + test_size

            splits.append(
                WalkForwardSplit(
                    fold_index=fold_idx,
                    train_indices=(train_start, train_end),
                    test_indices=(test_start, test_end),
                    embargo_indices=(embargo_start, embargo_end),
                )
            )

            fold_idx += 1
            if mode == SplitMode.ROLLING:
                start += test_size
            else:  # EXPANDING: absorb the fold just tested into the next training window
                current_train_size += test_size

        if not splits:
            logger.warning(
                "No walk-forward folds generated: %d row(s) available, %d required "
                "(train %d + embargo %d + test %d).",
                n_rows,
                WalkForwardSplitter.min_required_rows(train_size, test_size, embargo_size),
                train_size,
                embargo_size,
                test_size,
            )
        return splits

    @staticmethod
    def _check_embargo(
        embargo_size: int,
        max_feature_lookback: Optional[int],
        label_horizon: Optional[int],
    ) -> None:
        """Enforces embargo >= max(L, H), or warns that the gap is unverified."""
        required = 0
        if max_feature_lookback is not None:
            required = max(
                required, _require_int(max_feature_lookback, "max_feature_lookback", 0)
            )
        if label_horizon is not None:
            required = max(required, _require_int(label_horizon, "label_horizon", 0))

        if max_feature_lookback is None and label_horizon is None:
            logger.warning(
                "embargo_size=%d has not been checked against the strategy's feature lookback "
                "or label horizon. Pass max_feature_lookback and label_horizon to have the gap "
                "verified rather than assumed.",
                embargo_size,
            )
            return

        if embargo_size < required:
            raise WalkForwardError(
                f"embargo_size={embargo_size} is smaller than the required gap of {required} "
                f"row(s) (max of feature lookback {max_feature_lookback} and label horizon "
                f"{label_horizon}). Training rows would keep labels realised inside the test "
                f"window, or test rows would carry features computed from training bars."
            )

    # ------------------------------------------------------------------ evaluation

    def evaluate_walk_forward(
        self,
        df: pd.DataFrame,
        train_size: int,
        test_size: int,
        embargo_size: int,
        fit_predict_fn: Callable[[pd.DataFrame, pd.DataFrame], np.ndarray],
        target_col: str = "target",
        timestamp_col: Optional[str] = None,
        returns_fn: Optional[Callable[[pd.DataFrame, np.ndarray], Sequence[float]]] = None,
        hide_test_labels: bool = True,
        max_feature_lookback: Optional[int] = None,
        label_horizon: Optional[int] = None,
    ) -> List[FoldMetrics]:
        """Runs the fold sequence and returns per-fold out-of-sample metrics.

        Args:
            df: Chronologically sorted dataset. Not re-sorted here - if `timestamp_col` is
                given, ordering is verified and a violation raises.
            train_size, test_size, embargo_size, max_feature_lookback, label_horizon:
                As in `generate_splits`.
            fit_predict_fn: Called once per fold as `fn(train_df, test_df)` and must return one
                prediction per test row. `train_df` carries `target_col`; `test_df` does not,
                unless `hide_test_labels=False`.
            target_col: Column holding the realised label.
            timestamp_col: Optional column used for the human-readable period labels and for
                the chronological ordering check.
            returns_fn: Called as `fn(test_df, predictions)` and must return the fold's realised
                per-period strategy returns as simple decimals (0.01 is +1%), one per test row.
                Mapping a prediction to a position is strategy-specific, so it is the caller's
                decision. Without it, `sharpe_ratio`, `max_drawdown_pct` and `win_rate` are
                `None` rather than invented.
            hide_test_labels: Drop `target_col` from the frame handed to `fit_predict_fn`, so a
                callback cannot read the out-of-sample answers it is being scored against.

        Returns:
            One `FoldMetrics` per fold, in chronological order.

        Raises:
            WalkForwardError: On an invalid frame, a missing column, out-of-order timestamps,
                a prediction/return length mismatch, or an under-sized embargo.
        """
        if not isinstance(df, pd.DataFrame):
            raise WalkForwardError(f"df must be a pandas DataFrame, got {type(df).__name__}.")
        if target_col not in df.columns:
            raise WalkForwardError(f"target_col {target_col!r} is not a column of df.")
        if not callable(fit_predict_fn):
            raise WalkForwardError("fit_predict_fn must be callable.")
        if returns_fn is not None and not callable(returns_fn):
            raise WalkForwardError("returns_fn must be callable or None.")
        if timestamp_col is not None:
            self._check_chronological(df, timestamp_col)

        splits = self.generate_splits(
            n_rows=len(df),
            train_size=train_size,
            test_size=test_size,
            embargo_size=embargo_size,
            mode=self.mode,
            max_feature_lookback=max_feature_lookback,
            label_horizon=label_horizon,
        )

        fold_metrics_list: List[FoldMetrics] = []

        for split in splits:
            tr_start, tr_end = split.train_indices
            te_start, te_end = split.test_indices

            train_df = df.iloc[tr_start:tr_end]
            test_df = df.iloc[te_start:te_end]
            visible_test_df = test_df.drop(columns=[target_col]) if hide_test_labels else test_df

            predictions = np.asarray(fit_predict_fn(train_df, visible_test_df))
            actuals = test_df[target_col].to_numpy()

            if predictions.ndim != 1 or predictions.shape[0] != actuals.shape[0]:
                raise WalkForwardError(
                    f"Fold {split.fold_index}: fit_predict_fn returned shape "
                    f"{predictions.shape} for {actuals.shape[0]} test row(s). NumPy would "
                    f"broadcast or silently compare a mismatched pair, yielding a meaningless "
                    f"accuracy."
                )

            accuracy = float(np.count_nonzero(predictions == actuals) / actuals.shape[0])

            sharpe: Optional[float] = None
            max_dd: Optional[float] = None
            win_rate: Optional[float] = None
            if returns_fn is not None:
                r = _validated_returns(returns_fn(test_df, predictions), actuals.shape[0])
                sharpe = self._sharpe(r)
                max_dd = self._max_drawdown(r)
                win_rate = float(np.count_nonzero(r > 0.0) / r.size)

            fold_metrics_list.append(
                FoldMetrics(
                    fold_index=split.fold_index,
                    train_period=self._period_label(train_df, timestamp_col, tr_start, tr_end),
                    test_period=self._period_label(test_df, timestamp_col, te_start, te_end),
                    sample_count=len(test_df),
                    accuracy=round(accuracy, 4),
                    sharpe_ratio=None if sharpe is None else round(sharpe, 4),
                    max_drawdown_pct=None if max_dd is None else round(max_dd, 6),
                    win_rate=None if win_rate is None else round(win_rate, 4),
                )
            )

        return fold_metrics_list

    def _sharpe(self, r: np.ndarray) -> Optional[float]:
        """Annualized Sharpe: (mean * ppy - rf) / (sample sd * sqrt(ppy)).

        `None` below two observations or on a constant series, where a sample standard
        deviation is undefined or zero. A literally constant series has zero variance
        analytically, but `np.std` returns rounding error of order 1e-18 for it, which would
        turn an undefined Sharpe into a finite but absurd ~1e16 - so constancy is tested on the
        input rather than on the derived statistic.
        """
        if r.size < _MIN_SHARPE_OBS:
            logger.warning(
                "Sharpe undefined for a fold of %d observation(s); at least %d are required.",
                r.size,
                _MIN_SHARPE_OBS,
            )
            return None
        if bool(np.all(r == r[0])):
            return None
        excess_ann_return = float(np.mean(r)) * self.periods_per_year - self.risk_free_rate
        vol = float(np.std(r, ddof=1)) * math.sqrt(self.periods_per_year)
        return _safe_ratio(excess_ann_return, vol)

    @staticmethod
    def _max_drawdown(r: np.ndarray) -> float:
        """Worst peak-to-trough decline of the fold's equity curve, as a non-positive fraction.

        The curve is seeded at 1.0 so a decline starting on the first test period is measured
        from starting capital rather than from that period's own close.
        """
        if r.size == 0:
            return 0.0
        equity = np.concatenate((np.ones(1), np.cumprod(1.0 + r)))
        running_max = np.maximum.accumulate(equity)
        return float(np.min(equity / running_max - 1.0))

    @staticmethod
    def _check_chronological(df: pd.DataFrame, timestamp_col: str) -> None:
        """Rejects an unsorted frame: index folds are chronological only if the rows are."""
        if timestamp_col not in df.columns:
            raise WalkForwardError(f"timestamp_col {timestamp_col!r} is not a column of df.")
        ts = df[timestamp_col]
        if ts.isna().any():
            raise WalkForwardError(f"timestamp_col {timestamp_col!r} contains missing values.")
        if not ts.is_monotonic_increasing:
            raise WalkForwardError(
                f"df is not sorted by {timestamp_col!r} in non-decreasing order. Row-index folds "
                f"assume chronological order; on an unsorted frame the 'training' window would "
                f"contain rows dated after the 'test' window."
            )

    @staticmethod
    def _period_label(
        frame: pd.DataFrame, timestamp_col: Optional[str], start: int, end: int
    ) -> str:
        if timestamp_col and timestamp_col in frame and len(frame) > 0:
            return f"{frame[timestamp_col].iloc[0]} to {frame[timestamp_col].iloc[-1]}"
        return f"Row {start}:{end}"

    # ------------------------------------------------------------------ aggregation

    @staticmethod
    def aggregate_folds(folds: Sequence[FoldMetrics]) -> WalkForwardAggregate:
        """Summarises every fold, including the worst one.

        Reporting the final or best-performing fold hides regime dependence, which is the
        failure mode walk-forward validation exists to expose.

        Raises:
            WalkForwardError: If `folds` is empty; there is nothing to conclude from no folds.
        """
        if not folds:
            raise WalkForwardError("aggregate_folds requires at least one fold.")

        accuracies = np.asarray([f.accuracy for f in folds], dtype=float)
        sharpes = [f.sharpe_ratio for f in folds if f.sharpe_ratio is not None]
        drawdowns = [f.max_drawdown_pct for f in folds if f.max_drawdown_pct is not None]
        with_returns = sum(1 for f in folds if f.max_drawdown_pct is not None)

        if len(folds) < 3:
            logger.warning(
                "Only %d fold(s) aggregated. Regime dependence is not visible across fewer "
                "than 3-5 distinct time windows.",
                len(folds),
            )

        return WalkForwardAggregate(
            fold_count=len(folds),
            mean_accuracy=float(np.mean(accuracies)),
            # Sample sd; undefined for a single fold, reported as 0.0 dispersion.
            std_accuracy=float(np.std(accuracies, ddof=1)) if accuracies.size > 1 else 0.0,
            min_accuracy=float(np.min(accuracies)),
            max_accuracy=float(np.max(accuracies)),
            folds_with_returns=with_returns,
            mean_sharpe=float(np.mean(sharpes)) if sharpes else None,
            min_sharpe=float(np.min(sharpes)) if sharpes else None,
            worst_max_drawdown_pct=float(np.min(drawdowns)) if drawdowns else None,
        )


def walk_forward_splits(
    n_rows: int,
    train_size: int,
    test_size: int,
    embargo: int,
    mode: str = "expanding",
) -> List[Dict[str, Tuple[int, int]]]:
    """Dict-shaped wrapper over `WalkForwardSplitter.generate_splits`.

    Raises:
        WalkForwardError: On an unrecognised `mode`. A typo such as "expandng" previously fell
            through to ROLLING and silently changed the validation methodology.
    """
    normalized = mode.strip().upper() if isinstance(mode, str) else mode
    try:
        split_mode = SplitMode(normalized)
    except ValueError as exc:
        raise WalkForwardError(f"mode must be 'expanding' or 'rolling', got {mode!r}.") from exc

    obj_splits = WalkForwardSplitter.generate_splits(
        n_rows=n_rows,
        train_size=train_size,
        test_size=test_size,
        embargo_size=embargo,
        mode=split_mode,
    )
    return [
        {
            "train": s.train_indices,
            "embargo": s.embargo_indices,
            "test": s.test_indices,
        }
        for s in obj_splits
    ]
