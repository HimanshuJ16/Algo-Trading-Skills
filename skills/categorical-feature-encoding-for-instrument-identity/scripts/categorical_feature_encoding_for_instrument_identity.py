"""
categorical-feature-encoding-for-instrument-identity: point-in-time smoothed target
encoding of instrument identity (ticker/symbol) for cross-sectional trading models.

The encoder replaces a high-cardinality symbol column with a single numeric column
holding an empirical-Bayes shrinkage blend of the symbol's own historical target mean
and the global historical target mean (Micci-Barreca, SIGKDD Explorations 3(1), 2001).

Two properties make this implementation safe for financial panels, and both are
enforced rather than assumed:

1. Every row is encoded using only observations that were **already realized** at that
   row's timestamp. An observation at time ``t`` may feed the encoding of a row at time
   ``T`` only when ``t <= T - label_horizon`` *and* ``t < T`` -- the first condition is
   the purge that overlapping forward-looking labels require (Lopez de Prado, *Advances
   in Financial Machine Learning*, ch. 7), the second forbids contemporaneous labels.
2. The caller's row order and DataFrame index survive the call unchanged, so the encoded
   column cannot silently misalign with the labels the caller pairs it with.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_SymbolState = Tuple[pd.Index, np.ndarray, np.ndarray]


class PointInTimeTargetEncoder:
    """
    Encodes instrument symbols as a smoothed, strictly historical target average.

    The encoding applied to a row at time ``T`` for symbol ``s`` is::

        encoded = (local_sum + smoothing_weight * global_mean)
                  / (local_count + smoothing_weight)

    which is algebraically identical to the usual shrinkage form
    ``lambda * local_mean + (1 - lambda) * global_mean`` with
    ``lambda = local_count / (local_count + smoothing_weight)``, but stays defined when
    ``local_count`` is zero. ``local_sum`` and ``local_count`` are the sum and count of
    ``s``'s realized targets, and ``global_mean`` is the mean of all realized targets,
    both restricted to observations available at ``T`` (see the module docstring).

    Args:
        smoothing_weight: Prior strength, in observations. The symbol's own mean carries
            50% of the weight once it has ``smoothing_weight`` realized observations.
            Must be finite and strictly positive: at zero, the encoding of an unseen
            symbol is 0/0.
        label_horizon: How long after its own timestamp a target becomes observable --
            the holding period of a forward-looking label. Pass a ``pd.Timedelta`` (or
            anything subtractable from the time column, e.g. an ``int`` for an integer
            bar index) whenever the label spans more than one timestamp step; a 5-day
            forward return needs ``pd.Timedelta(days=5)``. ``None`` means "realized by
            the next timestamp in the panel", which is correct only for one-step-ahead
            labels.
        cold_start_prior: Value used as ``global_mean`` for rows with no realized history
            at all -- in practice the first timestamp of the panel. The 0.0 default suits
            a centred target such as an excess return; for a 0/1 label set it to the
            expected base rate, or to ``float("nan")`` to make the absence of history
            explicit downstream rather than silently equal to zero.
    """

    def __init__(
        self,
        smoothing_weight: float = 10.0,
        label_horizon: Optional[Any] = None,
        cold_start_prior: float = 0.0,
    ) -> None:
        weight = float(smoothing_weight)
        if not np.isfinite(weight) or weight <= 0.0:
            raise ValueError(
                f"smoothing_weight must be finite and > 0, got {smoothing_weight!r}. "
                "A weight of 0 leaves the encoding of a symbol with no history undefined "
                "(0/0); a negative weight can drive the denominator through zero."
            )
        prior = float(cold_start_prior)
        if np.isinf(prior):
            raise ValueError(
                f"cold_start_prior must be finite or NaN, got {cold_start_prior!r}."
            )

        self.smoothing_weight: float = weight
        self.label_horizon: Optional[Any] = label_horizon
        self.cold_start_prior: float = prior

        self._global_times: Optional[pd.Index] = None
        self._global_sum: Optional[np.ndarray] = None
        self._global_count: Optional[np.ndarray] = None
        self._symbol_states: Dict[Any, _SymbolState] = {}

    # ------------------------------------------------------------------ public API

    @property
    def is_fitted(self) -> bool:
        """True once :meth:`fit` has recorded a history to encode against."""
        return self._global_times is not None

    @property
    def fitted_symbols(self) -> List[Any]:
        """Symbols seen during :meth:`fit`. Anything else cold-starts on transform."""
        return list(self._symbol_states)

    def fit(
        self,
        df: pd.DataFrame,
        time_col: str,
        symbol_col: str,
        target_col: str,
    ) -> "PointInTimeTargetEncoder":
        """
        Records the target history that later encodings are computed from.

        Rows whose target is NaN stay in the panel but are excluded from the statistics:
        a missing label carries no information about the symbol, yet the row still needs
        a feature value. Infinite targets are rejected outright rather than averaged.

        The frame need not be sorted -- ordering is handled internally -- and the
        caller's frame is never mutated.
        """
        times, symbols, targets = self._extract(df, time_col, symbol_col, target_col)

        observed = ~np.isnan(targets)
        frame = pd.DataFrame(
            {
                "_t": times,
                "_s": symbols,
                "_v": np.where(observed, targets, 0.0),
                "_n": observed.astype(np.int64),
            }
        )

        by_time = frame.groupby("_t", sort=True)[["_v", "_n"]].sum()
        self._global_times = by_time.index
        self._global_sum = by_time["_v"].to_numpy(dtype=float).cumsum()
        self._global_count = by_time["_n"].to_numpy(dtype=np.int64).cumsum()

        by_symbol_time = frame.groupby(["_s", "_t"], sort=True)[["_v", "_n"]].sum()
        self._symbol_states = {}
        for symbol, block in by_symbol_time.groupby(level=0, sort=False):
            self._symbol_states[symbol] = (
                block.index.get_level_values(1),
                block["_v"].to_numpy(dtype=float).cumsum(),
                block["_n"].to_numpy(dtype=np.int64).cumsum(),
            )

        logger.debug(
            "Fitted point-in-time target encoder on %d rows, %d symbols, %d timestamps.",
            len(frame),
            len(self._symbol_states),
            len(self._global_times),
        )
        return self

    def transform(
        self,
        df: pd.DataFrame,
        time_col: str,
        symbol_col: str,
        encoded_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Returns a copy of ``df`` with the encoded column appended.

        Row order, DataFrame index and every existing column are preserved exactly; only
        ``encoded_col`` (default ``f"{symbol_col}_encoded"``) is added, and it is refused
        rather than overwritten if that name is already taken.

        Safe to call on live rows: no target column is read here, and only the history
        recorded by :meth:`fit` -- restricted per row to what was realized at that row's
        timestamp -- can influence the result.
        """
        if not self.is_fitted:
            raise RuntimeError(
                "transform() called before fit(); there is no history to encode against."
            )

        out_col = encoded_col or f"{symbol_col}_encoded"
        self._require_columns(df, [time_col, symbol_col])
        if out_col in df.columns:
            raise ValueError(
                f"Column '{out_col}' already exists in the frame. Pass encoded_col=... "
                "with a free name; refusing to overwrite a caller column."
            )

        times = self._as_time_index(df[time_col], time_col)
        symbols = self._as_symbols(df[symbol_col], symbol_col)

        global_sum, global_count = self._read_cumulative(
            self._history_position(times, self._global_times),
            self._global_sum,
            self._global_count,
        )
        global_mean = np.divide(
            global_sum,
            global_count,
            out=np.full(len(df), self.cold_start_prior, dtype=float),
            where=global_count > 0,
        )

        local_sum = np.zeros(len(df), dtype=float)
        local_count = np.zeros(len(df), dtype=float)
        # Group rows by symbol in one pass. Scanning the full symbol column once per
        # fitted symbol instead would be O(symbols x rows), which is minutes rather
        # than seconds on the multi-thousand-name universes this skill exists for.
        for symbol, rows in self._row_groups(symbols):
            state = self._symbol_states.get(symbol)
            if state is None:
                continue  # unseen symbol: cold-starts onto the global mean
            symbol_times, symbol_sum, symbol_count = state
            local_sum[rows], local_count[rows] = self._read_cumulative(
                self._history_position(times[rows], symbol_times),
                symbol_sum,
                symbol_count,
            )

        encoded = (local_sum + self.smoothing_weight * global_mean) / (
            local_count + self.smoothing_weight
        )

        result = df.copy()
        # Positional assignment of the raw array: index-aligned assignment would be
        # ambiguous on a frame with duplicate index labels, which panels acquire easily
        # through concatenation.
        result[out_col] = encoded
        return result

    def fit_transform(
        self,
        df: pd.DataFrame,
        time_col: str,
        symbol_col: str,
        target_col: str,
        encoded_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Fits on ``df`` and encodes it in a single call.

        Fitting and transforming the same rows is not leaky here: the per-row history
        lookup can only reach observations realized before the row, so a row never
        contributes to its own encoding and no row is influenced by a later one. This
        differs from ``sklearn.preprocessing.TargetEncoder``, which needs random k-fold
        cross-fitting because it has no time axis to order the data by.
        """
        self.fit(df, time_col, symbol_col, target_col)
        return self.transform(df, time_col, symbol_col, encoded_col=encoded_col)

    # ------------------------------------------------------------------- internals

    @staticmethod
    def _require_columns(df: pd.DataFrame, columns: List[str]) -> None:
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"Expected a pandas DataFrame, got {type(df).__name__}.")
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise KeyError(
                f"Column(s) {missing} not found in the frame. "
                f"Available columns: {list(df.columns)}."
            )

    @staticmethod
    def _as_symbols(series: pd.Series, symbol_col: str) -> np.ndarray:
        symbols = series.to_numpy()
        if pd.isna(symbols).any():
            raise ValueError(
                f"Symbol column '{symbol_col}' contains missing values; every row must "
                "name the instrument it belongs to."
            )
        return symbols

    @staticmethod
    def _row_groups(symbols: np.ndarray) -> List[Tuple[Any, np.ndarray]]:
        """Row positions per distinct symbol, computed in a single O(n log n) pass."""
        codes, uniques = pd.factorize(symbols)
        order = np.argsort(codes, kind="stable")
        bounds = np.searchsorted(codes[order], np.arange(len(uniques) + 1))
        return [
            (symbol, order[bounds[i] : bounds[i + 1]])
            for i, symbol in enumerate(uniques)
        ]

    @staticmethod
    def _as_time_index(series: pd.Series, time_col: str) -> pd.Index:
        index = pd.Index(series)
        if index.hasnans:
            raise ValueError(
                f"Time column '{time_col}' contains missing values; a row with no "
                "timestamp has no point in time to be encoded as of."
            )
        return index

    def _extract(
        self,
        df: pd.DataFrame,
        time_col: str,
        symbol_col: str,
        target_col: str,
    ) -> Tuple[pd.Series, np.ndarray, np.ndarray]:
        self._require_columns(df, [time_col, symbol_col, target_col])
        if df.empty:
            raise ValueError("Cannot fit an encoder on an empty frame.")

        self._as_time_index(df[time_col], time_col)
        times = df[time_col].reset_index(drop=True)

        symbols = self._as_symbols(df[symbol_col], symbol_col)

        try:
            targets = pd.to_numeric(df[target_col], errors="raise").to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Target column '{target_col}' must be numeric to be averaged: {exc}"
            ) from exc
        if np.isinf(targets).any():
            raise ValueError(
                f"Target column '{target_col}' contains infinite values, which would "
                "poison every encoding derived from them. Clean or clip them first."
            )

        self._check_horizon(times)
        return times, symbols, targets

    def _check_horizon(self, times: pd.Series) -> None:
        """Fails loudly on a horizon that cannot be applied to this time column."""
        if self.label_horizon is None:
            return
        try:
            if self.label_horizon < self.label_horizon - self.label_horizon:
                raise ValueError(
                    f"label_horizon must not be negative, got {self.label_horizon!r}; a "
                    "negative horizon would let a row see labels from its own future."
                )
            _ = pd.Index(times.iloc[:1]) - self.label_horizon
        except TypeError as exc:
            raise TypeError(
                f"label_horizon {self.label_horizon!r} cannot be subtracted from time "
                f"column values of dtype {times.dtype}. Use a pd.Timedelta for datetime "
                "timestamps, or a plain number for an integer/float bar index."
            ) from exc

    def _history_position(self, times: pd.Index, history_times: pd.Index) -> np.ndarray:
        """
        Index into ``history_times`` of the newest observation usable at each time.

        Returns -1 where nothing qualifies. Two conditions apply and the stricter wins:
        the observation's label must already be realized (``t <= T - label_horizon``),
        and the observation must predate the row (``t < T``) -- the latter is what stops
        a contemporaneous label leaking in when the horizon is zero or ``None``.
        """
        not_contemporaneous = np.asarray(
            history_times.searchsorted(times, side="left"), dtype=np.int64
        ) - 1
        if self.label_horizon is None:
            return not_contemporaneous
        realized = np.asarray(
            history_times.searchsorted(times - self.label_horizon, side="right"),
            dtype=np.int64,
        ) - 1
        return np.minimum(realized, not_contemporaneous)

    @staticmethod
    def _read_cumulative(
        position: np.ndarray,
        cumulative_sum: np.ndarray,
        cumulative_count: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Reads cumulative stats at ``position``, yielding zeros wherever it is -1."""
        available = position >= 0
        safe = np.where(available, position, 0)
        sums = np.where(available, cumulative_sum[safe], 0.0)
        counts = np.where(available, cumulative_count[safe], 0).astype(float)
        return sums, counts
