"""Deployment freeze guard for trading systems: macro-event and session windows.

Gates production deployments against (a) one-off macro release windows (FOMC,
CPI, NFP, expiry days) and (b) recurring daily session windows (market open /
close), and enforces a dual sign-off break-glass path for emergency hotfixes.

Design stance -- this is a safety gate, so every ambiguous path fails CLOSED:
- An unrecognised target environment is DENIED, not exempted. The original
  "anything that is not PRODUCTION is exempt" test approved a deployment whose
  environment was misspelled ("PRODCUTION", "prod-eu"), which is the exact
  input a CI variable typo produces.
- Non-finite or out-of-range timestamps and buffers RAISE rather than silently
  producing a window that can never match (NaN comparisons are all False, and a
  negative buffer inverts the interval).
- If a maximum calendar age is configured and the registered calendar is older
  than that, production deployments are blocked. Macro release dates do move:
  BLS rescheduled the September 2025 Employment Situation from 3 Oct to 20 Nov
  2025 and never published the October 2025 CPI, following the lapse in
  appropriations. A stale calendar silently freezes the wrong hour.

Dual sign-off records approver identities, not just two booleans. Two booleans
can both be set by one person, and EU/EEA firms under RTS 6 (Commission
Delegated Regulation (EU) 2017/589) Article 11 must be able to determine "the
person that has approved the change" for any material change to algorithmic
trading software.

Scope and limitations (deliberate, documented):
- Approver identities are taken at face value. Bind them to authenticated IAM
  claims upstream; never accept an approver id from an unverified request body
  (see the ``risk-control-configuration-change-approval-workflow`` skill).
- Daily windows assume the standard session unless a per-date override is
  supplied. US equity markets close early at 1:00 p.m. ET on several days a
  year (NYSE holiday calendar), so a fixed "15 minutes before 16:00" rule
  guards nothing on those dates. Feed a real exchange calendar through
  ``session_overrides`` (see ``global-exchange-holiday-calendar-handling``).
- This module decides; it does not enforce. Wire the decision into the CI/CD
  job and fail the job on ``is_approved == False``.
- No freeze duration here is a regulatory constant. No regulator consulted
  mandates a specific pre/post-release deployment freeze length; the 60-minute
  defaults are engineering defaults to calibrate against your own incidents.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# --- Status codes (stable strings; CI pipelines match on these) --------------

STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED_FREEZE = "DEPLOYMENT_BLOCKED_FREEZE_ACTIVE"
STATUS_BREAK_GLASS_APPROVED = "BREAK_GLASS_HOTFIX_APPROVED"
STATUS_MISSING_DUAL_AUTH = "MISSING_DUAL_AUTHORIZATION"
STATUS_INVALID_DUAL_AUTH = "INVALID_DUAL_AUTHORIZATION"
STATUS_BLOCKED_STALE_CALENDAR = "DEPLOYMENT_BLOCKED_STALE_CALENDAR"
STATUS_UNKNOWN_ENVIRONMENT = "UNKNOWN_ENVIRONMENT_DENIED"

DEFAULT_PRODUCTION_ENVIRONMENTS = frozenset({"PRODUCTION"})
DEFAULT_EXEMPT_ENVIRONMENTS = frozenset({"STAGING", "RESEARCH", "DEVELOPMENT", "SANDBOX"})

# Engineering defaults, not regulatory constants.
DEFAULT_PRE_EVENT_BUFFER_MINUTES = 60.0
DEFAULT_POST_EVENT_BUFFER_MINUTES = 60.0

MONDAY_TO_FRIDAY: Tuple[int, ...] = (0, 1, 2, 3, 4)

# Sanity ceiling on a single buffer: a freeze arm longer than a week is a
# configuration error (minutes entered where days were meant, and similar).
_MAX_BUFFER_MINUTES = 7 * 24 * 60.0


class DeploymentFreezeError(ValueError):
    """Raised on an invalid freeze window, deployment request, or engine config."""


def _require_finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DeploymentFreezeError(
            f"{name} must be finite, got {value!r}. A NaN timestamp compares False "
            "against every window and would silently disable the freeze."
        )
    return number


def _require_buffer(value: float, name: str) -> float:
    minutes = _require_finite(value, name)
    if minutes < 0.0:
        raise DeploymentFreezeError(
            f"{name} must be >= 0, got {minutes}. A negative buffer inverts the "
            "freeze interval so that no request can ever fall inside it."
        )
    if minutes > _MAX_BUFFER_MINUTES:
        raise DeploymentFreezeError(
            f"{name} must be <= {_MAX_BUFFER_MINUTES} minutes (7 days), got {minutes}."
        )
    return minutes


def _require_identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentFreezeError(f"{name} must be a non-empty string, got {value!r}.")
    return value.strip()


def _parse_hhmm(value: str, name: str) -> Tuple[int, int]:
    """Parses a 24-hour ``HH:MM`` local wall-clock time."""
    text = _require_identifier(value, name)
    parts = text.split(":")
    if len(parts) != 2:
        raise DeploymentFreezeError(f"{name} must be 'HH:MM', got {value!r}.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise DeploymentFreezeError(f"{name} must be 'HH:MM' with integers, got {value!r}.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise DeploymentFreezeError(f"{name} out of range, got {value!r}.")
    return hour, minute


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DeploymentFreezeError(
            f"Unknown IANA timezone {name!r}. On Windows the stdlib tz database may be "
            "absent -- install the 'tzdata' package or supply a valid zone name."
        ) from exc


# --- Freeze window definitions ----------------------------------------------


@dataclass
class MacroEventFreezeWindow:
    """A one-off freeze around a scheduled macro release or market event.

    The buffers are measured from ``event_start_epoch_sec``, the instant the
    data hits the tape. Multi-part events need multiple windows or a longer
    post-buffer: the FOMC statement is released at 2:00 p.m. ET and the Chair's
    press conference begins at 2:30 p.m. ET, so a 30-minute post-buffer on the
    statement expires as the press conference starts.
    """

    event_id: str
    event_name: str
    event_start_epoch_sec: float
    pre_event_buffer_minutes: float = DEFAULT_PRE_EVENT_BUFFER_MINUTES
    post_event_buffer_minutes: float = DEFAULT_POST_EVENT_BUFFER_MINUTES

    def __post_init__(self) -> None:
        self.event_id = _require_identifier(self.event_id, "event_id")
        self.event_name = _require_identifier(self.event_name, "event_name")
        self.event_start_epoch_sec = _require_finite(
            self.event_start_epoch_sec, f"[{self.event_id}] event_start_epoch_sec"
        )
        self.pre_event_buffer_minutes = _require_buffer(
            self.pre_event_buffer_minutes, f"[{self.event_id}] pre_event_buffer_minutes"
        )
        self.post_event_buffer_minutes = _require_buffer(
            self.post_event_buffer_minutes, f"[{self.event_id}] post_event_buffer_minutes"
        )

    def bounds(self) -> Tuple[float, float]:
        """Inclusive [start, end] epoch bounds of the freeze."""
        return (
            self.event_start_epoch_sec - self.pre_event_buffer_minutes * 60.0,
            self.event_start_epoch_sec + self.post_event_buffer_minutes * 60.0,
        )


@dataclass
class DailyMarketFreezeWindow:
    """A recurring freeze around a daily session boundary (open or close).

    The anchor is a local wall-clock time in an IANA timezone, so the window
    follows daylight-saving transitions instead of drifting an hour twice a
    year -- the 9:30 a.m. New York open is 13:30 UTC in summer and 14:30 UTC in
    winter.

    ``session_overrides`` maps an ISO date (``'YYYY-MM-DD'``, local) to either a
    replacement ``'HH:MM'`` anchor (early close) or ``None`` (no session that
    day: holiday or weekend exception). Without overrides this class assumes the
    standard session on every configured weekday, which is wrong on early-close
    days -- US equity markets close at 1:00 p.m. ET on several dates a year.
    """

    window_id: str
    label: str
    timezone: str
    local_time_hhmm: str
    pre_buffer_minutes: float = 15.0
    post_buffer_minutes: float = 15.0
    weekdays: Sequence[int] = MONDAY_TO_FRIDAY
    session_overrides: Mapping[str, Optional[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.window_id = _require_identifier(self.window_id, "window_id")
        self.label = _require_identifier(self.label, "label")
        self.timezone = _require_identifier(self.timezone, "timezone")
        self._tz = _load_timezone(self.timezone)
        self._hour, self._minute = _parse_hhmm(self.local_time_hhmm, "local_time_hhmm")
        self.pre_buffer_minutes = _require_buffer(
            self.pre_buffer_minutes, f"[{self.window_id}] pre_buffer_minutes"
        )
        self.post_buffer_minutes = _require_buffer(
            self.post_buffer_minutes, f"[{self.window_id}] post_buffer_minutes"
        )

        weekdays = tuple(self.weekdays)
        if not weekdays:
            raise DeploymentFreezeError(f"[{self.window_id}] weekdays must not be empty.")
        for day in weekdays:
            if not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6:
                raise DeploymentFreezeError(
                    f"[{self.window_id}] weekdays must be ints 0 (Monday) to 6 (Sunday), got {day!r}."
                )
        self.weekdays = weekdays

        for iso_date, override in self.session_overrides.items():
            _parse_iso_date(iso_date, f"[{self.window_id}] session_overrides key")
            if override is not None:
                _parse_hhmm(override, f"[{self.window_id}] session_overrides[{iso_date}]")

    def occurrences(self, request_epoch_sec: float) -> List[Tuple[float, float]]:
        """Freeze bounds for the local dates that could contain this instant.

        Checks the local day before, of, and after the request, because a
        window near midnight (or with a multi-hour buffer) straddles dates.
        """
        local_now = datetime.fromtimestamp(request_epoch_sec, tz=self._tz)
        bounds: List[Tuple[float, float]] = []

        for offset in (-1, 0, 1):
            local_date = (local_now + timedelta(days=offset)).date()
            iso_date = local_date.isoformat()

            if iso_date in self.session_overrides:
                override = self.session_overrides[iso_date]
                if override is None:  # explicitly no session that day
                    continue
                hour, minute = _parse_hhmm(override, "session_overrides value")
            else:
                if local_date.weekday() not in self.weekdays:
                    continue
                hour, minute = self._hour, self._minute

            anchor = datetime(
                local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=self._tz
            )
            epoch = anchor.timestamp()
            # A local time inside a spring-forward gap does not exist; Python
            # maps it forward, so the round-trip changes the wall clock.
            round_trip = datetime.fromtimestamp(epoch, tz=self._tz)
            if (round_trip.hour, round_trip.minute) != (hour, minute):
                logger.warning(
                    "FREEZE WINDOW SKIPPED [%s]: local time %02d:%02d does not exist on %s in %s "
                    "(daylight-saving transition).",
                    self.window_id, hour, minute, iso_date, self.timezone,
                )
                continue

            bounds.append(
                (epoch - self.pre_buffer_minutes * 60.0, epoch + self.post_buffer_minutes * 60.0)
            )

        return bounds


def _parse_iso_date(value: str, name: str) -> None:
    text = _require_identifier(value, name)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise DeploymentFreezeError(f"{name} must be an ISO date 'YYYY-MM-DD', got {value!r}.") from exc


# --- Request and report ------------------------------------------------------


@dataclass
class DeploymentRequest:
    """One deployment attempt presented to the guard.

    ``risk_officer_id`` / ``head_of_trading_id`` are required for the
    break-glass path and must be two different people; the booleans alone are
    not sufficient authorisation. Bind the identities to authenticated IAM
    claims before constructing this request.
    """

    deployment_id: str
    service_name: str
    target_environment: str
    requested_epoch_sec: float
    is_emergency_hotfix: bool = False
    risk_officer_approval: bool = False
    head_of_trading_approval: bool = False
    risk_officer_id: Optional[str] = None
    head_of_trading_id: Optional[str] = None
    justification: Optional[str] = None

    def __post_init__(self) -> None:
        self.deployment_id = _require_identifier(self.deployment_id, "deployment_id")
        self.service_name = _require_identifier(self.service_name, "service_name")
        self.target_environment = _require_identifier(
            self.target_environment, "target_environment"
        )
        self.requested_epoch_sec = _require_finite(
            self.requested_epoch_sec, f"[{self.deployment_id}] requested_epoch_sec"
        )


@dataclass
class DeploymentFreezeAuditReport:
    """Audit record of one gate decision.

    ``active_freeze_event_name`` names the governing (latest-ending) window;
    ``active_freeze_labels`` lists every window covering the request, and
    ``freeze_ends_epoch_sec`` is when the last of them lifts -- the number an
    operator actually needs when a deploy is blocked.
    """

    deployment_id: str
    service_name: str
    target_environment: str
    is_approved: bool
    status: str
    active_freeze_event_name: Optional[str]
    applied_policy: str
    active_freeze_labels: List[str] = field(default_factory=list)
    freeze_ends_epoch_sec: Optional[float] = None
    approvers: List[str] = field(default_factory=list)


class DeploymentFreezeGuardEngine:
    """Evaluates deployment requests against registered freeze windows.

    Fails closed: unknown environments are denied, a stale calendar blocks
    production, and break-glass requires two distinct named approvers.
    """

    def __init__(
        self,
        production_environments: Sequence[str] = tuple(DEFAULT_PRODUCTION_ENVIRONMENTS),
        exempt_environments: Sequence[str] = tuple(DEFAULT_EXEMPT_ENVIRONMENTS),
        max_calendar_staleness_sec: Optional[float] = None,
        require_justification: bool = True,
    ) -> None:
        """
        Args:
            production_environments: Environment names subject to freezes.
            exempt_environments: Environment names explicitly exempt. Anything
                in neither set is denied rather than exempted.
            max_calendar_staleness_sec: If set, production deployments are
                blocked when the registered calendar is older than this.
                Requires ``set_calendar_as_of`` to be called on each refresh.
            require_justification: Require a non-empty justification (change
                ticket, incident id) on the break-glass path.
        """
        self.production_environments = frozenset(
            _require_identifier(name, "production_environments entry").upper()
            for name in production_environments
        )
        self.exempt_environments = frozenset(
            _require_identifier(name, "exempt_environments entry").upper()
            for name in exempt_environments
        )
        if not self.production_environments:
            raise DeploymentFreezeError("production_environments must not be empty.")
        overlap = self.production_environments & self.exempt_environments
        if overlap:
            raise DeploymentFreezeError(
                f"Environments cannot be both production and exempt: {sorted(overlap)}."
            )

        if max_calendar_staleness_sec is not None:
            max_calendar_staleness_sec = _require_finite(
                max_calendar_staleness_sec, "max_calendar_staleness_sec"
            )
            if max_calendar_staleness_sec <= 0.0:
                raise DeploymentFreezeError(
                    f"max_calendar_staleness_sec must be > 0, got {max_calendar_staleness_sec}."
                )
        self.max_calendar_staleness_sec = max_calendar_staleness_sec
        self.require_justification = bool(require_justification)

        self.freeze_events: List[MacroEventFreezeWindow] = []
        self.daily_windows: List[DailyMarketFreezeWindow] = []
        self.calendar_as_of_epoch_sec: Optional[float] = None

    # --- registration ----------------------------------------------------

    def register_freeze_event(self, event: MacroEventFreezeWindow) -> None:
        """Registers a one-off macro event window."""
        if not isinstance(event, MacroEventFreezeWindow):
            raise DeploymentFreezeError(
                f"register_freeze_event expects a MacroEventFreezeWindow, got "
                f"{type(event).__name__}."
            )
        if any(existing.event_id == event.event_id for existing in self.freeze_events):
            raise DeploymentFreezeError(
                f"Duplicate event_id {event.event_id!r}. Re-registering a moved release under "
                "the same id would leave both the stale and the corrected window active."
            )
        self.freeze_events.append(event)

    def register_daily_window(self, window: DailyMarketFreezeWindow) -> None:
        """Registers a recurring daily session window (market open / close)."""
        if not isinstance(window, DailyMarketFreezeWindow):
            raise DeploymentFreezeError(
                f"register_daily_window expects a DailyMarketFreezeWindow, got "
                f"{type(window).__name__}."
            )
        if any(existing.window_id == window.window_id for existing in self.daily_windows):
            raise DeploymentFreezeError(f"Duplicate window_id {window.window_id!r}.")
        self.daily_windows.append(window)

    def set_calendar_as_of(self, as_of_epoch_sec: float) -> None:
        """Records when the event calendar was last refreshed from its source."""
        self.calendar_as_of_epoch_sec = _require_finite(as_of_epoch_sec, "as_of_epoch_sec")

    # --- evaluation ------------------------------------------------------

    def active_freezes(self, request_epoch_sec: float) -> List[Tuple[str, float]]:
        """Returns ``(label, freeze_end_epoch_sec)`` for every covering window.

        Bounds are inclusive at both ends: a request exactly on the freeze
        boundary is inside the freeze.
        """
        request_epoch_sec = _require_finite(request_epoch_sec, "request_epoch_sec")
        active: List[Tuple[str, float]] = []

        for event in self.freeze_events:
            start, end = event.bounds()
            if start <= request_epoch_sec <= end:
                active.append((event.event_name, end))

        for window in self.daily_windows:
            for start, end in window.occurrences(request_epoch_sec):
                if start <= request_epoch_sec <= end:
                    active.append((window.label, end))
                    break

        # Latest-ending first, ties broken by label, so the governing window and
        # the reported lift time do not depend on registration order.
        active.sort(key=lambda item: (-item[1], item[0]))
        return active

    def evaluate_deployment_request(
        self, req: DeploymentRequest
    ) -> DeploymentFreezeAuditReport:
        """Audits one deployment request. Never raises on a policy outcome.

        Raises:
            DeploymentFreezeError: Only on a malformed request object.
        """
        if not isinstance(req, DeploymentRequest):
            raise DeploymentFreezeError(
                f"evaluate_deployment_request expects a DeploymentRequest, got "
                f"{type(req).__name__}."
            )

        environment = req.target_environment.strip().upper()

        if environment in self.exempt_environments:
            return self._report(
                req, True, STATUS_APPROVED, None, [], None,
                f"Non-production environment ({req.target_environment}) exempt from "
                "deployment freeze.",
            )

        if environment not in self.production_environments:
            msg = (
                f"Unrecognised target environment {req.target_environment!r}. Denied: an "
                "unknown environment is treated as production, never as exempt."
            )
            logger.error("DEPLOYMENT DENIED [%s]: %s", req.deployment_id, msg)
            return self._report(req, False, STATUS_UNKNOWN_ENVIRONMENT, None, [], None, msg)

        # Production path. Establish the blocking condition first, then decide
        # whether break-glass can lift it.
        blocking_status: Optional[str] = None
        blocking_msg = ""
        labels: List[str] = []
        freeze_end: Optional[float] = None

        if self.max_calendar_staleness_sec is not None:
            age = self._calendar_age(req.requested_epoch_sec)
            if age is None or age > self.max_calendar_staleness_sec:
                blocking_status = STATUS_BLOCKED_STALE_CALENDAR
                age_text = "never refreshed" if age is None else f"{age:.0f}s old"
                blocking_msg = (
                    f"PRODUCTION DEPLOYMENT BLOCKED: freeze calendar is {age_text} "
                    f"(limit {self.max_calendar_staleness_sec:.0f}s). Refusing to certify a "
                    "freeze window from a calendar that may have missed a rescheduled release."
                )

        if blocking_status is None:
            active = self.active_freezes(req.requested_epoch_sec)
            if active:
                labels = [label for label, _ in active]
                freeze_end = active[0][1]
                blocking_status = STATUS_BLOCKED_FREEZE
                blocking_msg = (
                    f"PRODUCTION DEPLOYMENT BLOCKED: active freeze window for "
                    f"'{labels[0]}' (lifts at epoch {freeze_end:.0f}"
                    + (f"; {len(labels)} windows active" if len(labels) > 1 else "")
                    + ")."
                )

        if blocking_status is None:
            return self._report(
                req, True, STATUS_APPROVED, None, [], None,
                "No active market event deployment freeze window.",
            )

        governing = labels[0] if labels else None

        if not req.is_emergency_hotfix:
            logger.critical("DEPLOYMENT BLOCKED [%s]: %s", req.deployment_id, blocking_msg)
            return self._report(
                req, False, blocking_status, governing, labels, freeze_end, blocking_msg
            )

        return self._evaluate_break_glass(req, governing, labels, freeze_end)

    # --- internals -------------------------------------------------------

    def _calendar_age(self, request_epoch_sec: float) -> Optional[float]:
        if self.calendar_as_of_epoch_sec is None:
            return None
        return max(0.0, request_epoch_sec - self.calendar_as_of_epoch_sec)

    def _evaluate_break_glass(
        self,
        req: DeploymentRequest,
        governing: Optional[str],
        labels: List[str],
        freeze_end: Optional[float],
    ) -> DeploymentFreezeAuditReport:
        """Dual sign-off: two approvals, two named and distinct approvers."""
        risk_id = (req.risk_officer_id or "").strip()
        trading_id = (req.head_of_trading_id or "").strip()
        justification = (req.justification or "").strip()

        missing: List[str] = []
        if not req.risk_officer_approval:
            missing.append("risk_officer_approval")
        if not req.head_of_trading_approval:
            missing.append("head_of_trading_approval")
        if not risk_id:
            missing.append("risk_officer_id")
        if not trading_id:
            missing.append("head_of_trading_id")
        if self.require_justification and not justification:
            missing.append("justification")

        if missing:
            msg = (
                "EMERGENCY HOTFIX REJECTED: incomplete dual sign-off, missing "
                f"{', '.join(missing)}. Recording the approving individuals is required to "
                "evidence who authorised a material change (RTS 6 Art. 11 for EU/EEA firms)."
            )
            logger.error("DEPLOYMENT REJECTED [%s]: %s", req.deployment_id, msg)
            return self._report(
                req, False, STATUS_MISSING_DUAL_AUTH, governing, labels, freeze_end, msg
            )

        if risk_id.casefold() == trading_id.casefold():
            msg = (
                f"EMERGENCY HOTFIX REJECTED: both approvals are held by the same person "
                f"({risk_id!r}). Dual sign-off requires two distinct approvers."
            )
            logger.error("DEPLOYMENT REJECTED [%s]: %s", req.deployment_id, msg)
            return self._report(
                req, False, STATUS_INVALID_DUAL_AUTH, governing, labels, freeze_end, msg
            )

        msg = (
            f"BREAK-GLASS EMERGENCY OVERRIDE APPROVED during '{governing}' "
            f"(dual sign-off: risk_officer={risk_id}, head_of_trading={trading_id}; "
            f"justification={justification or 'n/a'})."
        )
        logger.warning("DEPLOYMENT BREAK-GLASS [%s]: %s", req.deployment_id, msg)
        report = self._report(
            req, True, STATUS_BREAK_GLASS_APPROVED, governing, labels, freeze_end, msg
        )
        report.approvers = [risk_id, trading_id]
        return report

    @staticmethod
    def _report(
        req: DeploymentRequest,
        is_approved: bool,
        status: str,
        governing: Optional[str],
        labels: List[str],
        freeze_end: Optional[float],
        policy: str,
    ) -> DeploymentFreezeAuditReport:
        return DeploymentFreezeAuditReport(
            deployment_id=req.deployment_id,
            service_name=req.service_name,
            target_environment=req.target_environment,
            is_approved=is_approved,
            status=status,
            active_freeze_event_name=governing,
            applied_policy=policy,
            active_freeze_labels=list(labels),
            freeze_ends_epoch_sec=freeze_end,
        )
