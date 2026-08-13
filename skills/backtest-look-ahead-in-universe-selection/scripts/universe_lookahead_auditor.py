"""
backtest-look-ahead-in-universe-selection: point-in-time universe membership auditor.

Audits the constituent records a backtest used for one rebalance snapshot against two
independent time axes, following the same knowledge-time / valid-time split used by
`backtest-database-schema-for-point-in-time-queries`:

  * knowledge axis (`data_publication_date`) -- when the membership decision *and* the
    ranking inputs behind it became publicly available;
  * effective axis (`added_date`, `removed_date`) -- when index membership actually
    started and ended.

Timestamp conventions enforced here (see `references/standards.md` for the sourcing):

  * Membership is the half-open interval ``[added_date, removed_date)``. Index providers
    make constituent changes effective *prior to the opening of trading* on the effective
    date, so a name added on date D is a member for the whole session of D, and a name
    removed on date D is not a member on D.
  * Effective dates are read at start of day. Publication timestamps are read at *end* of
    day when they are date-granular (exactly midnight), because a date-only stamp cannot
    establish that the data existed before that session's decision instant. Reading it as
    midnight would silently authorise same-session look-ahead.

The auditor is deterministic and side-effect free; it never mutates the records it is
given.
"""
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
import logging
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Universes strictly larger than this with zero closed membership intervals trip the
#: survivorship heuristic. This is an operational tripwire, not an empirically derived
#: threshold; tune it per index via the constructor.
DEFAULT_SURVIVORSHIP_WARNING_THRESHOLD = 50

_END_OF_DAY = time(23, 59, 59, 999999)
_MIDNIGHT = time(0, 0, 0, 0)


class UniverseAuditError(ValueError):
    """Raised when the snapshot instant or a constituent record is structurally invalid.

    Structural problems (wrong types, mixed timezone awareness, a removal that predates
    its own addition) are raised rather than reported as findings: they mean the audit
    itself cannot be trusted, so failing loudly is safer than emitting a clean report.
    """


class UniverseFindingType(str, Enum):
    """Machine-readable classification for every finding this auditor can emit.

    The values double as the human-readable prefix of each rendered message, so callers
    may gate on the enum instead of substring-matching free text.
    """

    # Hard violations: the snapshot provably used information it could not have had.
    LOOKAHEAD_LEAK = "Lookahead Leak"
    FUTURE_ADDITION = "Future Addition"
    ZOMBIE_ASSET = "Zombie Asset"
    DUPLICATE_SYMBOL = "Duplicate Symbol"
    # Heuristic warnings: suggestive of a bad data source, not proof of a leak.
    SURVIVORSHIP_BIAS = "Survivorship Bias"
    VACUOUS_PUBLICATION_DATES = "Vacuous Publication Dates"


_VIOLATION_TYPES = frozenset(
    {
        UniverseFindingType.LOOKAHEAD_LEAK,
        UniverseFindingType.FUTURE_ADDITION,
        UniverseFindingType.ZOMBIE_ASSET,
        UniverseFindingType.DUPLICATE_SYMBOL,
    }
)


@dataclass(frozen=True)
class AuditFinding:
    """One classified finding. `symbol` is None for universe-level findings."""

    finding_type: UniverseFindingType
    symbol: Optional[str]
    message: str

    @property
    def is_violation(self) -> bool:
        return self.finding_type in _VIOLATION_TYPES


@dataclass
class ConstituentRecord:
    """One index membership interval as the backtest believed it on the snapshot date.

    Args:
        symbol: Instrument identifier. Compared case-insensitively for duplicate
            detection; ticker reuse across different issuers must be disambiguated
            upstream (see `reference-data-symbol-mapping-across-vendors`).
        added_date: Instant membership became effective, read at start of day.
        removed_date: Instant membership ended, read at start of day, exclusive.
            `None` means still a member as far as the record knows.
        data_publication_date: Latest instant among *every* input used to justify
            membership at this snapshot -- the index provider's announcement and the
            as-of stamp of any ranking data (market cap, float, ADV). Set this to the
            announcement/publication instant, never to `added_date`: index changes are
            announced days to weeks before they take effect, and copying the effective
            date into this field makes the look-ahead check vacuous.
    """

    symbol: str
    added_date: datetime
    removed_date: Optional[datetime]
    data_publication_date: datetime


@dataclass
class AuditResult:
    """Outcome of one snapshot audit.

    Attributes:
        is_clean: True only when there are no violations *and* no heuristic warnings.
            Use `has_violations` for a CI gate that should not fail on a heuristic.
        lookahead_violations: Rendered messages for hard violations.
        survivorship_warnings: Rendered messages for heuristic warnings.
        findings: The same findings, classified by `UniverseFindingType`.
    """

    is_clean: bool
    lookahead_violations: List[str]
    survivorship_warnings: List[str]
    findings: List[AuditFinding] = field(default_factory=list)

    @property
    def has_violations(self) -> bool:
        """True when at least one hard violation was found, ignoring heuristics."""
        return bool(self.lookahead_violations)


class UniverseLookaheadAuditor:
    """
    Audits universe constituent data to prevent Lookahead Bias (using future data)
    and Survivorship Bias (implicitly dropping delisted assets).
    """

    def __init__(
        self,
        survivorship_warning_threshold: int = DEFAULT_SURVIVORSHIP_WARNING_THRESHOLD,
        date_granular_publication_is_end_of_day: bool = True,
    ) -> None:
        """
        Args:
            survivorship_warning_threshold: Universes strictly larger than this with no
                closed membership interval trip the survivorship heuristic.
            date_granular_publication_is_end_of_day: When True (default, fail-closed), a
                `data_publication_date` of exactly midnight is treated as date-granular
                and read as end of that day. Set False only when the stored midnight
                genuinely means "available at 00:00", which is rare for index data.
        """
        if survivorship_warning_threshold < 0:
            raise UniverseAuditError(
                f"survivorship_warning_threshold must be non-negative, got "
                f"{survivorship_warning_threshold}."
            )
        self.survivorship_warning_threshold = survivorship_warning_threshold
        self.date_granular_publication_is_end_of_day = date_granular_publication_is_end_of_day

    def audit_universe_snapshot(
        self,
        snapshot_date: datetime,
        constituents: Sequence[ConstituentRecord],
    ) -> AuditResult:
        """Audits one rebalance snapshot for look-ahead and survivorship bias.

        Args:
            snapshot_date: The *decision instant* at which the universe was frozen (for
                example 09:30 ET on the rebalance date), not the end of that session.
            constituents: The membership records the backtest actually used for that
                snapshot.

        Returns:
            An `AuditResult`. An empty `constituents` list audits clean; an empty
            universe is a strategy concern, not a look-ahead one.

        Raises:
            UniverseAuditError: If the snapshot instant or any record is structurally
                invalid, including mixed timezone-aware and naive timestamps.
        """
        # Materialise first: a one-shot iterable would be drained by validation and every
        # later pass would see an empty universe, reporting a silent false-clean.
        records = list(constituents)
        self._validate_inputs(snapshot_date, records)

        findings: List[AuditFinding] = []
        findings.extend(self._audit_duplicates(records))
        for record in records:
            findings.extend(self._audit_record(snapshot_date, record))
        findings.extend(self._audit_universe_heuristics(records))

        violations = [f.message for f in findings if f.is_violation]
        warnings = [f.message for f in findings if not f.is_violation]

        if violations:
            logger.warning(
                "Universe audit for %s: %d look-ahead violation(s) across %d constituents.",
                snapshot_date.isoformat(),
                len(violations),
                len(records),
            )
        else:
            logger.debug(
                "Universe audit for %s: clean across %d constituents (%d warning(s)).",
                snapshot_date.isoformat(),
                len(records),
                len(warnings),
            )

        return AuditResult(
            is_clean=(not violations and not warnings),
            lookahead_violations=violations,
            survivorship_warnings=warnings,
            findings=findings,
        )

    # ------------------------------------------------------------------ internals

    def _validate_inputs(
        self, snapshot_date: datetime, constituents: Sequence[ConstituentRecord]
    ) -> None:
        """Rejects inputs that would make the audit meaningless or crash mid-loop."""
        if not isinstance(snapshot_date, datetime):
            raise UniverseAuditError(
                f"snapshot_date must be a datetime (a date carries no decision instant), "
                f"got {type(snapshot_date).__name__}."
            )
        snapshot_is_aware = snapshot_date.tzinfo is not None

        for position, record in enumerate(constituents):
            if not isinstance(record, ConstituentRecord):
                raise UniverseAuditError(
                    f"constituents[{position}] must be a ConstituentRecord, got "
                    f"{type(record).__name__}."
                )
            if not isinstance(record.symbol, str) or not record.symbol.strip():
                raise UniverseAuditError(
                    f"constituents[{position}] has an empty or non-string symbol."
                )
            label = f"{record.symbol!r} (constituents[{position}])"

            for attr in ("added_date", "data_publication_date"):
                value = getattr(record, attr)
                if not isinstance(value, datetime):
                    raise UniverseAuditError(
                        f"{label}: {attr} must be a datetime, got {type(value).__name__}."
                    )
                self._require_matching_awareness(label, attr, value, snapshot_is_aware)

            if record.removed_date is not None:
                if not isinstance(record.removed_date, datetime):
                    raise UniverseAuditError(
                        f"{label}: removed_date must be a datetime or None, got "
                        f"{type(record.removed_date).__name__}."
                    )
                self._require_matching_awareness(
                    label, "removed_date", record.removed_date, snapshot_is_aware
                )
                if record.removed_date < record.added_date:
                    raise UniverseAuditError(
                        f"{label}: removed_date {record.removed_date.isoformat()} precedes "
                        f"added_date {record.added_date.isoformat()}."
                    )

    @staticmethod
    def _require_matching_awareness(
        label: str, attr: str, value: datetime, snapshot_is_aware: bool
    ) -> None:
        """Blocks the TypeError that naive/aware comparison would raise mid-audit."""
        if (value.tzinfo is not None) != snapshot_is_aware:
            expected = "timezone-aware" if snapshot_is_aware else "timezone-naive"
            raise UniverseAuditError(
                f"{label}: {attr} must be {expected} to match snapshot_date. Normalise "
                f"every timestamp to a single convention (UTC is recommended) before auditing."
            )

    def _effective_publication_instant(self, record: ConstituentRecord) -> datetime:
        """Resolves when the selection data was genuinely usable.

        A publication timestamp of exactly midnight is date-granular: the true intraday
        instant was discarded upstream, so it cannot prove availability at any particular
        point in that session. The conservative reading is end of that day.
        """
        published = record.data_publication_date
        if self.date_granular_publication_is_end_of_day and published.time() == _MIDNIGHT:
            return published.replace(
                hour=_END_OF_DAY.hour,
                minute=_END_OF_DAY.minute,
                second=_END_OF_DAY.second,
                microsecond=_END_OF_DAY.microsecond,
            )
        return published

    def _audit_record(
        self, snapshot_date: datetime, record: ConstituentRecord
    ) -> List[AuditFinding]:
        findings: List[AuditFinding] = []
        snapshot_iso = snapshot_date.isoformat()

        # 1. Lookahead Bias: the data justifying membership was not yet available.
        available_at = self._effective_publication_instant(record)
        if available_at > snapshot_date:
            granularity_note = (
                "; date-granular timestamp read as end of day"
                if available_at != record.data_publication_date
                else ""
            )
            findings.append(
                AuditFinding(
                    UniverseFindingType.LOOKAHEAD_LEAK,
                    record.symbol,
                    f"[{record.symbol}] Lookahead Leak: selection data became available at "
                    f"{available_at.isoformat()} (publication timestamp "
                    f"{record.data_publication_date.isoformat()}{granularity_note}), after the "
                    f"{snapshot_iso} snapshot decision instant.",
                )
            )

        # 2. Point-in-Time Logic: membership must have been effective at the snapshot.
        if record.added_date > snapshot_date:
            findings.append(
                AuditFinding(
                    UniverseFindingType.FUTURE_ADDITION,
                    record.symbol,
                    f"[{record.symbol}] Future Addition: membership became effective "
                    f"{record.added_date.isoformat()}, after the {snapshot_iso} snapshot.",
                )
            )

        if record.removed_date is not None and record.removed_date <= snapshot_date:
            findings.append(
                AuditFinding(
                    UniverseFindingType.ZOMBIE_ASSET,
                    record.symbol,
                    f"[{record.symbol}] Zombie Asset: membership ended "
                    f"{record.removed_date.isoformat()} (effective prior to that session's "
                    f"open) but the name was still included in the {snapshot_iso} snapshot.",
                )
            )

        return findings

    @staticmethod
    def _audit_duplicates(constituents: Sequence[ConstituentRecord]) -> List[AuditFinding]:
        """Flags repeated symbols, which double-weight a name in the constructed universe."""
        counts: Dict[str, int] = {}
        for record in constituents:
            key = record.symbol.strip().upper()
            counts[key] = counts.get(key, 0) + 1

        return [
            AuditFinding(
                UniverseFindingType.DUPLICATE_SYMBOL,
                symbol,
                f"[{symbol}] Duplicate Symbol: appears {count} times in the snapshot "
                f"constituent list, double-weighting the name. Overlapping membership "
                f"intervals must be merged before the universe is built.",
            )
            for symbol, count in sorted(counts.items())
            if count > 1
        ]

    def _audit_universe_heuristics(
        self, constituents: Sequence[ConstituentRecord]
    ) -> List[AuditFinding]:
        """Universe-level heuristics. These are suggestive, never proof."""
        findings: List[AuditFinding] = []
        if not constituents:
            return findings

        has_closed_interval = any(c.removed_date is not None for c in constituents)
        if not has_closed_interval and len(constituents) > self.survivorship_warning_threshold:
            findings.append(
                AuditFinding(
                    UniverseFindingType.SURVIVORSHIP_BIAS,
                    None,
                    "WARNING: Large universe with zero delisted/removed assets detected. "
                    "High probability of Survivorship Bias (using current active "
                    "constituents only). Expected for a snapshot near the database build "
                    "date, where nothing has been removed yet; investigate for any "
                    "historical snapshot.",
                )
            )

        if all(c.data_publication_date == c.added_date for c in constituents):
            findings.append(
                AuditFinding(
                    UniverseFindingType.VACUOUS_PUBLICATION_DATES,
                    None,
                    "WARNING: Vacuous Publication Dates -- every record's "
                    "data_publication_date equals its added_date, so the look-ahead check "
                    "cannot fail by construction. Scheduled index changes are announced "
                    "before they take effect, so populate data_publication_date from the "
                    "announcement and the as-of stamp of the ranking data instead. "
                    "Unscheduled removals (bankruptcy, merger completion) are a legitimate "
                    "exception where the two dates coincide.",
                )
            )

        return findings
