"""
backtest-database-schema-for-point-in-time-queries: Point-in-time (PIT)
data store with bitemporal as-of query semantics.

Two independent time axes are modelled, following the SQL:2011 temporal
vocabulary (system/transaction time vs. application/valid time):

* ``known_at``   -- knowledge time: the instant the value became *externally
                    known* (vendor publication / filing release timestamp).
                    This is deliberately NOT the database insertion time.
* ``valid_from`` -- valid (application) time: the instant from which the value
                    describes the real world (e.g. the fiscal period end).

An as-of query answers "what did we know at instant Q about the state in
effect at instant Q" -- the only query a backtest may issue on a given bar.

Temporal string contract
------------------------
All temporal values are parsed into timezone-aware UTC instants before any
comparison. Raw lexicographic string comparison is NOT used, because it is
only chronologically valid when every timestamp shares an identical UTC offset
representation and an identical number of fractional-second digits
(RFC 3339 s5.1). Mixed-granularity inputs such as ``"2023-02-01"`` versus
``"2023-02-01T14:30:00-05:00"`` silently mis-order under string comparison,
which is precisely the lookahead bias this schema exists to prevent.

Date-only values are widened conservatively -- always in the direction that
*excludes* a record rather than admitting it:

* a record's ``known_at`` / ``valid_from`` day widens to the LAST instant of
  that day, so a figure published at some unknown time on day D is not treated
  as tradable at 09:30 on day D;
* a query's as-of day widens to the FIRST instant of that day.

Consequence: ``known_at == as_of_date`` at date granularity yields NO match.
Supply full timestamps when intraday precision matters.
"""
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, time, timezone
import logging
import math
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "TemporalFormatError",
    "PITRecord",
    "PITQueryResult",
    "PointInTimeStore",
]

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Conservative widening bounds for date-only (no clock time) inputs.
_FIRST_INSTANT_OF_DAY = time(0, 0, 0, 0)
_LAST_INSTANT_OF_DAY = time(23, 59, 59, 999999)


class TemporalFormatError(ValueError):
    """Raised when a temporal value is not an ISO 8601 date or date-time.

    Rejecting these at the boundary is a correctness control, not cosmetics:
    an unpadded value such as ``"2023-9-01"`` sorts *after* ``"2023-10-01"``
    lexicographically, which silently selects the wrong point-in-time record.
    """


def _parse_instant(raw: str, *, label: str, day_bound: time) -> datetime:
    """Parses an ISO 8601 date or date-time into a timezone-aware UTC instant.

    Args:
        raw: ``YYYY-MM-DD`` or a full ISO 8601 date-time (``Z`` suffix allowed).
        label: Field name used in error messages.
        day_bound: Clock time applied when ``raw`` carries no time component.

    Returns:
        The corresponding instant normalised to UTC.

    Raises:
        TemporalFormatError: If ``raw`` is not a string or not valid ISO 8601.
    """
    if not isinstance(raw, str):
        raise TemporalFormatError(
            f"{label} must be an ISO 8601 string, got {type(raw).__name__}"
        )

    text = raw.strip()
    if not text:
        raise TemporalFormatError(f"{label} must be a non-empty ISO 8601 string")

    if _DATE_ONLY_RE.match(text):
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise TemporalFormatError(f"{label}={raw!r} is not a valid date") from exc
        return datetime.combine(day, day_bound, tzinfo=timezone.utc)

    # Reject date-only values that are not extended format. Python accepts the
    # basic form "20230115", which would silently skip the conservative
    # end-of-day widening above and be read as midnight instead.
    if "T" not in text and ":" not in text:
        raise TemporalFormatError(
            f"{label}={raw!r} is not an extended-format ISO 8601 date. "
            "Use YYYY-MM-DD, or a full date-time."
        )

    # datetime.fromisoformat does not accept the 'Z' designator before 3.11.
    normalised = text[:-1] + "+00:00" if text[-1] in ("Z", "z") else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise TemporalFormatError(
            f"{label}={raw!r} is not an ISO 8601 date (YYYY-MM-DD) or date-time. "
            "Zero-padded, fixed-width values are required."
        ) from exc

    # A naive timestamp is assumed UTC; mixing naive local times across vendors
    # is itself a leakage vector, so the assumption is made explicit here.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class PITRecord:
    """A single immutable observation on the knowledge and valid time axes.

    Restatements are never applied in place: a correction is a NEW record with
    a later ``known_at`` and the same ``valid_from``.
    """

    symbol: str
    field: str
    value: float
    known_at: str  # ISO 8601 instant at which the value became publicly known
    valid_from: str  # ISO 8601 instant from which the value is in effect
    revision: int = 0  # 0 = as-originally-reported, 1+ = successive restatements

    known_at_instant: datetime = dataclass_field(init=False, repr=False, compare=False)
    valid_from_instant: datetime = dataclass_field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.field, str) or not self.field.strip():
            raise ValueError("field must be a non-empty string")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError(f"value must be a real number, got {type(self.value).__name__}")
        if not math.isfinite(self.value):
            # NaN/Inf propagates silently through downstream signal maths.
            raise ValueError(f"value must be finite, got {self.value!r}")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError(f"revision must be a non-negative int, got {self.revision!r}")

        # Record-side bounds widen LATE so an unknown publication time within
        # the day cannot be treated as available earlier that same day.
        self.known_at_instant = _parse_instant(
            self.known_at, label="known_at", day_bound=_LAST_INSTANT_OF_DAY
        )
        self.valid_from_instant = _parse_instant(
            self.valid_from, label="valid_from", day_bound=_LAST_INSTANT_OF_DAY
        )


@dataclass
class PITQueryResult:
    """Outcome of an as-of query, including its leakage audit verdict."""

    symbol: str
    field: str
    as_of_date: str
    value: Optional[float]
    known_at: Optional[str]
    has_future_leakage: bool
    revision: Optional[int] = None
    # Value a naive "latest row wins" query would have returned. Populated by
    # audit_leakage() so the audit reports the wrong number, not just a flag.
    naive_value: Optional[float] = None
    naive_known_at: Optional[str] = None


class PointInTimeStore:
    """In-memory bitemporal PIT data store with as-of query semantics.

    Reference implementation for the schema contract; the same predicates map
    directly onto a SQL view over a table indexed on ``(symbol, field, known_at)``.
    """

    def __init__(self) -> None:
        self._records: List[PITRecord] = []

    def insert(self, record: PITRecord) -> None:
        """Appends a record. Records are append-only; never update in place."""
        if not isinstance(record, PITRecord):
            raise TypeError(f"expected PITRecord, got {type(record).__name__}")
        self._records.append(record)

    def _matching(self, symbol: str, field: str) -> List[Tuple[int, PITRecord]]:
        """Returns (insertion_index, record) pairs for a symbol/field series.

        Matching is case-insensitive: a case mismatch would otherwise surface as
        an empty result that a strategy silently reads as "no data".
        """
        symbol_key = symbol.strip().casefold()
        field_key = field.strip().casefold()
        return [
            (idx, r)
            for idx, r in enumerate(self._records)
            if r.symbol.strip().casefold() == symbol_key
            and r.field.strip().casefold() == field_key
        ]

    @staticmethod
    def _select_best(candidates: List[Tuple[int, PITRecord]]) -> PITRecord:
        """Picks the record that was authoritative last.

        Ordering is total and insertion-order-tiebroken, so the same inputs
        always yield the same answer regardless of insert sequence collisions.
        """
        _, best = max(
            candidates,
            key=lambda pair: (
                pair[1].known_at_instant,
                pair[1].valid_from_instant,
                pair[1].revision,
                pair[0],
            ),
        )
        return best

    def query_as_of(
        self,
        symbol: str,
        field: str,
        as_of_date: str,
        valid_as_of: Optional[str] = None,
    ) -> PITQueryResult:
        """Returns the value in effect at ``valid_as_of``, as known at ``as_of_date``.

        Args:
            symbol: Instrument identifier (matched case-insensitively).
            field: Metric name (matched case-insensitively).
            as_of_date: Knowledge-time cutoff; records published later are excluded.
            valid_as_of: Valid-time cutoff. Defaults to ``as_of_date``. Pass an
                explicit value to decouple the axes, e.g. to ask what was known
                on date K about a period ending on date V.

        Raises:
            TemporalFormatError: If either temporal argument is not ISO 8601.
        """
        # Query-side bounds widen EARLY, the conservative direction.
        known_cutoff = _parse_instant(
            as_of_date, label="as_of_date", day_bound=_FIRST_INSTANT_OF_DAY
        )
        valid_cutoff = (
            known_cutoff
            if valid_as_of is None
            else _parse_instant(
                valid_as_of, label="valid_as_of", day_bound=_FIRST_INSTANT_OF_DAY
            )
        )

        candidates = [
            pair
            for pair in self._matching(symbol, field)
            if pair[1].known_at_instant <= known_cutoff
            and pair[1].valid_from_instant <= valid_cutoff
        ]

        if not candidates:
            logger.debug(
                f"PIT MISS: no {symbol}/{field} record known by {as_of_date} "
                f"and effective by {valid_as_of or as_of_date}."
            )
            return PITQueryResult(symbol, field, as_of_date, None, None, False)

        best = self._select_best(candidates)
        return PITQueryResult(
            symbol,
            field,
            as_of_date,
            best.value,
            best.known_at,
            False,
            revision=best.revision,
        )

    def audit_leakage(self, symbol: str, field: str, backtest_date: str) -> PITQueryResult:
        """Compares the PIT answer against what a naive "latest row" query returns.

        ``has_future_leakage`` is True when the series contains at least one
        record published after ``backtest_date`` -- i.e. when a non-PIT query
        against this table on this date WOULD have leaked. The returned
        ``value`` is always the correct PIT value; ``naive_value`` carries the
        wrong number a naive query would have used, so the finding is actionable.
        """
        known_cutoff = _parse_instant(
            backtest_date, label="backtest_date", day_bound=_FIRST_INSTANT_OF_DAY
        )
        all_records = self._matching(symbol, field)
        future = [p for p in all_records if p[1].known_at_instant > known_cutoff]
        pit_result = self.query_as_of(symbol, field, backtest_date)

        if not future:
            return pit_result

        naive_best = self._select_best(all_records)
        logger.warning(
            f"LOOKAHEAD LEAKAGE: {len(future)} record(s) for {symbol}/{field} "
            f"have known_at > {backtest_date}. A naive latest-row query would "
            f"have returned {naive_best.value} (known_at={naive_best.known_at}); "
            f"the PIT query correctly returned {pit_result.value}."
        )
        return PITQueryResult(
            symbol,
            field,
            backtest_date,
            pit_result.value,
            pit_result.known_at,
            has_future_leakage=True,
            revision=pit_result.revision,
            naive_value=naive_best.value,
            naive_known_at=naive_best.known_at,
        )
