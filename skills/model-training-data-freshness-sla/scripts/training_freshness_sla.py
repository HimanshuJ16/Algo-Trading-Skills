"""Training-data freshness SLA evaluation for governed model retraining.

Compares the age of the newest record in a training dataset against a
three-rung SLA ladder (target / warning / breach) and returns the governance
action a retraining pipeline should take.

The engine is deliberately stateless and calendar-free. Exchange-session
arithmetic is supplied by the caller through
``DatasetMetadataPayload.calendar_excluded_hours`` -- without it, a healthy
daily-bar pipeline audited on a Monday morning shows ~65h of wall-clock lag
and would be halted for a weekend it did not cause.

All thresholds are expressed in hours and all timestamps in epoch **seconds**.
No regulator prescribes a numeric freshness threshold; the defaults here are
illustrative operating points, not standards (see ``references/standards.md``).
"""

import logging
import math
import numbers
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600.0

# Governance statuses, ordered by severity.
STATUS_COMPLIANT = "SLA_COMPLIANT"
STATUS_WARNING_OFF_TARGET = "SLA_WARNING_OFF_TARGET"
STATUS_WARNING_NEAR_LIMIT = "SLA_WARNING_NEAR_LIMIT"
STATUS_BREACH_CRITICAL = "SLA_BREACH_CRITICAL"

# Recommended governance actions.
ACTION_PROCEED_NORMAL = "PROCEED_NORMAL"
ACTION_TRIGGER_BACKFILL_ALERT = "TRIGGER_BACKFILL_ALERT"
ACTION_ESCALATE_BACKFILL_URGENT = "ESCALATE_BACKFILL_URGENT"
ACTION_HALT_MODEL_RETRAINING = "HALT_MODEL_RETRAINING"
ACTION_REDUCE_CONFIDENCE = "REDUCE_CONFIDENCE"
ACTION_ALERT_ONLY = "ALERT_ONLY"

# Actions a caller may configure for a hard breach. Constrained because
# downstream automation matches on the exact string: a typo that flowed
# through unchecked would silently fail to halt the retraining job.
VALID_BREACH_ACTIONS = frozenset({
    ACTION_HALT_MODEL_RETRAINING,
    ACTION_REDUCE_CONFIDENCE,
    ACTION_ALERT_ONLY,
})

# How ``latest_record_timestamp_epoch`` was derived. Freshness is only
# meaningful against event time; ingestion time omits the vendor's own
# publication delay and therefore understates true staleness.
BASIS_EVENT_TIME = "EVENT_TIME"
BASIS_INGESTION_TIME = "INGESTION_TIME"
VALID_TIMESTAMP_BASES = frozenset({BASIS_EVENT_TIME, BASIS_INGESTION_TIME})

# Tolerance for float noise when comparing caller-supplied calendar hours
# against elapsed wall-clock hours.
_FLOAT_TOLERANCE_HOURS = 1e-9


def _require_finite(value: object, name: str) -> float:
    """Return ``value`` as a finite float or raise.

    NaN is rejected explicitly: every ``>`` comparison against NaN is ``False``,
    so a NaN lag or threshold would fall through the whole SLA ladder and be
    reported as ``SLA_COMPLIANT`` / ``PROCEED_NORMAL`` -- a governance gate
    failing open on unusable input.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return numeric


def _require_count(value: object, name: str) -> int:
    """Return ``value`` as a non-negative int or raise."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}.")
    count = int(value)
    if count < 0:
        raise ValueError(f"{name} must be non-negative, got {count}.")
    return count


@dataclass
class FreshnessSlaConfig:
    """Per-model freshness contract for one training dataset.

    Thresholds form a ladder that must satisfy
    ``0 < target_sla_hours <= warning_sla_hours <= breach_sla_hours``.
    The 24/36/48 defaults are illustrative operating points for a
    daily-bar equity dataset and carry no external authority; set them from
    the dataset's own publication cadence.
    """

    model_id: str
    dataset_name: str
    target_sla_hours: float = 24.0      # Freshness the pipeline promises.
    warning_sla_hours: float = 36.0     # Escalate: approaching the hard limit.
    breach_sla_hours: float = 48.0      # Hard limit; past this, do not train.
    action_on_breach: str = ACTION_HALT_MODEL_RETRAINING  # See VALID_BREACH_ACTIONS.
    max_missing_days: int = 2           # Gaps tolerated before a hard breach.
    min_record_count: int = 0           # Rows required to train; 0 disables.
    clock_skew_tolerance_seconds: float = 1.0  # Absorbed NTP skew, see below.

    def validate(self) -> None:
        """Raise if the contract is unusable. Called by the engine."""
        for name in ("model_id", "dataset_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"FreshnessSlaConfig.{name} must be a non-empty string.")

        target = _require_finite(self.target_sla_hours, "target_sla_hours")
        warning = _require_finite(self.warning_sla_hours, "warning_sla_hours")
        breach = _require_finite(self.breach_sla_hours, "breach_sla_hours")
        if target <= 0.0:
            raise ValueError(f"target_sla_hours must be positive, got {target}.")
        if not (target <= warning <= breach):
            raise ValueError(
                "SLA thresholds must be ordered target <= warning <= breach, got "
                f"target={target}, warning={warning}, breach={breach}. An inverted "
                "ladder makes at least one rung unreachable."
            )

        if self.action_on_breach not in VALID_BREACH_ACTIONS:
            raise ValueError(
                f"action_on_breach {self.action_on_breach!r} is not one of "
                f"{sorted(VALID_BREACH_ACTIONS)}. Downstream automation matches "
                "this string exactly, so an unrecognised value would not halt."
            )

        _require_count(self.max_missing_days, "max_missing_days")
        _require_count(self.min_record_count, "min_record_count")
        tolerance = _require_finite(
            self.clock_skew_tolerance_seconds, "clock_skew_tolerance_seconds")
        if tolerance < 0.0:
            raise ValueError(
                f"clock_skew_tolerance_seconds must be non-negative, got {tolerance}.")


@dataclass
class DatasetMetadataPayload:
    """Observed state of one ingested training dataset.

    ``latest_record_timestamp_epoch`` and ``current_system_timestamp_epoch``
    must be epoch **seconds** in the same clock domain. Passing epoch
    milliseconds inflates the measured lag by ~1000x, which fails closed
    (a spurious breach) rather than open -- but it is still a caller bug.

    ``calendar_excluded_hours`` is the span within the audit window during
    which the dataset was not expected to publish (weekends, exchange
    holidays, overnight for a session-bound feed). The caller computes it from
    its exchange calendar; this engine owns no calendar.
    """

    dataset_name: str
    latest_record_timestamp_epoch: float
    current_system_timestamp_epoch: float
    total_record_count: int
    missing_days_count: int = 0
    calendar_excluded_hours: float = 0.0
    timestamp_basis: str = BASIS_EVENT_TIME

    def validate(self) -> None:
        """Raise if the payload is unusable. Called by the engine."""
        if not isinstance(self.dataset_name, str) or not self.dataset_name.strip():
            raise ValueError("DatasetMetadataPayload.dataset_name must be a non-empty string.")
        _require_finite(self.latest_record_timestamp_epoch, "latest_record_timestamp_epoch")
        _require_finite(self.current_system_timestamp_epoch, "current_system_timestamp_epoch")
        _require_count(self.total_record_count, "total_record_count")
        _require_count(self.missing_days_count, "missing_days_count")
        excluded = _require_finite(self.calendar_excluded_hours, "calendar_excluded_hours")
        if excluded < 0.0:
            raise ValueError(
                f"calendar_excluded_hours must be non-negative, got {excluded}.")
        if self.timestamp_basis not in VALID_TIMESTAMP_BASES:
            raise ValueError(
                f"timestamp_basis {self.timestamp_basis!r} is not one of "
                f"{sorted(VALID_TIMESTAMP_BASES)}.")


@dataclass
class FreshnessSlaReport:
    """Audit record of one SLA evaluation.

    ``data_lag_hours`` is the raw wall-clock age of the newest record.
    ``effective_lag_hours`` is that lag net of ``calendar_excluded_hours`` and
    is the value the verdict was actually computed from; the two are equal
    when no calendar exclusion was supplied.

    ``is_sla_compliant`` means "within the *target* SLA" -- it is ``False``
    for both warning rungs. Gate a hard stop on ``is_sla_breached``.
    """

    model_id: str
    dataset_name: str
    data_lag_hours: float
    target_sla_hours: float
    breach_sla_hours: float
    is_sla_compliant: bool
    recommended_governance_action: str
    status: str
    audit_notes: str
    warning_sla_hours: float = 0.0
    effective_lag_hours: float = 0.0
    is_sla_breached: bool = False
    calendar_excluded_hours: float = 0.0
    clock_skew_seconds: float = 0.0
    timestamp_basis: str = BASIS_EVENT_TIME
    total_record_count: int = 0
    missing_days_count: int = 0


class TrainingFreshnessSlaEngine:
    """Evaluates a training dataset against its freshness SLA contract.

    Stateless and deterministic: the same config and payload always produce
    the same report. The engine reads no clock of its own -- the caller
    supplies ``current_system_timestamp_epoch`` -- so evaluations are
    reproducible and replayable from an audit log.
    """

    def evaluate_training_freshness_sla(
        self,
        config: FreshnessSlaConfig,
        metadata: DatasetMetadataPayload,
    ) -> FreshnessSlaReport:
        """Classify dataset freshness and return the recommended action.

        Raises:
            TypeError: a field has the wrong type.
            ValueError: the config is inconsistent, the payload is unusable,
                the config and payload describe different datasets, or the
                newest record is dated further into the future than
                ``clock_skew_tolerance_seconds`` allows.
        """
        config.validate()
        metadata.validate()

        if config.dataset_name != metadata.dataset_name:
            raise ValueError(
                f"Dataset mismatch: config targets {config.dataset_name!r} but the "
                f"payload describes {metadata.dataset_name!r}. Evaluating one "
                "dataset's SLA against another's metadata would mislabel the verdict."
            )

        lag_seconds = (
            metadata.current_system_timestamp_epoch
            - metadata.latest_record_timestamp_epoch
        )

        skew_notes = []
        clock_skew_seconds = 0.0
        if lag_seconds < 0.0:
            # A future-dated record is a clock problem, not fresh data.
            clock_skew_seconds = -lag_seconds
            if clock_skew_seconds > config.clock_skew_tolerance_seconds:
                raise ValueError(
                    f"Latest record for {metadata.dataset_name} is dated "
                    f"{clock_skew_seconds:.3f}s into the future, beyond the "
                    f"{config.clock_skew_tolerance_seconds:.3f}s skew tolerance. "
                    "The record's vintage is unknown; fix clock sync (NTP/PTP) "
                    "before trusting this dataset."
                )
            # Within tolerance: routine host-to-host skew, treat as zero lag.
            skew_notes.append(
                f"Clock skew of {clock_skew_seconds:.3f}s absorbed "
                f"(tolerance {config.clock_skew_tolerance_seconds:.3f}s); lag floored at 0."
            )
            lag_seconds = 0.0

        raw_lag_hours = lag_seconds / SECONDS_PER_HOUR
        if metadata.calendar_excluded_hours > raw_lag_hours + _FLOAT_TOLERANCE_HOURS:
            raise ValueError(
                f"calendar_excluded_hours ({metadata.calendar_excluded_hours}) exceeds "
                f"the elapsed lag ({raw_lag_hours}h). More non-publishing hours than "
                "elapsed hours indicates a calendar bug in the caller."
            )
        effective_lag_hours = max(0.0, raw_lag_hours - metadata.calendar_excluded_hours)

        # Classify on the exact lag; round only for presentation. Rounding
        # first would absorb a real overshoot of up to ~18 seconds.
        breach_reasons = []
        if effective_lag_hours > config.breach_sla_hours:
            breach_reasons.append(
                f"effective data lag {effective_lag_hours:.4f}h exceeds the hard limit "
                f"{config.breach_sla_hours:.2f}h"
            )
        if metadata.missing_days_count > config.max_missing_days:
            breach_reasons.append(
                f"{metadata.missing_days_count} missing days exceeds max_missing_days "
                f"({config.max_missing_days}) -- zero lag on a gapped series is not freshness"
            )
        if metadata.total_record_count < config.min_record_count:
            breach_reasons.append(
                f"{metadata.total_record_count} records is below min_record_count "
                f"({config.min_record_count})"
            )

        if breach_reasons:
            status = STATUS_BREACH_CRITICAL
            action = config.action_on_breach
            is_breached = True
            headline = (
                f"FRESHNESS SLA CRITICAL BREACH [{config.dataset_name}]: "
                + "; ".join(breach_reasons)
                + f". Enforcing action: '{action}'."
            )
            log = logger.critical
        elif effective_lag_hours > config.warning_sla_hours:
            status = STATUS_WARNING_NEAR_LIMIT
            action = ACTION_ESCALATE_BACKFILL_URGENT
            is_breached = False
            headline = (
                f"FRESHNESS SLA NEAR LIMIT [{config.dataset_name}]: effective data lag "
                f"({effective_lag_hours:.2f}h) exceeds the warning threshold "
                f"({config.warning_sla_hours:.2f}h) and is within "
                f"{config.breach_sla_hours - effective_lag_hours:.2f}h of the hard limit "
                f"({config.breach_sla_hours:.2f}h)."
            )
            log = logger.warning
        elif effective_lag_hours > config.target_sla_hours:
            status = STATUS_WARNING_OFF_TARGET
            action = ACTION_TRIGGER_BACKFILL_ALERT
            is_breached = False
            headline = (
                f"FRESHNESS SLA OFF TARGET [{config.dataset_name}]: effective data lag "
                f"({effective_lag_hours:.2f}h) exceeds the target SLA "
                f"({config.target_sla_hours:.2f}h), below the warning threshold "
                f"({config.warning_sla_hours:.2f}h)."
            )
            log = logger.warning
        else:
            status = STATUS_COMPLIANT
            action = ACTION_PROCEED_NORMAL
            is_breached = False
            headline = (
                f"FRESHNESS SLA OK [{config.dataset_name}]: effective data lag "
                f"({effective_lag_hours:.2f}h) is within the target SLA "
                f"({config.target_sla_hours:.2f}h)."
            )
            log = logger.info

        context_notes = list(skew_notes)
        if metadata.calendar_excluded_hours > 0.0:
            context_notes.append(
                f"Raw lag {raw_lag_hours:.2f}h less {metadata.calendar_excluded_hours:.2f}h "
                "of caller-supplied non-publishing calendar time."
            )
            if not is_breached and raw_lag_hours > config.breach_sla_hours:
                # The exclusion, not the data, is what kept this dataset out of
                # a breach. calendar_excluded_hours is a trusted caller input:
                # an over-stated exclusion silently disables the lag gate, so
                # make the dependency visible whenever it decides the verdict.
                context_notes.append(
                    "CALENDAR EXCLUSION IS LOAD-BEARING: raw lag alone would have "
                    f"breached the {config.breach_sla_hours:.2f}h hard limit. Verify the "
                    "exclusion against the exchange calendar before trusting this pass."
                )
                log = logger.warning
        if metadata.timestamp_basis == BASIS_INGESTION_TIME:
            context_notes.append(
                "Lag measured from INGESTION time, not event time: it excludes the "
                "vendor's publication delay and understates true staleness."
            )

        notes = " ".join([headline] + context_notes)
        log(notes)

        return FreshnessSlaReport(
            model_id=config.model_id,
            dataset_name=config.dataset_name,
            data_lag_hours=round(raw_lag_hours, 2),
            target_sla_hours=config.target_sla_hours,
            breach_sla_hours=config.breach_sla_hours,
            is_sla_compliant=(status == STATUS_COMPLIANT),
            recommended_governance_action=action,
            status=status,
            audit_notes=notes,
            warning_sla_hours=config.warning_sla_hours,
            effective_lag_hours=round(effective_lag_hours, 2),
            is_sla_breached=is_breached,
            calendar_excluded_hours=metadata.calendar_excluded_hours,
            clock_skew_seconds=clock_skew_seconds,
            timestamp_basis=metadata.timestamp_basis,
            total_record_count=metadata.total_record_count,
            missing_days_count=metadata.missing_days_count,
        )
