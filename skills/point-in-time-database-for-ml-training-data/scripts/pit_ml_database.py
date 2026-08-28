"""
point-in-time-database-for-ml-training-data
===========================================

As-of join engine for ML feature-matrix construction.

The engine answers exactly one question, once per (label row, feature):

    "What was the latest value of feature F for symbol S that a model
     could have *known* at instant T?"

It answers it on the **knowledge axis** (``available_at`` -- when the value
became externally known), never on the event/period axis (``event_timestamp``
-- the period the value describes). Joining on the event axis is the canonical
target-leakage bug in financial ML: Q4 EPS describes the period ending
2022-12-31 but is not knowable until the filing lands in 2023.

Design decisions that exist to make leakage structurally hard:

* Every timestamp is parsed and normalised to a timezone-aware UTC instant at
  the boundary. Raw ISO strings are never compared with ``<=`` -- see
  RFC 3339 section 5.1, which makes lexicographic ordering correct *only* when
  zone representation and fractional-second precision are identical across
  every value. Vendor feeds do not guarantee that.
* A date-granular ``available_at`` is resolved to **end of day** by default, so
  a value published on day D is not joinable to a decision made on day D.
* "Latest" is resolved by an explicit total order ``(available_at, revision,
  insertion_sequence)``, so same-instant corrections never resolve by accident.
* Non-finite feature values and target values are rejected at ingest rather
  than propagated into a training matrix as silent ``NaN``.

Pure standard library. No third-party dependency.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

TimestampLike = Union[str, _dt.datetime, _dt.date]

#: Sentinel used to build an upper-bound bisect key on (instant, revision, seq).
_MAX_ORD = float("inf")

#: How a date-granular ``available_at`` is resolved to an instant.
DATE_ONLY_END_OF_DAY = "end_of_day"
DATE_ONLY_START_OF_DAY = "start_of_day"
_DATE_ONLY_POLICIES = (DATE_ONLY_END_OF_DAY, DATE_ONLY_START_OF_DAY)


# --------------------------------------------------------------------------- #
# Timestamp normalisation
# --------------------------------------------------------------------------- #
def _parse_timestamp(raw: TimestampLike, field_name: str) -> Tuple[_dt.datetime, bool]:
    """
    Normalise ``raw`` to ``(aware UTC datetime, is_date_only)``.

    Accepts ISO 8601 strings, ``datetime.date`` and ``datetime.datetime``.
    A naive ``datetime`` is interpreted as UTC -- callers mixing local-time
    naive stamps with offset-aware stamps must normalise upstream.

    Raises:
        ValueError: on any value that is not an unambiguous ISO 8601 timestamp.
            Unpadded components (``2023-9-01``) are rejected here rather than
            silently mis-sorting later.
    """
    if isinstance(raw, _dt.datetime):
        instant = raw if raw.tzinfo is not None else raw.replace(tzinfo=_dt.timezone.utc)
        return instant.astimezone(_dt.timezone.utc), False

    if isinstance(raw, _dt.date):
        return _dt.datetime(raw.year, raw.month, raw.day, tzinfo=_dt.timezone.utc), True

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            f"{field_name}: expected an ISO 8601 string or datetime, got {raw!r}"
        )

    text = raw.strip()
    try:
        day = _dt.date.fromisoformat(text)
    except ValueError:
        pass
    else:
        return _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc), True

    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"{field_name}: {text!r} is not a valid ISO 8601 timestamp "
            f"(zero-pad every component; use 'Z' or an explicit UTC offset): {exc}"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc), False


def _resolve_availability(
    instant: _dt.datetime, is_date_only: bool, policy: str
) -> _dt.datetime:
    """Resolve a feature's ``available_at`` to the instant it became knowable."""
    if not is_date_only or policy == DATE_ONLY_START_OF_DAY:
        return instant
    return instant + _dt.timedelta(days=1)


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name}: expected a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name}: must be finite, got {numeric!r}")
    return numeric


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}: must be a non-empty string, got {value!r}")
    return value.strip()


# --------------------------------------------------------------------------- #
# Public record types
# --------------------------------------------------------------------------- #
@dataclass
class FeatureRecord:
    """
    One observation of one feature, as published.

    Attributes:
        symbol: Instrument identifier.
        feature_name: Feature identifier.
        value: The published value. Must be finite.
        event_timestamp: The period/event the value describes (e.g. fiscal
            quarter end ``2022-12-31``). **Never** used as the availability
            gate -- present only so a naive-join leakage audit can be run.
        available_at: When the value became externally known (publication /
            filing release). This is the axis the as-of join filters on.
        revision: Monotonic correction counter within one ``available_at``
            instant. Higher wins. Ties on ``(available_at, revision)`` fall back
            to insertion order.
    """

    symbol: str
    feature_name: str
    value: float
    event_timestamp: TimestampLike
    available_at: TimestampLike
    revision: int = 0


@dataclass
class LabelRecord:
    """One training row: the instant a prediction is made, and its target."""

    symbol: str
    label_timestamp: TimestampLike
    target_value: float


@dataclass
class PITJoinRow:
    """
    One (label, feature) pair after the as-of join.

    ``is_valid_pit`` is the single gate downstream code should filter on: it is
    ``True`` only when a knowable, non-stale value was found. ``feature_value``
    is ``None`` whenever ``is_valid_pit`` is ``False``.
    """

    symbol: str
    label_timestamp: str
    feature_name: str
    feature_value: Optional[float]
    feature_available_at: Optional[str]
    target_value: float
    is_valid_pit: bool
    is_stale: bool = False
    staleness_days: Optional[float] = None
    naive_join_value: Optional[float] = None
    leakage_blocked: bool = False


@dataclass
class PITDatasetReport:
    """Audit summary for one as-of join or training-matrix build."""

    total_joined_rows: int
    valid_pit_rows: int
    missing_feature_rows: int
    stale_feature_rows: int
    future_leakage_prevented_count: int
    message: str


@dataclass
class TrainingRow:
    """One wide training-matrix row across several features."""

    symbol: str
    label_timestamp: str
    target_value: float
    features: Dict[str, Optional[float]] = field(default_factory=dict)
    is_complete: bool = False


# --------------------------------------------------------------------------- #
# Internal storage
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _StoredFeature:
    value: float
    event_at: _dt.datetime
    available_at: _dt.datetime
    revision: int
    seq: int

    @property
    def pit_key(self) -> Tuple[_dt.datetime, int, int]:
        return (self.available_at, self.revision, self.seq)

    @property
    def naive_key(self) -> Tuple[_dt.datetime, int, int]:
        return (self.event_at, self.revision, self.seq)


@dataclass
class _KeyIndex:
    """Sorted views of one (symbol, feature_name) bucket, built on demand."""

    pit_items: List[_StoredFeature]
    pit_keys: List[Tuple[_dt.datetime, int, int]]
    event_items: List[_StoredFeature]
    event_keys: List[Tuple[_dt.datetime, int, int]]


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class PointInTimeMLDatabase:
    """
    Builds leakage-free ML training data by as-of joining features to labels on
    the knowledge axis (``available_at <= label_timestamp``).

    Args:
        ingestion_lag_days: Constant delay added to every resolved
            ``available_at``, modelling the gap between a vendor publishing a
            value and your pipeline actually holding it. Must be >= 0.
        max_staleness_days: If set, a matched value older than this many days
            at the label instant is refused (``is_valid_pit=False``,
            ``is_stale=True``) instead of being carried forward indefinitely.
            This is the equivalent of ``pandas.merge_asof(tolerance=...)``.
        date_only_availability: How a date-granular ``available_at`` resolves.
            ``"end_of_day"`` (default) treats a value published on day D as
            knowable only from D+1 00:00 UTC, so it cannot be used for a
            decision made on day D. ``"start_of_day"`` permits same-day use and
            is correct only when you have verified the publication preceded the
            decision.
        require_event_before_label: When ``True``, additionally require
            ``event_timestamp <= label_timestamp``. Default ``False``, which is
            correct for features: a published forecast or forward guidance is
            legitimately knowable at T even though the period it describes has
            not occurred. Set ``True`` only when the feature must describe a
            completed period.

    Raises:
        ValueError: on any invalid constructor argument.
    """

    def __init__(
        self,
        ingestion_lag_days: float = 0.0,
        max_staleness_days: Optional[float] = None,
        date_only_availability: str = DATE_ONLY_END_OF_DAY,
        require_event_before_label: bool = False,
    ) -> None:
        lag = _require_finite(ingestion_lag_days, "ingestion_lag_days")
        if lag < 0:
            raise ValueError(f"ingestion_lag_days must be >= 0, got {lag}")

        if max_staleness_days is not None:
            staleness = _require_finite(max_staleness_days, "max_staleness_days")
            if staleness <= 0:
                raise ValueError(f"max_staleness_days must be > 0, got {staleness}")
            self.max_staleness: Optional[_dt.timedelta] = _dt.timedelta(days=staleness)
        else:
            self.max_staleness = None

        if date_only_availability not in _DATE_ONLY_POLICIES:
            raise ValueError(
                f"date_only_availability must be one of {_DATE_ONLY_POLICIES}, "
                f"got {date_only_availability!r}"
            )

        self.ingestion_lag = _dt.timedelta(days=lag)
        self.date_only_availability = date_only_availability
        self.require_event_before_label = bool(require_event_before_label)

        # Append-only store, keyed by (symbol, feature_name). Sort order is
        # built lazily in _ensure_indexed() so a bulk load costs one O(n log n)
        # sort per key regardless of the order records arrive in, rather than
        # an O(n) shift per insert.
        self._store: Dict[Tuple[str, str], List[_StoredFeature]] = {}
        self._index: Dict[Tuple[str, str], _KeyIndex] = {}
        self._seq = 0

    # -- ingest ------------------------------------------------------------ #
    def insert_features(self, records: Sequence[FeatureRecord]) -> None:
        """
        Validate and index feature records.

        Every record is fully validated before *any* record is indexed, so a
        malformed batch leaves the store unchanged rather than half-loaded.

        Raises:
            ValueError: on a non-finite value, empty identifier, negative
                revision, or unparseable timestamp.
        """
        if isinstance(records, FeatureRecord):
            raise ValueError(
                "insert_features expects a sequence of FeatureRecord, not a single record"
            )

        staged: List[Tuple[Tuple[str, str], _StoredFeature]] = []
        for position, record in enumerate(records):
            if not isinstance(record, FeatureRecord):
                raise ValueError(
                    f"records[{position}]: expected FeatureRecord, got {type(record).__name__}"
                )

            symbol = _require_identifier(record.symbol, f"records[{position}].symbol")
            feature_name = _require_identifier(
                record.feature_name, f"records[{position}].feature_name"
            )
            value = _require_finite(record.value, f"records[{position}].value")

            if isinstance(record.revision, bool) or not isinstance(record.revision, int):
                raise ValueError(
                    f"records[{position}].revision: expected an int, got {record.revision!r}"
                )
            if record.revision < 0:
                raise ValueError(
                    f"records[{position}].revision: must be >= 0, got {record.revision}"
                )

            event_at, _ = _parse_timestamp(
                record.event_timestamp, f"records[{position}].event_timestamp"
            )
            raw_available, available_is_date_only = _parse_timestamp(
                record.available_at, f"records[{position}].available_at"
            )
            available_at = (
                _resolve_availability(
                    raw_available, available_is_date_only, self.date_only_availability
                )
                + self.ingestion_lag
            )

            staged.append(
                (
                    (symbol, feature_name),
                    _StoredFeature(
                        value=value,
                        event_at=event_at,
                        available_at=available_at,
                        revision=record.revision,
                        seq=0,  # replaced below, once the whole batch is known valid
                    ),
                )
            )

        for key, stub in staged:
            stored = _StoredFeature(
                value=stub.value,
                event_at=stub.event_at,
                available_at=stub.available_at,
                revision=stub.revision,
                seq=self._seq,
            )
            self._seq += 1
            self._store.setdefault(key, []).append(stored)
            self._index.pop(key, None)  # invalidate; rebuilt on next query

        logger.info(
            "Indexed %d feature record(s); store now holds %d record(s).",
            len(staged),
            self._seq,
        )

    # -- lookup ------------------------------------------------------------ #
    def _ensure_indexed(self, key: Tuple[str, str]) -> Optional[_KeyIndex]:
        """Build (or reuse) the sorted views for one bucket."""
        cached = self._index.get(key)
        if cached is not None:
            return cached
        records = self._store.get(key)
        if not records:
            return None

        pit_items = sorted(records, key=lambda r: r.pit_key)
        event_items = sorted(records, key=lambda r: r.naive_key)
        built = _KeyIndex(
            pit_items=pit_items,
            pit_keys=[r.pit_key for r in pit_items],
            event_items=event_items,
            event_keys=[r.naive_key for r in event_items],
        )
        self._index[key] = built
        return built

    @staticmethod
    def _latest_at_or_before(
        items: List[_StoredFeature],
        keys: List[Tuple[_dt.datetime, int, int]],
        cutoff: _dt.datetime,
    ) -> Optional[_StoredFeature]:
        """Greatest record whose ordering instant is at or before ``cutoff``."""
        position = bisect.bisect_right(keys, (cutoff, _MAX_ORD, _MAX_ORD))
        if position == 0:
            return None
        return items[position - 1]

    # -- join -------------------------------------------------------------- #
    def as_of_join(
        self,
        labels: Sequence[LabelRecord],
        feature_name: str,
    ) -> Tuple[List[PITJoinRow], PITDatasetReport]:
        """
        As-of join one feature onto every label row.

        For each label the engine selects the record with the greatest
        ``(available_at, revision, insertion_sequence)`` satisfying
        ``available_at <= label_timestamp``, then applies the staleness bound.

        It independently resolves what a **naive** event-axis join would have
        returned (greatest ``event_timestamp <= label_timestamp``). When that
        record is not the point-in-time answer, the row is flagged
        ``leakage_blocked=True``. That flag counts label rows whose value would
        actually have been wrong -- not merely records that were filtered out.

        Returns:
            ``(rows, report)``. ``rows`` holds one row per label, in input
            order.

        Raises:
            ValueError: on an empty ``feature_name``, a non-``LabelRecord``
                entry, a non-finite target, or an unparseable label timestamp.
        """
        feature_name = _require_identifier(feature_name, "feature_name")
        if isinstance(labels, LabelRecord):
            raise ValueError(
                "as_of_join expects a sequence of LabelRecord, not a single record"
            )

        rows: List[PITJoinRow] = []
        missing = 0
        stale = 0
        leakage_blocked = 0

        for position, label in enumerate(labels):
            if not isinstance(label, LabelRecord):
                raise ValueError(
                    f"labels[{position}]: expected LabelRecord, got {type(label).__name__}"
                )

            symbol = _require_identifier(label.symbol, f"labels[{position}].symbol")
            target = _require_finite(
                label.target_value, f"labels[{position}].target_value"
            )
            # A date-granular label resolves to the START of that day: a model
            # predicting "on 2023-01-21" may only use what was knowable before
            # the day began. Paired with end-of-day availability resolution,
            # this makes same-day publication non-joinable by default.
            decision_at, _ = _parse_timestamp(
                label.label_timestamp, f"labels[{position}].label_timestamp"
            )
            index = self._ensure_indexed((symbol, feature_name))

            if index is None:
                pit_match = None
                naive_match = None
            else:
                pit_match = self._latest_at_or_before(
                    index.pit_items, index.pit_keys, decision_at
                )
                if (
                    pit_match is not None
                    and self.require_event_before_label
                    and pit_match.event_at > decision_at
                ):
                    pit_match = None
                naive_match = self._latest_at_or_before(
                    index.event_items, index.event_keys, decision_at
                )
            row_leaked = naive_match is not None and naive_match is not pit_match
            if row_leaked:
                leakage_blocked += 1

            row_stale = False
            staleness_days: Optional[float] = None
            value: Optional[float] = None
            available_at_iso: Optional[str] = None

            if pit_match is None:
                missing += 1
            else:
                available_at_iso = pit_match.available_at.isoformat()
                age = decision_at - pit_match.available_at
                staleness_days = age.total_seconds() / 86400.0
                if self.max_staleness is not None and age > self.max_staleness:
                    row_stale = True
                    stale += 1
                else:
                    value = pit_match.value

            rows.append(
                PITJoinRow(
                    symbol=symbol,
                    label_timestamp=decision_at.isoformat(),
                    feature_name=feature_name,
                    feature_value=value,
                    feature_available_at=available_at_iso,
                    target_value=target,
                    is_valid_pit=pit_match is not None and not row_stale,
                    is_stale=row_stale,
                    staleness_days=staleness_days,
                    naive_join_value=naive_match.value if naive_match is not None else None,
                    leakage_blocked=row_leaked,
                )
            )

        total = len(rows)
        valid = total - missing - stale
        message = (
            f"PIT ML join complete for {feature_name!r}: {total} label row(s) -> "
            f"{valid} valid, {missing} missing, {stale} stale, "
            f"{leakage_blocked} row(s) where a naive event-date join would have leaked."
        )
        logger.info(message)

        return rows, PITDatasetReport(
            total_joined_rows=total,
            valid_pit_rows=valid,
            missing_feature_rows=missing,
            stale_feature_rows=stale,
            future_leakage_prevented_count=leakage_blocked,
            message=message,
        )

    # -- matrix ------------------------------------------------------------ #
    def build_training_matrix(
        self,
        labels: Sequence[LabelRecord],
        feature_names: Sequence[str],
    ) -> Tuple[List[TrainingRow], PITDatasetReport]:
        """
        Assemble a wide, point-in-time-correct training matrix.

        Each returned :class:`TrainingRow` carries one entry per requested
        feature. A feature that was not knowable (or was stale) at the label
        instant is ``None``, and ``is_complete`` is ``False`` for that row --
        the value is never back-filled or forward-filled, because either would
        reintroduce exactly the leakage this engine exists to prevent. Decide
        the imputation policy downstream, where it is visible and auditable.

        Returns:
            ``(rows, report)``. Report counters are per **cell**
            (rows x features), except ``total_joined_rows``, which counts label
            rows.

        Raises:
            ValueError: on an empty or duplicated feature name, or any error
                raised by :meth:`as_of_join`.
        """
        if isinstance(feature_names, str):
            raise ValueError(
                "feature_names expects a sequence of names, not a single string"
            )
        names = [
            _require_identifier(name, f"feature_names[{i}]")
            for i, name in enumerate(feature_names)
        ]
        if not names:
            raise ValueError("feature_names must contain at least one feature")
        if len(set(names)) != len(names):
            raise ValueError(f"feature_names contains duplicates: {names}")

        per_feature = {name: self.as_of_join(labels, name) for name in names}

        rows: List[TrainingRow] = []
        label_count = len(per_feature[names[0]][0])
        for index in range(label_count):
            template = per_feature[names[0]][0][index]
            values: Dict[str, Optional[float]] = {
                name: per_feature[name][0][index].feature_value for name in names
            }
            rows.append(
                TrainingRow(
                    symbol=template.symbol,
                    label_timestamp=template.label_timestamp,
                    target_value=template.target_value,
                    features=values,
                    is_complete=all(v is not None for v in values.values()),
                )
            )

        missing = sum(per_feature[n][1].missing_feature_rows for n in names)
        stale = sum(per_feature[n][1].stale_feature_rows for n in names)
        leaked = sum(per_feature[n][1].future_leakage_prevented_count for n in names)
        cells = label_count * len(names)
        complete_rows = sum(1 for row in rows if row.is_complete)

        message = (
            f"PIT training matrix: {label_count} label row(s) x {len(names)} feature(s) = "
            f"{cells} cell(s); {complete_rows} complete row(s), {missing} missing cell(s), "
            f"{stale} stale cell(s), {leaked} leaking cell(s) blocked."
        )
        logger.info(message)

        return rows, PITDatasetReport(
            total_joined_rows=label_count,
            valid_pit_rows=cells - missing - stale,
            missing_feature_rows=missing,
            stale_feature_rows=stale,
            future_leakage_prevented_count=leaked,
            message=message,
        )
