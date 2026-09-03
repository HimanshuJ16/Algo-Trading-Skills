"""
alternative-data-feature-integration:
Enforces Point-in-Time (PIT) integrity by explicitly mapping publication lags
and safely aligning alternative data to a trading schedule.

Conventions enforced by this module:
  * All datetimes are **naive UTC**. Timezone-aware datetimes are rejected at
    ingest and at alignment to avoid a mid-loop TypeError and to prevent
    UTC/ET offset confusion that would silently shift knowledge times.
  * `publication_lag` is non-negative; a negative lag would make the knowledge
    timestamp precede the event and silently re-introduce look-ahead bias.
  * `feature_value` is finite; NaN/inf are rejected so they cannot flow through
    the forward-fill and defeat equality comparisons.
  * Data revisions are modelled as **appended** PIT facts (never overwrites):
    a restatement's knowledge timestamp is `revised_date + publication_lag`,
    so the original fact is served until the restatement becomes knowable.

Thread-safety: this integrator is NOT thread-safe. Concurrent ingest/align
must be serialized by the caller (e.g. snapshot the result of `pit_features`
for read-side concurrency). Production serving must persist PIT features with
idempotent partition finalization externally; this helper holds state in
memory only.
"""
import bisect
import dataclasses
import enum
import logging
import math
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class AltDataIntegratorError(Exception):
    """Base class for all alternative-data integrator errors."""


class AltDataValidationError(AltDataIntegratorError):
    """Raised when a raw vendor event violates an ingest invariant
    (negative lag, non-finite value, timezone-aware datetime, schema drift)."""


class PointInTimeAlignmentError(AltDataIntegratorError):
    """Raised when the trading schedule passed to alignment violates an
    invariant (e.g. timezone-aware trading times)."""


class StalenessState(enum.Enum):
    """Freshness state of an aligned value relative to a trading time."""
    UNKNOWN = "unknown"   # no fact has been published for this source yet
    FRESH = "fresh"       # last known fact is within its max_age TTL
    STALE = "stale"       # last known fact exceeds its max_age TTL


@dataclasses.dataclass(frozen=True)
class RawAltDataEvent:
    """Raw alternative data as received from a vendor.

    `event_timestamp` and `revised_date` are naive UTC. When `revised_date`
    is set, this event represents a restatement whose knowledge timestamp is
    `revised_date + publication_lag` rather than `event_timestamp + lag`;
    the original fact remains in place and is served until the restatement's
    knowledge timestamp passes.
    """
    source_id: str
    event_timestamp: datetime
    publication_lag: timedelta
    feature_value: float
    schema_version: str = "1.0"
    revised_date: Optional[datetime] = None


@dataclasses.dataclass(frozen=True)
class PointInTimeFeature:
    """An immutable Point-in-Time fact strictly tied to when the algorithm
    could have known it. Frozen so PIT facts are value objects: hashable for
    set-based dedup and safe to share. Use `dataclasses.replace()` to derive
    a revised fact rather than mutating.

    `event_timestamp` retains the originating event's timestamp so an auditor
    can re-derive the applied lag (`knowledge_timestamp - event_timestamp`, or
    `knowledge_timestamp - revised_date` for a restatement) from the stored
    fact alone. It is always populated by `ingest_events`; the default exists
    only to keep the field order backward compatible.
    """
    source_id: str
    knowledge_timestamp: datetime
    feature_value: float
    schema_version: str = "1.0"
    revised_date: Optional[datetime] = None
    event_timestamp: Optional[datetime] = None


@dataclasses.dataclass(frozen=True)
class SourceConfig:
    """Per-source operational configuration for the integrator.

    `max_age` bounds how long a value may be forward-filled before it is
    marked STALE. `expected_schema_version` is asserted against each
    ingested payload's `schema_version` to detect vendor schema drift.
    """
    source_id: str
    max_age: Optional[timedelta] = None
    expected_schema_version: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class AlignedValue:
    """The aligned, PIT-safe value for a single source at one trading time.

    `value` is None when no fact has been published yet (staleness_state
    UNKNOWN) or when the last fact exceeds the source's max_age TTL
    (staleness_state STALE). `knowledge_timestamp` and `age` retain the
    provenance of the last known fact even when stale so downstream can
    decide whether to downweight or fall back.
    """
    source_id: str
    value: Optional[float]
    knowledge_timestamp: Optional[datetime]
    age: Optional[timedelta]
    staleness_state: StalenessState


class AltDataIntegrator:
    """
    Integrates alternative data by calculating strict Knowledge Dates and
    aligning irregular frequencies to the strategy's trading timeline.

    Ingest is atomic and idempotent: all events are validated and staged in a
    first pass (raising AltDataValidationError on the first bad event without
    mutating state), then committed keyed on ``(source_id,
    knowledge_timestamp)`` so a backfill/restart resending the same events
    dedups rather than duplicates. Two *different* facts colliding on that key
    inside one batch are rejected rather than silently resolved by list order;
    a later ingest call still overwrites an earlier fact, which is the
    in-place correction path. Revisions are appended as new PIT facts; the
    original is served until the restatement's knowledge timestamp passes.
    """

    def __init__(self, source_configs: Optional[dict[str, SourceConfig]] = None):
        # Dedup store keyed on (source_id, knowledge_timestamp); last-write-wins.
        self._pit_by_key: dict[tuple[str, datetime], PointInTimeFeature] = {}
        # Derived indices, rebuilt after every ingest.
        self._pit_sorted: list[PointInTimeFeature] = []
        self._by_source: dict[str, list[PointInTimeFeature]] = {}
        self._timestamps_by_source: dict[str, list[datetime]] = {}
        self._source_configs: dict[str, SourceConfig] = dict(source_configs or {})

    # ------------------------------------------------------------------ #
    # Ingest
    # ------------------------------------------------------------------ #
    def ingest_events(self, events: list[RawAltDataEvent]) -> None:
        """
        Validate, then convert raw events into Point-In-Time features by
        applying the publication lag.

        Pass 1 validates every event, stages the derived PIT facts, and raises
        AltDataValidationError on the first violation WITHOUT mutating state,
        so the integrator is never left half-populated by a mid-batch
        exception. Pass 2 commits the staged facts. A restatement
        (``revised_date`` set) is appended as a new PIT fact with
        ``knowledge_timestamp = revised_date + publication_lag``; it does not
        overwrite the original, so the as-of merge serves the original until
        the restatement's knowledge timestamp passes.

        Within a single batch, two events that map to the same
        ``(source_id, knowledge_timestamp)`` but carry different content are
        **ambiguous**: only one can be the as-of value, and silently taking the
        list-order-last one would make ``pit_features`` depend on the order the
        batch happened to arrive in. Such a collision raises
        AltDataValidationError. Byte-identical repeats are true duplicates (a
        backfill or restart resending the same rows) and collapse silently, so
        ingest stays idempotent. Across separate calls the later call still
        wins, which is the documented in-place correction path.
        """
        # Pass 1: validate and stage everything first (atomicity).
        staged: dict[tuple[str, datetime], PointInTimeFeature] = {}
        for event in events:
            self._validate_event(event)
            publication_reference = (
                event.revised_date if event.revised_date is not None
                else event.event_timestamp
            )
            knowledge_time = publication_reference + event.publication_lag
            pit_feature = PointInTimeFeature(
                source_id=event.source_id,
                knowledge_timestamp=knowledge_time,
                feature_value=event.feature_value,
                schema_version=event.schema_version,
                revised_date=event.revised_date,
                event_timestamp=event.event_timestamp,
            )
            key = (event.source_id, knowledge_time)
            previous = staged.get(key)
            if previous is not None and previous != pit_feature:
                raise AltDataValidationError(
                    f"ambiguous within-batch collision for source "
                    f"{event.source_id!r} at knowledge_timestamp "
                    f"{knowledge_time.isoformat()}: {previous!r} vs "
                    f"{pit_feature!r}. Two different facts cannot both be the "
                    f"as-of value; resolve upstream or ingest the correction "
                    f"in a separate call."
                )
            staged[key] = pit_feature

        # Pass 2: commit (idempotent; a later call overwrites an earlier fact).
        self._pit_by_key.update(staged)

        self._rebuild_indices()
        logger.info(f"Ingested and PIT-mapped {len(events)} alternative data events.")

    def _validate_event(self, event: RawAltDataEvent) -> None:
        """Enforce every ingest invariant. Raises AltDataValidationError."""
        # Naive-UTC guard on event_timestamp and revised_date.
        if event.event_timestamp.tzinfo is not None:
            raise AltDataValidationError(
                f"event_timestamp for source {event.source_id!r} must be naive UTC; "
                f"got tzinfo={event.event_timestamp.tzinfo!r}."
            )
        if event.revised_date is not None and event.revised_date.tzinfo is not None:
            raise AltDataValidationError(
                f"revised_date for source {event.source_id!r} must be naive UTC; "
                f"got tzinfo={event.revised_date.tzinfo!r}."
            )
        # Non-negative publication lag guards the core PIT invariant.
        if event.publication_lag < timedelta(0):
            raise AltDataValidationError(
                f"publication_lag for source {event.source_id!r} must be non-negative; "
                f"got {event.publication_lag!r}."
            )
        # Finite feature_value prevents NaN/inf flowing through the forward-fill.
        if not math.isfinite(event.feature_value):
            raise AltDataValidationError(
                f"feature_value for source {event.source_id!r} must be finite; "
                f"got {event.feature_value!r}."
            )
        # Schema-version contract: reject vendor schema drift when configured.
        cfg = self._source_configs.get(event.source_id)
        if (cfg is not None
                and cfg.expected_schema_version is not None
                and event.schema_version != cfg.expected_schema_version):
            raise AltDataValidationError(
                f"schema_version drift for source {event.source_id!r}: "
                f"expected {cfg.expected_schema_version!r}, "
                f"got {event.schema_version!r}."
            )

    def reset(self) -> None:
        """Clear all ingested state. Useful between independent backtest runs."""
        self._pit_by_key.clear()
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """Rebuild the derived sorted and per-source views from the dedup store.

        Deterministic tie-break: sort by ``(knowledge_timestamp, source_id)``.
        Because the upsert key is ``(source_id, knowledge_timestamp)``, ties on
        a knowledge timestamp can only occur across distinct sources, so this
        order is total and reproducible across runs regardless of input order.
        """
        self._pit_sorted = sorted(
            self._pit_by_key.values(),
            key=lambda f: (f.knowledge_timestamp, f.source_id),
        )
        self._by_source = {}
        for feat in self._pit_sorted:
            self._by_source.setdefault(feat.source_id, []).append(feat)
        self._timestamps_by_source = {
            sid: [f.knowledge_timestamp for f in feats]
            for sid, feats in self._by_source.items()
        }

    def _known_source_ids(self) -> list[str]:
        """Every source the integrator must report on, in deterministic order.

        The union of sources with ingested facts and sources declared in
        ``source_configs``: a declared source that never delivered data is a
        vendor outage to report as UNKNOWN, not a key to omit.
        """
        return sorted(set(self._by_source) | set(self._source_configs))

    # ------------------------------------------------------------------ #
    # Read-only access
    # ------------------------------------------------------------------ #
    @property
    def pit_features(self) -> tuple[PointInTimeFeature, ...]:
        """Immutable snapshot of all PIT features, deterministically sorted.

        Returning a tuple makes the PIT invariant an enforceable contract:
        callers cannot mutate internal state through this accessor.
        """
        return tuple(self._pit_sorted)

    # ------------------------------------------------------------------ #
    # Alignment
    # ------------------------------------------------------------------ #
    def align_to_trading_schedule(
        self,
        trading_times: list[datetime],
        max_age: Optional[timedelta] = None,
    ) -> dict[datetime, dict[str, AlignedValue]]:
        """
        Align irregular alternative data to a trading schedule via a
        PIT-compliant as-of forward-fill, per source.

        Returns a dict keyed by each trading time (in the caller's original
        order) whose value is a dict keyed by ``source_id`` of
        :class:`AlignedValue`. This per-source shape fixes multi-source scalar
        clobbering: each source's last-known value, knowledge timestamp, age,
        and staleness state are preserved independently.

        Every source that has ingested facts **and** every source named in
        ``source_configs`` appears in each slot, so a configured source that
        has never delivered a single row surfaces as
        :attr:`StalenessState.UNKNOWN` rather than being absent from the
        mapping. A silently missing key is the shape a vendor outage takes,
        and it reaches the model as a ``KeyError`` or, worse, a defaulted
        ``0.0``; an explicit UNKNOWN is what the degradation policy can act on.

        Because the result is keyed by trading time, duplicate
        ``trading_times`` collapse into one entry: ``len(result)`` may be less
        than ``len(trading_times)``. Look values up by timestamp; never zip the
        result positionally against another series.

        Bounded staleness: when ``(trading_time - knowledge_timestamp)`` exceeds
        the source's ``max_age`` (from :class:`SourceConfig`, else the
        ``max_age`` argument), the value is reported as ``None`` with
        ``staleness_state`` :attr:`StalenessState.STALE` rather than silently
        forward-filling an arbitrarily stale value on a vendor outage/lapse.
        When no fact has been published yet, ``staleness_state`` is
        :attr:`StalenessState.UNKNOWN` and ``value`` is ``None``.

        All ``trading_times`` must be naive UTC; timezone-aware values raise
        :class:`PointInTimeAlignmentError` to avoid a mid-loop TypeError and
        UTC/ET offset confusion.
        """
        # Naive-UTC guard on the trading schedule.
        for t in trading_times:
            if t.tzinfo is not None:
                raise PointInTimeAlignmentError(
                    f"trading_times must be naive UTC; got tzinfo={t.tzinfo!r} "
                    f"for {t!r}."
                )

        # Hoisted: the source universe is fixed for the whole schedule.
        source_ids = self._known_source_ids()

        aligned_features: dict[datetime, dict[str, AlignedValue]] = {}
        for t in trading_times:  # caller's original order is preserved
            per_source: dict[str, AlignedValue] = {}
            for source_id in source_ids:
                cfg = self._source_configs.get(source_id)
                src_max_age = (
                    cfg.max_age if cfg is not None and cfg.max_age is not None
                    else max_age
                )
                feats = self._by_source.get(source_id, [])
                ts_list = self._timestamps_by_source.get(source_id, [])
                idx = bisect.bisect_right(ts_list, t) - 1
                if idx < 0:
                    per_source[source_id] = AlignedValue(
                        source_id=source_id,
                        value=None,
                        knowledge_timestamp=None,
                        age=None,
                        staleness_state=StalenessState.UNKNOWN,
                    )
                else:
                    pit = feats[idx]
                    age = t - pit.knowledge_timestamp
                    if src_max_age is not None and age > src_max_age:
                        per_source[source_id] = AlignedValue(
                            source_id=source_id,
                            value=None,
                            knowledge_timestamp=pit.knowledge_timestamp,
                            age=age,
                            staleness_state=StalenessState.STALE,
                        )
                    else:
                        per_source[source_id] = AlignedValue(
                            source_id=source_id,
                            value=pit.feature_value,
                            knowledge_timestamp=pit.knowledge_timestamp,
                            age=age,
                            staleness_state=StalenessState.FRESH,
                        )
            aligned_features[t] = per_source
        return aligned_features
