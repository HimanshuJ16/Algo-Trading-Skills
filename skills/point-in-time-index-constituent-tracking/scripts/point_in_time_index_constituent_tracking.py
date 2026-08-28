"""
point-in-time-index-constituent-tracking: reconstruct historical index membership.

Resolves "which symbols were in index X on date T" from a log of addition and
deletion events, so a backtest universe contains the names that were actually in
the index on each historical date -- including the ones that were later removed
after a bankruptcy, merger, or delisting.

Membership interval convention
------------------------------
Membership is the **half-open interval ``[effective_add_date, effective_del_date)``**.
S&P Dow Jones Indices makes constituent changes "effective prior to the open of
trading" on the effective date (S&P DJI press release, "Tesla Set to Join S&P 500",
2020-11-16), so:

  * a name whose addition is effective on date ``T`` **is** a member for the whole
    session of ``T``;
  * a name whose deletion is effective on date ``T`` is **not** a member on ``T``.

This is the same convention used by ``backtest-look-ahead-in-universe-selection``;
see ``references/standards.md`` for the sourcing and for the inclusive-end-date
vendor hazard.

Time axis
---------
This engine resolves the **effective** (valid-time) axis only. It does not model the
*knowledge* axis -- when a membership change was announced and therefore knowable.
Announcement precedes effect by days to weeks for scheduled changes, and a strategy
that must not act on an unannounced change needs the knowledge axis too; that is the
job of ``backtest-look-ahead-in-universe-selection``.

The engine is deterministic and side-effect free: every event is validated and
snapshotted at ingest, it never mutates the events it is given, and repeated queries
against the same event set return identical results regardless of the order the
events were ingested.
"""
from dataclasses import dataclass, field
from datetime import date
import logging
import re
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

#: Accepted event types. Anything else is rejected rather than silently treated as
#: "not an addition", which would drop the symbol out of the universe unnoticed.
ADDITION = "ADDITION"
DELETION = "DELETION"
VALID_EVENT_TYPES = frozenset({ADDITION, DELETION})

#: Ordering applied to same-day events for the same security when the feed supplies no
#: explicit ``sequence``. A deletion is applied before an addition, so a same-day
#: delete/re-add pair resolves to "member". Same-day pairs are a data anomaly either
#: way and are always reported in ``PITIndexReport.data_quality_warnings``.
_EVENT_RANK: Mapping[str, int] = {DELETION: 0, ADDITION: 1}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DateLike = Union[str, date]


class IndexConstituentError(ValueError):
    """Raised when constituent events or a query cannot be interpreted unambiguously."""


def _parse_date(value: DateLike, field_name: str) -> date:
    """Parses a strict ``YYYY-MM-DD`` string, or passes a ``date`` through.

    Strictness is the point. The previous implementation compared raw strings, so a
    feed emitting ``'2020-1-5'`` or ``'01/05/2020'`` produced a silently wrong
    universe instead of an error -- lexicographic order is date order only for
    zero-padded ISO-8601.
    """
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise IndexConstituentError(
            f"{field_name} must be an ISO-8601 'YYYY-MM-DD' string or a datetime.date, "
            f"got {value!r}. Zero-padded ISO dates are required: date ordering is "
            f"otherwise not well defined."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise IndexConstituentError(
            f"{field_name} {value!r} is not a real calendar date"
        ) from exc


@dataclass
class PointInTimeIndexConstituentTrackingConfig:
    """Engine configuration.

    ``index_name`` is a documentation default only; every query names the index it
    resolves, so one engine instance can hold events for many indices.
    """
    enabled: bool = True
    index_name: str = "SP500"


@dataclass
class IndexConstituentEvent:
    """One membership change on the effective (valid-time) axis.

    Attributes:
        index_name: Index the change applies to, e.g. ``'SP500'``. Matched case-insensitively.
        symbol: Exchange ticker as of the event.
        event_type: ``'ADDITION'`` or ``'DELETION'``. Case-insensitive; anything else raises.
        effective_date: ``YYYY-MM-DD`` (or ``date``) the change takes effect, read as
            *prior to the open* of that session.
        weight: Index weight carried on this event, if the feed supplies one. See
            ``PITIndexReport.constituent_weights`` for what this does and does not mean.
        security_id: Stable issuer/security identifier (CUSIP, SEDOL, CRSP PERMNO,
            FIGI...). Supply it whenever available: tickers are reused across issuers,
            and without it two different companies that held the same ticker collapse
            into a single membership timeline. Membership is keyed by ``security_id``
            when present and by ``symbol`` otherwise.
        sequence: Optional feed-supplied ordinal, used to order events that share an
            ``effective_date`` for the same security. Supply it for all such events or
            none; a partial mix is reported as a data-quality warning.

    Events are snapshotted at ingest, so mutating an event object afterwards does not
    change any already-ingested membership timeline.
    """
    index_name: str
    symbol: str
    event_type: str
    effective_date: DateLike
    weight: float = 0.0
    security_id: Optional[str] = None
    sequence: Optional[int] = None


@dataclass
class PITIndexQuery:
    """As-of membership query. ``as_of_date`` is a ``YYYY-MM-DD`` string or a ``date``."""
    index_name: str
    as_of_date: DateLike


@dataclass
class PITIndexReport:
    """Result of a point-in-time membership query.

    Attributes:
        active_constituents: Ticker symbols that were index members on ``as_of_date``,
            sorted. A symbol appears once per distinct membership key, so a reused
            ticker held by two members simultaneously appears twice.
        survivorship_bias_ghost_count: Number of PIT members absent from the current
            static universe. ``None`` when no current universe was supplied -- which is
            *not* the same as zero, and must not be read as "audited, no bias found".
        ghost_symbols: The names behind that count.
        constituent_weights: Weight carried on each member's most recent
            membership-affecting event, keyed by membership key. This is **not** a
            point-in-time index weight: weights drift with price and are reset at
            rebalances that emit no addition or deletion event. Treat it as provenance
            metadata, not as a portfolio weight.
        status: ``'UNIVERSE_RESOLVED_PIT'``, ``'INDEX_NOT_FOUND'`` (no events for this
            index at all) or ``'ENGINE_DISABLED'``.
        data_quality_warnings: Non-fatal anomalies found while resolving this query.
    """
    index_name: str
    as_of_date: str
    active_constituents: List[str]
    total_active_count: int
    survivorship_bias_ghost_count: Optional[int]
    status: str
    audit_notes: str
    ghost_symbols: List[str] = field(default_factory=list)
    constituent_weights: Dict[str, float] = field(default_factory=dict)
    data_quality_warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ResolvedEvent:
    """Validated, immutable snapshot of one ingested event."""
    membership_key: str
    symbol: str
    event_type: str
    effective_date: date
    weight: float
    sequence: Optional[int]
    ingest_order: int

    @property
    def same_day_rank(self) -> int:
        """Ordering rank among events sharing an effective date for the same security."""
        return self.sequence if self.sequence is not None else _EVENT_RANK[self.event_type]

    @property
    def sort_key(self) -> Tuple[date, int, int]:
        return (self.effective_date, self.same_day_rank, self.ingest_order)


class PointInTimeIndexConstituentTrackingEngine:
    """Point-in-time index constituent tracker.

    Ingest addition/deletion events with :meth:`insert_events`, then resolve the
    membership set for any historical date with :meth:`query_pit_universe`.
    """

    def __init__(
        self, config: Optional[PointInTimeIndexConstituentTrackingConfig] = None
    ) -> None:
        self.config = config or PointInTimeIndexConstituentTrackingConfig()
        self.events: List[IndexConstituentEvent] = []
        # Validated snapshots of `self.events`, bucketed by upper-cased index name.
        self._resolved: Dict[str, List[_ResolvedEvent]] = {}
        self._ingest_counter = 0

    def insert_events(self, events: Iterable[IndexConstituentEvent]) -> None:
        """Ingests and validates historical index constituent events.

        Validation is eager: a malformed date or an unrecognised ``event_type`` raises
        at ingest, where the offending record is still identifiable, rather than
        silently distorting every later query. The batch is all-or-nothing -- nothing
        is ingested if any event in it is invalid.

        Raises:
            IndexConstituentError: On an empty index name or symbol, an unrecognised
                ``event_type``, a non-ISO-8601 ``effective_date``, or a non-numeric
                ``weight``.
        """
        batch = list(events)  # materialise, so a one-shot iterable is not half-consumed
        staged: List[Tuple[str, _ResolvedEvent]] = []
        counter = self._ingest_counter
        for position, ev in enumerate(batch):
            index_name = str(ev.index_name or "").strip()
            if not index_name:
                raise IndexConstituentError(f"event #{position}: index_name must be non-empty")
            symbol = str(ev.symbol or "").strip()
            if not symbol:
                raise IndexConstituentError(f"event #{position}: symbol must be non-empty")
            event_type = str(ev.event_type).strip().upper()
            if event_type not in VALID_EVENT_TYPES:
                raise IndexConstituentError(
                    f"event #{position} ({symbol}): event_type {ev.event_type!r} is not one "
                    f"of {sorted(VALID_EVENT_TYPES)}. An unrecognised type would silently "
                    f"remove the symbol from every point-in-time universe."
                )
            effective_date = _parse_date(
                ev.effective_date, f"event #{position} ({symbol}) effective_date"
            )
            try:
                weight = float(ev.weight)
            except (TypeError, ValueError) as exc:
                raise IndexConstituentError(
                    f"event #{position} ({symbol}): weight {ev.weight!r} is not numeric"
                ) from exc
            security_id = str(ev.security_id).strip() if ev.security_id is not None else ""
            staged.append(
                (
                    index_name.upper(),
                    _ResolvedEvent(
                        membership_key=(security_id or symbol).upper(),
                        symbol=symbol.upper(),
                        event_type=event_type,
                        effective_date=effective_date,
                        weight=weight,
                        sequence=None if ev.sequence is None else int(ev.sequence),
                        ingest_order=counter,
                    ),
                )
            )
            counter += 1

        for index_upper, resolved in staged:
            self._resolved.setdefault(index_upper, []).append(resolved)
        self.events.extend(batch)
        self._ingest_counter = counter
        logger.debug(
            "Ingested %d constituent event(s); %d total.", len(staged), len(self.events)
        )

    def query_pit_universe(
        self,
        query: PITIndexQuery,
        current_static_universe: Optional[Set[str]] = None,
    ) -> PITIndexReport:
        """Resolves index membership as it stood on ``query.as_of_date``.

        A security is a member when its latest membership-affecting event at or before
        ``as_of_date`` is an ``ADDITION`` -- i.e. ``add_date <= T < del_date``, matching
        the "effective prior to the open" convention documented at module level.

        Args:
            query: Index and as-of date to resolve.
            current_static_universe: Today's membership, used only to count point-in-time
                members that are missing from it ("ghosts"). Omit it and the ghost count
                is reported as ``None`` rather than ``0``.

        Returns:
            A :class:`PITIndexReport`. ``status`` is ``'INDEX_NOT_FOUND'`` when no events
            were ever ingested for the index -- an empty universe from an unknown index
            is a configuration error, not a legitimately empty index.

        Raises:
            IndexConstituentError: If ``as_of_date`` is not a strict ISO-8601 date.
        """
        as_of = _parse_date(query.as_of_date, "query.as_of_date")
        as_of_iso = as_of.isoformat()
        idx_name_upper = str(query.index_name or "").strip().upper()

        if not self.config.enabled:
            return PITIndexReport(
                index_name=idx_name_upper,
                as_of_date=as_of_iso,
                active_constituents=[],
                total_active_count=0,
                survivorship_bias_ghost_count=None,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled; no universe was resolved.",
            )

        index_events = self._resolved.get(idx_name_upper)
        if not index_events:
            notes = (
                f"PIT INDEX UNIVERSE NOT RESOLVED [{idx_name_upper} as of {as_of_iso}]: "
                f"no constituent events have been ingested for this index."
            )
            logger.warning(notes)
            return PITIndexReport(
                index_name=idx_name_upper,
                as_of_date=as_of_iso,
                active_constituents=[],
                total_active_count=0,
                survivorship_bias_ghost_count=None,
                status="INDEX_NOT_FOUND",
                audit_notes=notes,
            )

        # Group events at or before the as-of date by membership key.
        by_key: Dict[str, List[_ResolvedEvent]] = {}
        for resolved in index_events:
            if resolved.effective_date <= as_of:
                by_key.setdefault(resolved.membership_key, []).append(resolved)

        warnings: List[str] = []
        active: List[Tuple[str, str, float]] = []  # (symbol, membership key, weight)

        for _key, group in by_key.items():
            ordered = sorted(group, key=lambda r: r.sort_key)
            decisive = ordered[-1]
            warnings.extend(self._same_day_warnings(ordered, decisive))
            if decisive.event_type == ADDITION:
                active.append((decisive.symbol, decisive.membership_key, decisive.weight))

        active.sort()
        active_symbols = [symbol for symbol, _key, _weight in active]
        weights = {key: weight for _symbol, key, weight in active}

        ghost_symbols: List[str] = []
        ghost_count: Optional[int] = None
        if current_static_universe is not None:
            current_upper = {str(s).strip().upper() for s in current_static_universe}
            ghost_symbols = sorted({s for s in active_symbols if s not in current_upper})
            ghost_count = len(ghost_symbols)

        ghost_text = (
            "not audited (no current universe supplied)"
            if ghost_count is None
            else str(ghost_count)
        )
        notes = (
            f"PIT INDEX UNIVERSE RESOLVED [{idx_name_upper} as of {as_of_iso}]: "
            f"Total Active = {len(active_symbols)} symbols. "
            f"Survivorship Bias Ghost Symbols (Delisted/Removed) = {ghost_text}."
        )
        if warnings:
            notes += f" Data-quality warnings: {len(warnings)}."
            for warning in warnings:
                logger.warning("[%s as of %s] %s", idx_name_upper, as_of_iso, warning)
        logger.info(notes)

        return PITIndexReport(
            index_name=idx_name_upper,
            as_of_date=as_of_iso,
            active_constituents=active_symbols,
            total_active_count=len(active_symbols),
            survivorship_bias_ghost_count=ghost_count,
            status="UNIVERSE_RESOLVED_PIT",
            audit_notes=notes,
            ghost_symbols=ghost_symbols,
            constituent_weights=weights,
            data_quality_warnings=warnings,
        )

    @staticmethod
    def _same_day_warnings(
        ordered: List[_ResolvedEvent], decisive: _ResolvedEvent
    ) -> List[str]:
        """Flags ambiguity among the events that decided this security's final state."""
        same_day = [r for r in ordered if r.effective_date == decisive.effective_date]
        if len(same_day) < 2:
            return []

        warnings: List[str] = []
        day = decisive.effective_date.isoformat()
        sequences = [r.sequence for r in same_day]
        fully_sequenced = all(s is not None for s in sequences)
        partially_sequenced = not fully_sequenced and any(s is not None for s in sequences)

        types = {r.event_type for r in same_day}
        if len(types) > 1:
            rule = (
                "the supplied `sequence` ordering"
                if fully_sequenced
                else "deletion applied before addition"
            )
            warnings.append(
                f"{decisive.symbol}: conflicting {'/'.join(sorted(types))} events share "
                f"effective_date {day}; resolved as {decisive.event_type} using {rule}."
            )
        if partially_sequenced:
            warnings.append(
                f"{decisive.symbol}: events on {day} mix supplied and missing `sequence` "
                f"values; ordering falls back to event type."
            )
        return warnings
