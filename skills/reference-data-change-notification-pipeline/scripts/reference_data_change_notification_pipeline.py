"""Field-level reference-data change detection with severity-routed notification.

This module answers one question: *given the instrument-master record for a single
instrument before and after a refresh, which fields moved, how badly does each move
break a downstream trading system, and who has to be told?*

Four things about that question are easy to get wrong, and this module is built
around them.

**Absent is not the same as null.** A field missing from a snapshot and a field
present with the value ``None`` mean different things: the first says the vendor
stopped publishing it, the second says the vendor published "unknown". A prior
revision of this module read both with ``dict.get(field)``, so a record carrying
``{"isin": None}`` on both sides compared equal to one where ``isin`` had been
dropped entirely, and a genuine field removal was reported as no change at all.
Presence is now tracked explicitly and surfaced on every notification as
``old_present`` / ``new_present``.

**Severity is not binary, and "not on the critical list" is not "harmless".**
Identity and routing fields (``symbol``, ``exchange``, ``isin``, ...) send an order
to the *wrong instrument or wrong venue*. Order-construction fields (``lot_size``,
``tick_size``, ``contract_multiplier``, ...) send a *malformed* order to the right
one — rejected or mis-sized, not misrouted. A prior revision classified everything
outside the critical set as ``INFO``, which put ``lot_size`` — a field whose staleness
the skill's own documentation named as a failure mode — in the same bucket as a
changed free-text description. The three-level scale here keeps that distinction.

**A snapshot diff is not a corporate-action calendar.** This compares two states that
already exist. It has no notion of an *effective date*, and reference-data changes
routinely carry one: ISO 10383 MIC modifications, for instance, are published on the
second Monday of the month and become effective on the fourth
(https://www.iso20022.org/market-identifier-codes). If your loader writes an
announced-but-not-yet-effective value into the snapshot, this module will correctly
report a change that must not be acted on yet. Effective-date sequencing belongs
upstream — see ``corporate-action-event-calendar-integration``.

**One dead notification sink must not silence the others.** Routing therefore isolates
failures per consumer and *returns* them rather than raising: a risk engine that is
down cannot be allowed to stop the OMS from hearing that a symbol was renamed. The
caller is responsible for inspecting :attr:`NotificationDispatchResult.failures`.

Standard library only. Requires Python 3.9+.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Severity scale --------------------------------------------------------
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

# Ordered low to high. Used for threshold comparison and for escalation; never
# compare severity strings directly.
_SEVERITY_RANK = {SEVERITY_INFO: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}

# --- Change types ----------------------------------------------------------
CHANGE_ADDED = "ADDED"
CHANGE_MODIFIED = "MODIFIED"
CHANGE_REMOVED = "REMOVED"

# --- Report statuses -------------------------------------------------------
STATUS_CHANGES_DETECTED = "CHANGES_DETECTED"
STATUS_NO_CHANGES = "NO_CHANGES"
STATUS_ENGINE_DISABLED = "ENGINE_DISABLED"

# Identity and routing fields. A wrong value here does not produce a bad order, it
# produces an order against the wrong instrument or the wrong venue. Field names are
# matched case-insensitively, so a vendor that publishes "Symbol" is still caught.
DEFAULT_CRITICAL_FIELDS: FrozenSet[str] = frozenset(
    {"symbol", "exchange", "mic", "status", "currency", "isin", "cusip", "sedol", "figi"}
)

# Order-construction fields. A wrong value here produces a malformed or mis-sized
# order against the correct instrument: rejection, odd-lot, or an unintended notional.
DEFAULT_WARNING_FIELDS: FrozenSet[str] = frozenset(
    {
        "lot_size",
        "tick_size",
        "min_order_qty",
        "max_order_qty",
        "contract_multiplier",
        "price_precision",
        "quantity_precision",
        "expiry",
        "strike",
        "settlement_date",
    }
)


class ReferenceDataChangeError(ValueError):
    """Base class for every error raised by this module.

    Subclasses ``ValueError`` so callers written against a bare ``except ValueError``
    keep working unchanged.
    """


class ChangeDetectionConfigError(ReferenceDataChangeError):
    """Raised when a :class:`ReferenceDataChangeNotificationPipelineConfig` is unusable."""


class SnapshotError(ReferenceDataChangeError):
    """Raised when a snapshot pair cannot support a meaningful field-level diff."""


class _Missing:
    """Sentinel for "this field was not present in the snapshot at all"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<absent>"


MISSING = _Missing()


def _normalize_field_set(values: Iterable[str], label: str) -> FrozenSet[str]:
    """Casefold and validate a configured field-name set."""
    normalized = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ChangeDetectionConfigError(
                f"{label} must contain only field-name strings; got {type(raw).__name__}."
            )
        name = raw.strip().casefold()
        if not name:
            raise ChangeDetectionConfigError(f"{label} must not contain empty field names.")
        normalized.add(name)
    return frozenset(normalized)


def _validate_severity(value: str, label: str) -> str:
    if value not in _SEVERITY_RANK:
        raise ChangeDetectionConfigError(
            f"{label} must be one of {sorted(_SEVERITY_RANK, key=_SEVERITY_RANK.get)}; got {value!r}."
        )
    return value


def _values_equal(old: Any, new: Any) -> bool:
    """Return True when two field values are the same reference-data value.

    Plain ``==``. Two consequences worth stating, because both bite in production:

    * ``"100"`` and ``100`` are **not** equal, so a vendor that switches a field from
      string to numeric raises a change on every instrument in the universe. Canonicalize
      types in the loader, not here — silently coercing them would hide a real schema
      change under a type cast.
    * ``100`` and ``100.0`` *are* equal and are not reported.

    ``float("nan")`` never equals itself, so a vendor that publishes NaN for a missing
    numeric raises the same change on every cycle forever. Map NaN to ``None`` in the
    loader. (The identity short-circuit below means the *same* NaN object on both sides
    does compare equal — do not rely on that; it depends on how the snapshots were built.)

    A value whose ``__eq__`` raises or returns something that cannot be interpreted as a
    bool is treated as changed, on the principle that an uncomparable value is not
    evidence of stability.
    """
    if old is new:
        return True
    try:
        return bool(old == new)
    except Exception:  # noqa: BLE001 - any comparison failure means "cannot prove equal"
        logger.debug("Uncomparable reference-data values; treating as changed.", exc_info=True)
        return False


def _render(value: Any, present: bool) -> str:
    """Deterministic rendering of a field value for the audit change key.

    A hostile ``__repr__`` must not be able to raise out of a change key: the key is
    read inside the delivery-failure handler in :meth:`route_notifications`, so an
    exception here would abort the very dispatch loop that exists to survive bad
    downstream behaviour.
    """
    if not present:
        return repr(MISSING)
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - an unrenderable value still needs a stable key
        logger.debug("Field value repr() failed; using a type-based key component.", exc_info=True)
        return f"<unrepresentable {type(value).__name__}>"


@dataclass
class ReferenceDataChangeNotificationPipelineConfig:
    """Configuration for :class:`ReferenceDataChangeNotificationPipelineEngine`.

    Args:
        enabled: When False the engine short-circuits and reports ``ENGINE_DISABLED``.
            Detection is *not* silently skipped — the status makes the gap auditable.
        critical_fields: Identity/routing field names. Matched case-insensitively.
        warning_fields: Order-construction field names. Matched case-insensitively.
        removal_min_severity: Floor severity applied when a field disappears. A vendor
            dropping a field is a data-quality incident regardless of which field it is,
            so the default floor is ``WARNING`` rather than ``INFO``.
        treat_missing_as_removal: True (default) requires ``after`` to be a **full**
            snapshot: any field present in ``before`` and absent from ``after`` is a
            removal. Set False only when ``after`` is a partial/delta payload, in which
            case absent fields are ignored and removals can never be detected.
    """

    enabled: bool = True
    critical_fields: FrozenSet[str] = field(default_factory=lambda: DEFAULT_CRITICAL_FIELDS)
    warning_fields: FrozenSet[str] = field(default_factory=lambda: DEFAULT_WARNING_FIELDS)
    removal_min_severity: str = SEVERITY_WARNING
    treat_missing_as_removal: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.critical_fields, str) or isinstance(self.warning_fields, str):
            raise ChangeDetectionConfigError(
                "critical_fields/warning_fields must be collections of field names, not a string."
            )
        self.critical_fields = _normalize_field_set(self.critical_fields, "critical_fields")
        self.warning_fields = _normalize_field_set(self.warning_fields, "warning_fields")
        self.removal_min_severity = _validate_severity(
            self.removal_min_severity, "removal_min_severity"
        )
        overlap = self.critical_fields & self.warning_fields
        if overlap:
            raise ChangeDetectionConfigError(
                "A field cannot be both critical and warning; ambiguous classification for: "
                f"{sorted(overlap)}."
            )


@dataclass(frozen=True)
class ChangeNotification:
    """One detected field-level mutation on one instrument.

    ``old_present`` / ``new_present`` disambiguate an absent field from a field
    published with the value ``None``; when a side is absent its value is ``None``.
    """

    instrument_id: str
    field_name: str
    old_value: Any
    new_value: Any
    old_present: bool
    new_present: bool
    change_type: str
    severity: str
    as_of: Optional[str] = None

    @property
    def change_key(self) -> str:
        """Stable identity of this change, for downstream de-duplication.

        A snapshot pipeline that re-compares the same pair (a re-run, a replayed file,
        a failover) produces the same key, so consumers can suppress duplicate alerts
        without the engine holding state. ``as_of`` is deliberately excluded: the same
        change observed at two times is one change.
        """
        return "|".join(
            (
                self.instrument_id,
                self.field_name,
                self.change_type,
                _render(self.old_value, self.old_present),
                _render(self.new_value, self.new_present),
            )
        )


@dataclass
class ReferenceDataChangeReport:
    """Outcome of one before/after comparison for one instrument."""

    instrument_id: str
    total_changes: int
    critical_changes: int
    warning_changes: int
    info_changes: int
    notifications: List[ChangeNotification]
    status: str
    audit_notes: str
    max_severity: Optional[str] = None
    as_of: Optional[str] = None


@dataclass(frozen=True)
class NotificationConsumer:
    """A downstream subscriber for change notifications.

    Args:
        name: Unique consumer name, used in delivery failure records.
        callback: Invoked once per qualifying notification. Must be side-effect safe
            to call again: routing makes no delivery guarantee beyond "attempted once".
        min_severity: Lowest severity this consumer receives. A risk engine typically
            subscribes at ``CRITICAL``; a data-quality dashboard at ``INFO``.
    """

    name: str
    callback: Callable[[ChangeNotification], None]
    min_severity: str = SEVERITY_INFO


@dataclass(frozen=True)
class DeliveryFailure:
    """One consumer callback that raised while being handed one notification."""

    consumer_name: str
    change_key: str
    error: str


@dataclass
class NotificationDispatchResult:
    """Outcome of routing one report to a set of consumers.

    ``failures`` being non-empty means some downstream system did **not** learn about a
    change it subscribes to. That is an incident, not a warning to be logged and
    dropped — the caller must retry or escalate.
    """

    delivered: int
    skipped_below_threshold: int
    failures: List[DeliveryFailure]

    @property
    def all_delivered(self) -> bool:
        return not self.failures

    @property
    def failed_consumers(self) -> List[str]:
        seen: List[str] = []
        for failure in self.failures:
            if failure.consumer_name not in seen:
                seen.append(failure.consumer_name)
        return seen


class ReferenceDataChangeNotificationPipelineEngine:
    """Compares instrument reference-data snapshots and routes the resulting alerts.

    The engine is stateless across calls: the same snapshot pair always produces the
    same report, in the same order, with the same change keys.
    """

    def __init__(
        self, config: Optional[ReferenceDataChangeNotificationPipelineConfig] = None
    ) -> None:
        self.config = config or ReferenceDataChangeNotificationPipelineConfig()

    # -- classification ----------------------------------------------------
    def classify_severity(self, field_name: str, change_type: str) -> str:
        """Return the severity for a change to ``field_name``.

        Identity/routing fields are ``CRITICAL``, order-construction fields are
        ``WARNING``, anything unrecognized is ``INFO``. A removal is then floored at
        ``config.removal_min_severity``: losing a field is never merely informational,
        whatever the field is. An *addition* is not escalated — new data arriving is not
        the same risk as existing data disappearing.
        """
        key = field_name.strip().casefold()
        if key in self.config.critical_fields:
            severity = SEVERITY_CRITICAL
        elif key in self.config.warning_fields:
            severity = SEVERITY_WARNING
        else:
            severity = SEVERITY_INFO

        if change_type == CHANGE_REMOVED:
            floor = self.config.removal_min_severity
            if _SEVERITY_RANK[floor] > _SEVERITY_RANK[severity]:
                severity = floor
        return severity

    # -- detection ---------------------------------------------------------
    def detect_changes(
        self,
        instrument_id: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        as_of: Optional[str] = None,
    ) -> ReferenceDataChangeReport:
        """Compare two snapshots of one instrument and classify every field-level change.

        Args:
            instrument_id: Stable internal key for the instrument. Use a persistent
                identifier, never the ticker — the ticker is one of the fields whose
                change this method exists to detect.
            before: Previous full snapshot as a field-name -> value mapping.
            after: Current snapshot. Must be a **full** snapshot unless
                ``config.treat_missing_as_removal`` is False.
            as_of: Optional caller-supplied observation timestamp, recorded verbatim on
                the report and on every notification for audit. The engine never reads a
                clock, so results stay reproducible in replay.

        Returns:
            A :class:`ReferenceDataChangeReport` with notifications ordered by field name.

        Raises:
            SnapshotError: ``instrument_id`` is blank, a snapshot is not a mapping, or a
                snapshot has a non-string field name.
        """
        instrument_id = self._validate_instrument_id(instrument_id)
        self._validate_snapshot(before, "before")
        self._validate_snapshot(after, "after")

        if not self.config.enabled:
            notes = (
                f"REF DATA CHANGE [{STATUS_ENGINE_DISABLED}] {instrument_id}: "
                "detection engine disabled; snapshots were NOT compared."
            )
            logger.warning(notes)
            return ReferenceDataChangeReport(
                instrument_id=instrument_id,
                total_changes=0,
                critical_changes=0,
                warning_changes=0,
                info_changes=0,
                notifications=[],
                status=STATUS_ENGINE_DISABLED,
                audit_notes=notes,
                max_severity=None,
                as_of=as_of,
            )

        if self.config.treat_missing_as_removal:
            candidate_fields = set(before.keys()) | set(after.keys())
        else:
            # Delta mode: fields absent from `after` carry no information.
            candidate_fields = set(after.keys())

        notifications: List[ChangeNotification] = []
        for field_name in sorted(candidate_fields):
            old_present = field_name in before
            new_present = field_name in after
            old_value = before[field_name] if old_present else None
            new_value = after[field_name] if new_present else None

            if old_present and new_present:
                if _values_equal(old_value, new_value):
                    continue
                change_type = CHANGE_MODIFIED
            elif new_present:
                change_type = CHANGE_ADDED
            else:
                change_type = CHANGE_REMOVED

            notifications.append(
                ChangeNotification(
                    instrument_id=instrument_id,
                    field_name=field_name,
                    old_value=old_value,
                    new_value=new_value,
                    old_present=old_present,
                    new_present=new_present,
                    change_type=change_type,
                    severity=self.classify_severity(field_name, change_type),
                    as_of=as_of,
                )
            )

        critical = sum(1 for n in notifications if n.severity == SEVERITY_CRITICAL)
        warning = sum(1 for n in notifications if n.severity == SEVERITY_WARNING)
        info = sum(1 for n in notifications if n.severity == SEVERITY_INFO)
        total = len(notifications)
        status = STATUS_CHANGES_DETECTED if total else STATUS_NO_CHANGES
        max_severity = (
            max((n.severity for n in notifications), key=lambda s: _SEVERITY_RANK[s])
            if notifications
            else None
        )

        notes = (
            f"REF DATA CHANGE [{status}] {instrument_id}: "
            f"Total = {total}, Critical = {critical}, Warning = {warning}, Info = {info}."
        )
        if critical:
            logger.warning(notes)
        elif total:
            logger.info(notes)
        else:
            logger.debug(notes)

        return ReferenceDataChangeReport(
            instrument_id=instrument_id,
            total_changes=total,
            critical_changes=critical,
            warning_changes=warning,
            info_changes=info,
            notifications=notifications,
            status=status,
            audit_notes=notes,
            max_severity=max_severity,
            as_of=as_of,
        )

    # -- routing -----------------------------------------------------------
    def route_notifications(
        self,
        report: ReferenceDataChangeReport,
        consumers: Sequence[NotificationConsumer],
    ) -> NotificationDispatchResult:
        """Hand each notification to every consumer subscribed at or below its severity.

        Delivery is attempted **once** per (consumer, notification) pair, in consumer
        registration order, then notification order. A callback that raises is recorded
        in :attr:`NotificationDispatchResult.failures` and dispatch continues: an
        unreachable risk engine must not stop the OMS from being told that a symbol was
        renamed. No retry is performed here — retry policy, backoff and dead-lettering
        belong to the transport, which is the only layer that knows whether the sink is
        idempotent.

        Raises:
            ChangeDetectionConfigError: A consumer has a blank or duplicate name, an
                invalid ``min_severity``, or a non-callable callback.
        """
        self._validate_consumers(consumers)

        delivered = 0
        skipped = 0
        failures: List[DeliveryFailure] = []

        for consumer in consumers:
            threshold = _SEVERITY_RANK[consumer.min_severity]
            for notification in report.notifications:
                if _SEVERITY_RANK[notification.severity] < threshold:
                    skipped += 1
                    continue
                try:
                    consumer.callback(notification)
                except Exception as exc:  # noqa: BLE001 - one sink must not stop the rest
                    failures.append(
                        DeliveryFailure(
                            consumer_name=consumer.name,
                            change_key=notification.change_key,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    logger.error(
                        "Reference-data notification delivery failed: consumer=%s change=%s",
                        consumer.name,
                        notification.change_key,
                        exc_info=True,
                    )
                else:
                    delivered += 1

        if failures:
            logger.error(
                "Reference-data dispatch incomplete for %s: %d failure(s) across consumer(s) %s.",
                report.instrument_id,
                len(failures),
                ", ".join(sorted({f.consumer_name for f in failures})),
            )

        return NotificationDispatchResult(
            delivered=delivered, skipped_below_threshold=skipped, failures=failures
        )

    # -- validation helpers ------------------------------------------------
    @staticmethod
    def _validate_instrument_id(instrument_id: str) -> str:
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise SnapshotError("instrument_id must be a non-empty string.")
        return instrument_id.strip()

    @staticmethod
    def _validate_snapshot(snapshot: Mapping[str, Any], label: str) -> None:
        if not isinstance(snapshot, Mapping):
            raise SnapshotError(
                f"{label} snapshot must be a mapping of field name to value; "
                f"got {type(snapshot).__name__}."
            )
        for key in snapshot:
            if not isinstance(key, str) or not key.strip():
                raise SnapshotError(
                    f"{label} snapshot has a non-string or empty field name: {key!r}."
                )

    @staticmethod
    def _validate_consumers(consumers: Sequence[NotificationConsumer]) -> None:
        seen = set()
        for consumer in consumers:
            if not isinstance(consumer, NotificationConsumer):
                raise ChangeDetectionConfigError(
                    f"consumers must be NotificationConsumer instances; got "
                    f"{type(consumer).__name__}."
                )
            if not consumer.name.strip():
                raise ChangeDetectionConfigError("Consumer name must be a non-empty string.")
            if consumer.name in seen:
                raise ChangeDetectionConfigError(
                    f"Duplicate consumer name {consumer.name!r}; names must be unique so a "
                    "delivery failure can be attributed."
                )
            seen.add(consumer.name)
            _validate_severity(consumer.min_severity, f"consumer {consumer.name!r} min_severity")
            if not callable(consumer.callback):
                raise ChangeDetectionConfigError(
                    f"Consumer {consumer.name!r} callback is not callable."
                )
