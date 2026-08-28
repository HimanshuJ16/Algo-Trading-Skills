"""Synthetic continuous futures contract construction.

Splices individual futures contract histories into one continuous, gap-free
price series suitable for backtesting and technical analysis.

Two decisions define such a series, and both are made explicit here rather than
buried:

* **When to roll** -- volume crossover, open-interest crossover, or a fixed
  number of calendar days before expiration. Roll dates are not standardised
  across vendors, so two series built from identical raw data can disagree.
* **How to splice** -- additive (back-adjusted) or proportional (ratio) shifting
  of the *older* contracts.

Adjustment direction follows the standard back-adjustment convention. CSI Data's
Unfair Advantage manual states it precisely: "the new contract price minus the
past contract price on roll-from day represents the delta price difference that
is added to the past contract prices", and "the new contract prices remain
unaffected by the back-adjustment splicing process". The newest segment is
therefore left at real market prices and every earlier segment is shifted by the
cumulative gap of all later rolls. A series built the other way round -- leaving
the *oldest* prices real and shifting the recent ones -- is forward-adjusted, and
its final bar does not match the price you can actually trade today.

Roll timing is leak-free by construction. A crossover observed during session
``t`` uses that session's completed volume or open interest, which is not known
until the session closes, so the series switches contracts at session ``t+1``.
The gap is measured on session ``t`` -- the last session priced off the old
contract, "roll-from day" in CSI's terminology -- where both closes are known.

Sources:
  - CSI Data, Unfair Advantage online manual, "Back Adjusted Contracts" and
    "Back-Adjusted Charts":
    https://www.csidata.com/custserv/onlinehelp/OnlineManual/backadjustedoverview.htm
  - CME Group, "Contract Month Codes": https://www.cmegroup.com/month-codes.html
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# CME Group contract month codes (https://www.cmegroup.com/month-codes.html).
# The letter following the product root always encodes the expiration month.
MONTH_CODES: Dict[str, int] = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}

# Product root (may contain digits, e.g. '6E'), month code, then a 1- or 2-digit
# year code. The root is lazy so the month code binds to the last valid letter.
_SYMBOL_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,5}?)(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{1,2})$")

# Price columns shifted by the adjustment. Volume and open interest are never
# adjusted: they are quantities, not prices.
PRICE_COLUMNS: Tuple[str, ...] = ("open", "high", "low", "close")
PASSTHROUGH_COLUMNS: Tuple[str, ...] = ("volume", "open_interest")


class RollMethod(str, Enum):
    """Rule deciding which session the series moves to the next contract."""

    VOLUME_CROSSOVER = "VOLUME_CROSSOVER"
    OPEN_INTEREST_CROSSOVER = "OPEN_INTEREST_CROSSOVER"
    DAYS_BEFORE_EXPIRY = "DAYS_BEFORE_EXPIRY"


class AdjustmentMethod(str, Enum):
    """Rule removing -- or deliberately keeping -- the price gap at each roll."""

    ADDITIVE_BACK_ADJUSTMENT = "ADDITIVE_BACK_ADJUSTMENT"
    PROPORTIONAL_RATIO = "PROPORTIONAL_RATIO"
    UNADJUSTED_CONCATENATED = "UNADJUSTED_CONCATENATED"


@dataclass
class FuturesContractBar:
    """One session of a single futures contract.

    Schema reference for callers assembling the per-contract DataFrames; the
    engine consumes DataFrames, not instances of this class.
    """

    symbol: str               # e.g. 'ESH24', 'ESM24'
    timestamp: str            # 'YYYY-MM-DD'
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_interest: float


@dataclass(frozen=True)
class RollEvent:
    """One contract transition in the constructed series.

    Attributes:
        from_contract: Contract the series was priced off before the roll.
        to_contract: Contract the series is priced off from ``effective_session``.
        reference_session: Last session priced off ``from_contract``. Both closes
            used for the gap are observed on this session ("roll-from day").
        effective_session: First session priced off ``to_contract``.
        front_close: ``from_contract`` close on ``reference_session``.
        next_close: ``to_contract`` close on ``reference_session``.
        gap: ``next_close - front_close``. Positive in contango, negative in
            backwardation. Added to every earlier segment under additive
            back-adjustment.
        ratio: ``next_close / front_close``. Multiplied into every earlier
            segment under proportional adjustment.
        trigger: Roll rule that fired.
    """

    from_contract: str
    to_contract: str
    reference_session: Any
    effective_session: Any
    front_close: float
    next_close: float
    gap: float
    ratio: float
    trigger: RollMethod


@dataclass
class ContinuousFuturesSeries:
    """Result of splicing one product's contracts into a continuous series.

    Attributes:
        ticker: Caller-supplied label for the product.
        adjustment_method: Splicing rule actually applied.
        roll_method: Roll rule actually applied.
        df_continuous: Indexed by session. Columns: ``active_contract``,
            ``segment_id``, ``is_roll_session``, ``raw_close``,
            ``adjusted_close``, ``adjustment_offset``, ``adjustment_factor``,
            plus ``raw_`` / ``adjusted_`` open, high and low where the inputs
            carried them, plus unadjusted ``volume`` / ``open_interest``.
            ``adjusted = raw + adjustment_offset`` under additive adjustment
            (``adjustment_factor`` is 1.0); ``adjusted = raw *
            adjustment_factor`` under proportional adjustment
            (``adjustment_offset`` is 0.0).
        roll_events: One :class:`RollEvent` per transition, oldest first.
        total_roll_events: ``len(roll_events)``.
        cumulative_gap: Additive offset applied to the *oldest* segment, in
            contract quote units (index points, USD, EUR, JPY -- whatever the
            product is quoted in). 0.0 when no roll occurred.
        cumulative_ratio: Multiplicative factor applied to the oldest segment.
            1.0 when no roll occurred.
        sessions_without_active_bar: Sessions present in some contract's history
            but absent from the then-active contract, and therefore absent from
            ``df_continuous``.
        unevaluable_trigger_sessions: Sessions where the roll trigger could not
            be evaluated (no common bar, or a non-finite trigger value).
    """

    ticker: str
    adjustment_method: AdjustmentMethod
    roll_method: RollMethod
    df_continuous: pd.DataFrame
    roll_events: List[RollEvent]
    total_roll_events: int
    cumulative_gap: float
    cumulative_ratio: float
    sessions_without_active_bar: int
    unevaluable_trigger_sessions: int


def _as_date(value: Any) -> date:
    """Coerce a session label to a ``date``.

    Raises:
        ValueError: If the label is not an ISO date string, ``date``,
            ``datetime`` or ``pandas.Timestamp``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(
                f"Session label {value!r} is not an ISO 'YYYY-MM-DD' date."
            ) from exc
    raise ValueError(
        f"Session label {value!r} of type {type(value).__name__} cannot be interpreted "
        f"as a calendar date; DAYS_BEFORE_EXPIRY needs dated sessions."
    )


def _finite_float(value: Any) -> Optional[float]:
    """Return ``value`` as a finite float, or ``None`` if NaN / Inf / non-numeric."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class SyntheticContinuousFuturesEngine:
    """Builds a back-adjusted continuous futures series from individual contracts.

    Stateless between calls; never mutates the caller's DataFrames.
    """

    def __init__(
        self,
        roll_method: RollMethod = RollMethod.VOLUME_CROSSOVER,
        adjustment_method: AdjustmentMethod = AdjustmentMethod.ADDITIVE_BACK_ADJUSTMENT,
        days_before_expiry: int = 5,
        min_confirmation_sessions: int = 1,
    ) -> None:
        """
        Args:
            roll_method: Which roll trigger to evaluate.
            adjustment_method: How to splice across the roll.
            days_before_expiry: **Calendar** days before expiration at which
                ``DAYS_BEFORE_EXPIRY`` rolls. Ignored by the crossover methods.
            min_confirmation_sessions: Consecutive sessions the crossover must
                hold before the roll fires. ``1`` (the default) rolls on the
                first crossover -- what most vendors do, and what a single
                anomalous volume print can trigger.

        Raises:
            ValueError: On an unknown method or a non-positive / non-integer
                session count.
        """
        try:
            roll_method = RollMethod(roll_method)
        except ValueError as exc:
            raise ValueError(
                f"roll_method must be a RollMethod (or its exact string value), "
                f"got {roll_method!r}."
            ) from exc
        try:
            adjustment_method = AdjustmentMethod(adjustment_method)
        except ValueError as exc:
            raise ValueError(
                f"adjustment_method must be an AdjustmentMethod (or its exact string "
                f"value), got {adjustment_method!r}."
            ) from exc
        if not isinstance(days_before_expiry, int) or isinstance(days_before_expiry, bool):
            raise ValueError(
                f"days_before_expiry must be an integer count of calendar days, "
                f"got {days_before_expiry!r}."
            )
        if days_before_expiry < 0:
            raise ValueError(f"days_before_expiry must be >= 0, got {days_before_expiry}.")
        if (
            not isinstance(min_confirmation_sessions, int)
            or isinstance(min_confirmation_sessions, bool)
            or min_confirmation_sessions < 1
        ):
            raise ValueError(
                f"min_confirmation_sessions must be an integer >= 1, "
                f"got {min_confirmation_sessions!r}."
            )

        self.roll_method = roll_method
        self.adjustment_method = adjustment_method
        self.days_before_expiry = days_before_expiry
        self.min_confirmation_sessions = min_confirmation_sessions

    # -- input validation and contract ordering --------------------------------

    def _order_contracts(
        self,
        contract_data: Mapping[str, pd.DataFrame],
        contract_expiries: Optional[Mapping[str, str]],
    ) -> Tuple[List[str], Optional[Dict[str, date]]]:
        """Order contracts front-to-back by expiration.

        Uses ``contract_expiries`` when supplied, otherwise decodes the CME month
        code embedded in each symbol. Lexicographic ordering is never used: it
        places ``ESH25`` before ``ESZ24`` and silently inverts the whole series.

        Returns:
            The ordered symbols and, when known, their parsed expiration dates.

        Raises:
            ValueError: On missing, malformed, duplicated or mixed-product
                expiration information.
        """
        symbols = list(contract_data.keys())

        if contract_expiries is not None:
            missing = sorted(set(symbols) - set(contract_expiries))
            if missing:
                raise ValueError(
                    f"contract_expiries is missing entries for {missing}; supply an "
                    f"expiration date for every contract or omit the argument entirely."
                )
            parsed: Dict[str, date] = {}
            for symbol in symbols:
                raw = contract_expiries[symbol]
                try:
                    parsed[symbol] = date.fromisoformat(str(raw))
                except ValueError as exc:
                    raise ValueError(
                        f"contract_expiries[{symbol!r}] = {raw!r} is not an ISO "
                        f"'YYYY-MM-DD' date."
                    ) from exc
            expiry_values = list(parsed.values())
            duplicates = sorted({d.isoformat() for d in expiry_values if expiry_values.count(d) > 1})
            if duplicates:
                raise ValueError(
                    f"contract_expiries maps more than one contract to {duplicates}; "
                    f"contract ordering would be ambiguous."
                )
            return sorted(symbols, key=lambda s: parsed[s]), parsed

        roots = set()
        keys: Dict[str, Tuple[int, int]] = {}
        for symbol in symbols:
            match = _SYMBOL_RE.match(str(symbol).strip().upper())
            if match is None:
                raise ValueError(
                    f"Contract symbol {symbol!r} does not decode as <root><month code>"
                    f"<year>, e.g. 'ESZ24'. Pass contract_expiries to order contracts "
                    f"explicitly."
                )
            year_code = match.group("year")
            if len(year_code) == 1:
                raise ValueError(
                    f"Contract symbol {symbol!r} uses a single-digit year code, whose "
                    f"decade is ambiguous ('ESZ4' is 2014 or 2024). Pass "
                    f"contract_expiries, or use two-digit year codes."
                )
            roots.add(match.group("root"))
            # Two-digit year codes resolve into 2000-2099; pre-2000 history must
            # supply contract_expiries.
            keys[symbol] = (2000 + int(year_code), MONTH_CODES[match.group("month")])

        if len(roots) > 1:
            raise ValueError(
                f"contract_data mixes products {sorted(roots)}; a continuous series "
                f"must be built from one product's expirations."
            )
        key_values = list(keys.values())
        duplicate_keys = sorted({k for k in key_values if key_values.count(k) > 1})
        if duplicate_keys:
            raise ValueError(
                f"contract_data contains contracts resolving to the same expiration "
                f"{duplicate_keys}; contract ordering would be ambiguous."
            )
        return sorted(symbols, key=lambda s: keys[s]), None

    def _required_columns(self) -> Tuple[str, ...]:
        """Columns the configured roll method cannot run without."""
        if self.roll_method == RollMethod.VOLUME_CROSSOVER:
            return ("close", "volume")
        if self.roll_method == RollMethod.OPEN_INTEREST_CROSSOVER:
            return ("close", "open_interest")
        return ("close",)

    def _validate_frames(self, contract_data: Mapping[str, pd.DataFrame]) -> None:
        """Raise ``ValueError`` if any contract frame cannot be spliced."""
        required = self._required_columns()
        for symbol, df in contract_data.items():
            if not isinstance(df, pd.DataFrame):
                raise ValueError(
                    f"contract_data[{symbol!r}] must be a pandas DataFrame, "
                    f"got {type(df).__name__}."
                )
            if df.empty:
                raise ValueError(f"contract_data[{symbol!r}] is empty.")
            absent = [column for column in required if column not in df.columns]
            if absent:
                raise ValueError(
                    f"contract_data[{symbol!r}] is missing column(s) {absent} required "
                    f"by roll_method {self.roll_method.value}."
                )
            if df.index.has_duplicates:
                dupes = df.index[df.index.duplicated()].unique().tolist()
                raise ValueError(
                    f"contract_data[{symbol!r}] has duplicate index labels {dupes[:5]}; "
                    f"one bar per contract per session is required."
                )

    @staticmethod
    def _session_union(contract_data: Mapping[str, pd.DataFrame]) -> List[Any]:
        """Sorted union of every contract's session labels."""
        labels = {label for df in contract_data.values() for label in df.index}
        try:
            return sorted(labels)
        except TypeError as exc:
            raise ValueError(
                "Contract indexes mix incomparable label types (e.g. str and Timestamp); "
                "normalise every contract to one index type before splicing."
            ) from exc

    # -- roll trigger ----------------------------------------------------------

    def _trigger_fires(
        self,
        front: str,
        back: str,
        session: Any,
        contract_data: Mapping[str, pd.DataFrame],
        expiries: Optional[Mapping[str, date]],
    ) -> Optional[bool]:
        """Evaluate the roll trigger on one *completed* session.

        Returns:
            ``True`` / ``False`` when the trigger could be evaluated, or ``None``
            when it could not -- the contracts share no bar on this session, or a
            trigger value is NaN / Inf. ``None`` is not ``False``: a missing
            value must never read as "no crossover".
        """
        front_df = contract_data[front]
        back_df = contract_data[back]
        if session not in front_df.index or session not in back_df.index:
            return None

        if self.roll_method == RollMethod.DAYS_BEFORE_EXPIRY:
            if expiries is None:  # pragma: no cover - guarded by the public entry point
                raise ValueError("DAYS_BEFORE_EXPIRY reached without expiration dates.")
            return (expiries[front] - _as_date(session)).days <= self.days_before_expiry

        column = "volume" if self.roll_method == RollMethod.VOLUME_CROSSOVER else "open_interest"
        front_value = _finite_float(front_df.loc[session, column])
        back_value = _finite_float(back_df.loc[session, column])
        if front_value is None or back_value is None:
            logger.warning(
                "Non-finite %s for %s/%s on %s; roll trigger not evaluated this session.",
                column, front, back, session,
            )
            return None
        return back_value > front_value

    # -- public API ------------------------------------------------------------

    def construct_continuous_series(
        self,
        ticker: str,
        contract_data: Dict[str, pd.DataFrame],
        contract_expiries: Optional[Mapping[str, str]] = None,
    ) -> ContinuousFuturesSeries:
        """Splice individual contract histories into one continuous series.

        Args:
            ticker: Label for the product, used in the result and in log lines.
            contract_data: Contract symbol -> DataFrame indexed by session, with a
                ``close`` column plus whichever of ``volume`` / ``open_interest``
                the roll method needs. ``open``, ``high``, ``low``, ``volume`` and
                ``open_interest`` are carried through when present.
            contract_expiries: Optional contract symbol -> ``'YYYY-MM-DD'``
                expiration. Required by ``DAYS_BEFORE_EXPIRY``; otherwise
                optional, and authoritative for contract ordering when supplied.

        Returns:
            The spliced :class:`ContinuousFuturesSeries`.

        Raises:
            ValueError: On empty input, malformed frames, un-orderable symbols, a
                non-finite close on a bar the series uses, or a non-positive close
                where proportional adjustment needs to divide.
        """
        if not contract_data:
            raise ValueError("contract_data cannot be empty.")
        if self.roll_method == RollMethod.DAYS_BEFORE_EXPIRY and contract_expiries is None:
            raise ValueError(
                "roll_method DAYS_BEFORE_EXPIRY requires contract_expiries; without "
                "expiration dates the series would silently never roll."
            )

        self._validate_frames(contract_data)
        ordered, expiries = self._order_contracts(contract_data, contract_expiries)
        sessions = self._session_union(contract_data)

        rows: List[Dict[str, Any]] = []
        roll_events: List[RollEvent] = []
        active_index = 0
        confirmations = 0
        last_emitted_segment = -1
        sessions_without_active_bar = 0
        unevaluable = 0

        for position, session in enumerate(sessions):
            # The roll decision for this session uses only sessions that have
            # already closed, so today's bar never depends on today's volume.
            if position > 0 and active_index < len(ordered) - 1:
                reference = sessions[position - 1]
                front, back = ordered[active_index], ordered[active_index + 1]
                fired = self._trigger_fires(front, back, reference, contract_data, expiries)
                if fired is None:
                    unevaluable += 1
                    confirmations = 0
                elif fired:
                    confirmations += 1
                else:
                    confirmations = 0

                if fired and confirmations >= self.min_confirmation_sessions:
                    roll_events.append(
                        self._build_roll_event(front, back, reference, session, contract_data)
                    )
                    active_index += 1
                    confirmations = 0

            active = ordered[active_index]
            active_df = contract_data[active]
            if session not in active_df.index:
                sessions_without_active_bar += 1
                continue

            bar = active_df.loc[session]
            raw_close = _finite_float(bar["close"])
            if raw_close is None:
                raise ValueError(
                    f"{ticker}: non-finite close for {active} on {session}. A NaN close "
                    f"propagates silently into every adjusted bar; clean or drop the bar."
                )

            segment_id = len(roll_events)
            row: Dict[str, Any] = {
                "timestamp": session,
                "active_contract": active,
                "segment_id": segment_id,
                # First bar actually emitted for a new segment. Derived from the
                # emitted rows rather than the roll's effective session, which may
                # itself have been skipped for want of a bar.
                "is_roll_session": segment_id > 0 and segment_id != last_emitted_segment,
                "raw_close": raw_close,
            }
            last_emitted_segment = segment_id
            for column in PRICE_COLUMNS:
                if column == "close" or column not in active_df.columns:
                    continue
                value = _finite_float(bar[column])
                row[f"raw_{column}"] = math.nan if value is None else value
            for column in PASSTHROUGH_COLUMNS:
                if column in active_df.columns:
                    row[column] = bar[column]
            rows.append(row)

        if not rows:
            raise ValueError(
                f"{ticker}: no session produced a bar on the active contract; check that "
                f"the contract indexes overlap the contracts' own histories."
            )

        df_continuous = self._apply_adjustment(ticker, rows, roll_events)
        cumulative_gap = float(df_continuous["adjustment_offset"].iloc[0])
        cumulative_ratio = float(df_continuous["adjustment_factor"].iloc[0])

        if self.adjustment_method == AdjustmentMethod.UNADJUSTED_CONCATENATED and roll_events:
            logger.warning(
                "%s: UNADJUSTED_CONCATENATED leaves %d roll discontinuit(ies) in the "
                "series; any return computed across a roll session is an artefact.",
                ticker, len(roll_events),
            )
        if sessions_without_active_bar:
            logger.warning(
                "%s: %d session(s) dropped because the then-active contract had no bar.",
                ticker, sessions_without_active_bar,
            )

        return ContinuousFuturesSeries(
            ticker=ticker,
            adjustment_method=self.adjustment_method,
            roll_method=self.roll_method,
            df_continuous=df_continuous,
            roll_events=roll_events,
            total_roll_events=len(roll_events),
            cumulative_gap=cumulative_gap,
            cumulative_ratio=cumulative_ratio,
            sessions_without_active_bar=sessions_without_active_bar,
            unevaluable_trigger_sessions=unevaluable,
        )

    # -- internals -------------------------------------------------------------

    def _build_roll_event(
        self,
        front: str,
        back: str,
        reference: Any,
        effective: Any,
        contract_data: Mapping[str, pd.DataFrame],
    ) -> RollEvent:
        """Measure the roll gap on the last session priced off ``front``."""
        front_close = _finite_float(contract_data[front].loc[reference, "close"])
        next_close = _finite_float(contract_data[back].loc[reference, "close"])
        if front_close is None or next_close is None:
            raise ValueError(
                f"Roll {front} -> {back} on {reference}: non-finite close "
                f"({front_close!r} / {next_close!r}); the roll gap cannot be measured."
            )
        gap = next_close - front_close
        ratio = next_close / front_close if front_close != 0.0 else math.nan
        logger.info(
            "ROLL %s -> %s: reference session %s, effective %s, gap %+.4f, ratio %s",
            front, back, reference, effective, gap,
            f"{ratio:.6f}" if math.isfinite(ratio) else "undefined",
        )
        return RollEvent(
            from_contract=front,
            to_contract=back,
            reference_session=reference,
            effective_session=effective,
            front_close=front_close,
            next_close=next_close,
            gap=gap,
            ratio=ratio,
            trigger=self.roll_method,
        )

    @staticmethod
    def _segment_adjustments(
        roll_events: Sequence[RollEvent],
    ) -> Tuple[List[float], List[float]]:
        """Cumulative offset and factor per segment, newest segment at identity.

        Segment ``j`` is shifted by the gaps of every roll that happens *after*
        it, which leaves the newest segment at real market prices.
        """
        segments = len(roll_events) + 1
        offsets = [0.0] * segments
        factors = [1.0] * segments
        for index in range(segments - 2, -1, -1):
            event = roll_events[index]
            offsets[index] = offsets[index + 1] + event.gap
            factors[index] = factors[index + 1] * event.ratio
        return offsets, factors

    def _apply_adjustment(
        self,
        ticker: str,
        rows: List[Dict[str, Any]],
        roll_events: Sequence[RollEvent],
    ) -> pd.DataFrame:
        """Apply the configured adjustment and materialise the output frame."""
        if self.adjustment_method == AdjustmentMethod.PROPORTIONAL_RATIO:
            for event in roll_events:
                if event.front_close <= 0.0 or event.next_close <= 0.0:
                    raise ValueError(
                        f"{ticker}: proportional adjustment needs strictly positive closes "
                        f"at every roll, but {event.from_contract} = {event.front_close} and "
                        f"{event.to_contract} = {event.next_close} on "
                        f"{event.reference_session}. Use ADDITIVE_BACK_ADJUSTMENT for "
                        f"series that trade at or below zero."
                    )

        offsets, factors = self._segment_adjustments(roll_events)

        for row in rows:
            segment = row["segment_id"]
            if self.adjustment_method == AdjustmentMethod.ADDITIVE_BACK_ADJUSTMENT:
                offset, factor = offsets[segment], 1.0
            elif self.adjustment_method == AdjustmentMethod.PROPORTIONAL_RATIO:
                offset, factor = 0.0, factors[segment]
            else:
                offset, factor = 0.0, 1.0
            row["adjustment_offset"] = offset
            row["adjustment_factor"] = factor

            for column in PRICE_COLUMNS:
                raw_key = "raw_close" if column == "close" else f"raw_{column}"
                if raw_key not in row:
                    continue
                raw_value = row[raw_key]
                if self.adjustment_method == AdjustmentMethod.PROPORTIONAL_RATIO:
                    row[f"adjusted_{column}"] = raw_value * factor
                else:
                    row[f"adjusted_{column}"] = raw_value + offset

        ordered_columns = [
            "active_contract", "segment_id", "is_roll_session",
            "raw_open", "raw_high", "raw_low", "raw_close",
            "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
            "adjustment_offset", "adjustment_factor",
            "volume", "open_interest",
        ]
        df = pd.DataFrame(rows).set_index("timestamp")
        return df[[column for column in ordered_columns if column in df.columns]]
