"""systemd supervision for algorithmic trading bots.

Three things live here, and they solve three different failure modes:

1. ``SystemdSupervisionHelper`` speaks the **sd_notify protocol** -- ``READY=1``
   at the end of start-up, ``WATCHDOG=1`` as the keep-alive ping, ``STOPPING=1``
   when shutdown begins, and ``EXTEND_TIMEOUT_USEC=`` when a shutdown that is
   still cancelling live orders needs more time than ``TimeoutStopSec=``.

2. ``run_premarket_healthcheck`` is the ``ExecStartPre=`` gate: credentials
   present, broker reachable, exchange open. It separates a *fault* (exit
   non-zero, the unit should fail) from *market closed* (not a fault -- see
   ``references/workflows.md`` for why that distinction matters to the start
   rate limiter).

3. ``validate_unit_file_content`` audits a ``.service`` file. It parses
   sections, because the highest-value defect in a trading-bot unit is a
   directive written in the wrong one.

Scope and honesty boundary
--------------------------
This module makes no ``systemctl`` call, reads no journal, and starts nothing.
It sends datagrams to the socket systemd exported in ``$NOTIFY_SOCKET`` and it
reasons about the text of a unit file you hand it. It cannot tell you whether
the unit on disk is the unit systemd loaded (``systemctl cat`` and drop-ins
decide that), nor whether the running systemd is new enough for a directive.

Two protocol facts drive most of the design, both from ``sd_notify(3)``:

* The payload is "a newline-separated list of variable assignments", and
  ``STATUS=`` passes "a single-line UTF-8 status string". A newline inside a
  caller-supplied status is therefore not cosmetic -- it injects a further
  protocol field. The convenience wrappers sanitise; the primitive
  ``build_notify_message`` refuses.
* "If the ``$NOTIFY_SOCKET`` was not set and hence no status message could be
  sent, 0 is returned." Not being under systemd is a normal condition, not an
  error, and every notify call here returns ``False`` rather than raising.

See ``references/standards.md`` for the source behind every directive this
module validates.
"""

import datetime
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from typing import (
    Callable,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Every result is returned programmatically, so a caller with no
# handlers still gets the full outcome. Without this, the module's own
# `logger.error` calls fall through to logging's lastResort handler and print
# onto the supervised process's stderr, i.e. into the journal, unformatted.
logger.addHandler(logging.NullHandler())

__all__ = [
    "NotifyProtocolError",
    "PreMarketHealthCheckError",
    "HealthCheckResult",
    "UnitFileFinding",
    "UnitFileValidationReport",
    "SystemdSupervisionHelper",
    "build_notify_message",
    "parse_unit_file",
    "parse_systemd_timespan",
    "DEFAULT_REQUIRED_SECRETS",
    "MAX_STATUS_LENGTH",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "CODE_MALFORMED_UNIT_FILE",
    "CODE_MISSING_EXEC_START",
    "CODE_MISSING_MEMORY_LIMIT",
    "CODE_MISSING_PREMARKET_HEALTHCHECK",
    "CODE_MISSING_RESTART_POLICY",
    "CODE_MISSING_START_RATE_LIMIT",
    "CODE_MISSING_STOP_TIMEOUT",
    "CODE_MISSING_WATCHDOG",
    "CODE_START_LIMIT_IGNORED_IN_SERVICE_SECTION",
    "CODE_START_LIMIT_LEGACY_SERVICE_SECTION",
    "CODE_START_LIMIT_UNREACHABLE",
    "CODE_UNBOUNDED_RESTART_POLICY",
    "CODE_UNPARSEABLE_DIRECTIVE",
    "CODE_WATCHDOG_PINGS_IGNORED",
]

# --------------------------------------------------------------------------
# Protocol constants
# --------------------------------------------------------------------------

#: Repository convention, not a systemd limit. sd_notify(3) documents no
#: maximum for STATUS=; the datagram simply has to fit in the socket buffer. A
#: status line is meant to be human-readable in `systemctl status`, so a broker
#: error blob pasted into it is a bug regardless of whether it fits.
MAX_STATUS_LENGTH = 2048

#: Valid sd_notify variable names, mirroring an environment-block key.
_NOTIFY_FIELD_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: Control characters that must never survive into a notify payload. \n and \r
#: are the injection vector; the rest merely corrupt journal output.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

DEFAULT_REQUIRED_SECRETS: Tuple[str, ...] = ("BROKER_API_KEY", "BROKER_SECRET")

# --------------------------------------------------------------------------
# Unit-file audit vocabulary
# --------------------------------------------------------------------------

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"

_SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2}

CODE_MALFORMED_UNIT_FILE = "MALFORMED_UNIT_FILE"
CODE_START_LIMIT_IGNORED_IN_SERVICE_SECTION = "START_LIMIT_IGNORED_IN_SERVICE_SECTION"
CODE_START_LIMIT_LEGACY_SERVICE_SECTION = "START_LIMIT_LEGACY_SERVICE_SECTION"
CODE_START_LIMIT_UNREACHABLE = "START_LIMIT_UNREACHABLE"
CODE_MISSING_START_RATE_LIMIT = "MISSING_START_RATE_LIMIT"
CODE_UNBOUNDED_RESTART_POLICY = "UNBOUNDED_RESTART_POLICY"
CODE_MISSING_RESTART_POLICY = "MISSING_RESTART_POLICY"
CODE_WATCHDOG_PINGS_IGNORED = "WATCHDOG_PINGS_IGNORED"
CODE_MISSING_WATCHDOG = "MISSING_WATCHDOG"
CODE_MISSING_MEMORY_LIMIT = "MISSING_MEMORY_LIMIT"
CODE_MISSING_PREMARKET_HEALTHCHECK = "MISSING_PREMARKET_HEALTHCHECK"
CODE_MISSING_EXEC_START = "MISSING_EXEC_START"
CODE_MISSING_STOP_TIMEOUT = "MISSING_STOP_TIMEOUT"
CODE_UNPARSEABLE_DIRECTIVE = "UNPARSEABLE_DIRECTIVE"

_SECTION_UNIT = "Unit"
_SECTION_SERVICE = "Service"

#: Service types for which systemd forces `NotifyAccess=main` when it is unset
#: or `none`, which is what makes `WATCHDOG=1` from the main process arrive.
_NOTIFY_SERVICE_TYPES = frozenset({"notify", "notify-reload"})

#: systemd-system.conf(5): DefaultStartLimitIntervalSec defaults to 10s and
#: DefaultStartLimitBurst to 5. Used only to reason about what a unit that
#: omits (or misplaces) the directives actually gets.
_DEFAULT_START_LIMIT_INTERVAL_SEC = 10.0
_DEFAULT_START_LIMIT_BURST = 5


class NotifyProtocolError(ValueError):
    """Raised when a value cannot be encoded into an sd_notify datagram."""


class PreMarketHealthCheckError(RuntimeError):
    """Raised when a pre-market healthcheck cannot be evaluated at all."""


# --------------------------------------------------------------------------
# sd_notify payload construction (pure -- testable without a socket)
# --------------------------------------------------------------------------

def build_notify_message(fields: Sequence[Tuple[str, str]]) -> str:
    """Assemble a validated sd_notify payload from ordered ``(name, value)`` pairs.

    sd_notify(3) describes the payload as "a newline-separated list of variable
    assignments, similar in style to an environment block". This function is the
    strict primitive: it raises rather than silently emitting a payload that
    means something other than what the caller asked for.

    Args:
        fields: Ordered assignments, e.g. ``[("READY", "1"), ("STATUS", "up")]``.
            Order is preserved so payloads are byte-for-byte deterministic.

    Returns:
        The payload string, ready to encode as UTF-8.

    Raises:
        NotifyProtocolError: If ``fields`` is empty, a name is not an uppercase
            environment-style identifier, or a value contains a control
            character. A newline in a value is the important case: it would
            append a broker- or operator-controlled *protocol field* (say
            ``MAINPID=`` or ``WATCHDOG=1``) to the datagram.
    """
    if not fields:
        raise NotifyProtocolError("sd_notify payload must contain at least one assignment")

    parts: List[str] = []
    for name, value in fields:
        if not isinstance(name, str) or not _NOTIFY_FIELD_NAME_RE.match(name):
            raise NotifyProtocolError(
                f"invalid sd_notify field name {name!r}; expected uppercase [A-Z][A-Z0-9_]*"
            )
        if not isinstance(value, str):
            raise NotifyProtocolError(
                f"sd_notify field {name} must be a str, got {type(value).__name__}"
            )
        match = _CONTROL_CHAR_RE.search(value)
        if match:
            raise NotifyProtocolError(
                f"sd_notify field {name} contains control character "
                f"{match.group(0)!r} at offset {match.start()}; a newline here "
                f"would inject an additional protocol field into the datagram"
            )
        parts.append(f"{name}={value}")
    return "\n".join(parts)


def _sanitize_status(status: str) -> str:
    """Coerce caller text into a legal single-line ``STATUS=`` value.

    The convenience wrappers must never raise: refusing to send ``STOPPING=1``
    because a broker error message contained a newline would turn a cosmetic
    problem into a shutdown that systemd has to SIGKILL. So they sanitise, and
    log when sanitising changed anything.
    """
    if not isinstance(status, str):
        status = str(status)
    cleaned = _CONTROL_CHAR_RE.sub(" ", status).strip()
    if len(cleaned) > MAX_STATUS_LENGTH:
        cleaned = cleaned[: MAX_STATUS_LENGTH - 1].rstrip() + "…"
    if cleaned != status:
        logger.warning(
            "sd_notify STATUS= was sanitised before sending (control characters "
            "removed and/or truncated to %d chars)", MAX_STATUS_LENGTH
        )
    return cleaned


# --------------------------------------------------------------------------
# Pre-market healthcheck
# --------------------------------------------------------------------------

@dataclass
class HealthCheckResult:
    """Outcome of the ``ExecStartPre=`` gate.

    ``passed`` keeps its original meaning -- every check succeeded -- but it is
    *not* the right thing to turn into an exit code on its own. A market holiday
    makes ``passed`` false while ``is_fault`` stays false: nothing is broken, the
    exchange is shut. Exiting non-zero on a holiday marks the unit ``failed``
    and consumes a slot in the start rate limiter, which is the wrong signal to
    send to both systemd and your on-call rotation.

    Attributes:
        passed: True when no check failed and the market is open.
        checks: Per-check booleans, stable keys, for dashboards and assertions.
        details: Human-readable explanation for each failure.
        blocking_failures: The subset of ``details`` describing genuine faults.
        market_closed: True when the exchange calendar says today is a holiday.
        as_of_date: The date the calendar was actually asked about.
    """

    passed: bool
    checks: Dict[str, bool]
    details: List[str]
    blocking_failures: List[str] = field(default_factory=list)
    market_closed: bool = False
    as_of_date: Optional[datetime.date] = None

    @property
    def is_fault(self) -> bool:
        """True when something is broken, as opposed to the market being shut.

        This is the value an ``ExecStartPre=`` wrapper should turn into a
        non-zero exit status.
        """
        return bool(self.blocking_failures)


# --------------------------------------------------------------------------
# Unit-file parsing and audit
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitFileFinding:
    """One defect found in a systemd unit file."""

    code: str
    severity: str
    section: Optional[str]
    directive: Optional[str]
    description: str
    remediation: str

    def __str__(self) -> str:
        where = ""
        if self.section and self.directive:
            where = f" [{self.section}]{self.directive}:"
        elif self.section:
            where = f" [{self.section}]:"
        return f"{self.severity}:{self.code}{where} {self.description} -> {self.remediation}"


@dataclass(frozen=True)
class UnitFileValidationReport:
    """Result of :meth:`SystemdSupervisionHelper.validate_unit_file_content`.

    Unpacks as ``(is_valid, issues)`` so that the pre-2.0.0 call site
    ``valid, issues = validate_unit_file_content(text)`` keeps working, while
    ``findings`` gives callers a stable ``code`` to branch on. Branch on
    ``code``; ``description`` wording may change between versions.
    """

    is_valid: bool
    findings: Tuple[UnitFileFinding, ...]

    @property
    def issues(self) -> List[str]:
        """Findings rendered as strings, most severe first."""
        return [str(f) for f in self.findings]

    @property
    def codes(self) -> List[str]:
        """Stable finding codes, most severe first."""
        return [f.code for f in self.findings]

    def __iter__(self) -> Iterator[object]:
        return iter((self.is_valid, self.issues))


_TIMESPAN_UNITS_SEC: Dict[str, float] = {
    "us": 1e-6, "usec": 1e-6,
    "ms": 1e-3, "msec": 1e-3,
    "s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0,
    "m": 60.0, "min": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hour": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
    "w": 604800.0, "week": 604800.0, "weeks": 604800.0,
}

_TIMESPAN_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]*)")


def parse_systemd_timespan(value: str) -> Optional[float]:
    """Parse a systemd.time(7) time span into seconds.

    Handles the forms that appear in service units: a bare number (seconds by
    default for the directives audited here), a suffixed value such as ``30s``
    or ``10min``, and a compound such as ``1min 30s``. ``infinity`` is
    recognised and returned as ``float("inf")``.

    Returns:
        Seconds, or ``None`` if the value is not a well-formed time span.
    """
    text = value.strip().lower()
    if not text:
        return None
    if text == "infinity":
        return float("inf")

    remaining = text
    total = 0.0
    matched_any = False
    while remaining:
        match = _TIMESPAN_TOKEN_RE.match(remaining)
        if not match:
            return None
        number, unit = match.group(1), match.group(2)
        if unit == "":
            factor = 1.0  # Bare number: seconds, for every directive audited here.
        elif unit in _TIMESPAN_UNITS_SEC:
            factor = _TIMESPAN_UNITS_SEC[unit]
        else:
            return None
        total += float(number) * factor
        matched_any = True
        remaining = remaining[match.end():].strip()
    return total if matched_any else None


def parse_unit_file(unit_content: str) -> Dict[str, Dict[str, List[str]]]:
    """Parse a systemd unit file into ``{section: {directive: [values]}}``.

    Section-aware on purpose. A substring scan cannot tell
    ``[Unit] StartLimitIntervalSec=600`` (honoured) from
    ``[Service] StartLimitIntervalSec=600`` (an unknown key in that section,
    logged and ignored), and that distinction is the single most consequential
    thing about a trading-bot unit file.

    Handles the syntax that matters for auditing: ``#``/``;`` comments,
    backslash line continuation, and repeated directives (``ExecStartPre=`` is
    routinely given more than once). Values are stored in file order.
    """
    sections: Dict[str, Dict[str, List[str]]] = {}
    current: Optional[str] = None
    pending = ""

    for raw_line in unit_content.splitlines():
        line = raw_line.strip()
        if pending:
            line = pending + " " + line
            pending = ""
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" not in line or current is None:
            # Reported separately by _stray_lines() as MALFORMED_UNIT_FILE.
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        sections.setdefault(current, {}).setdefault(key, []).append(value)

    return sections


def _stray_lines(unit_content: str) -> List[str]:
    """Content lines that sit outside any section or carry no ``=``."""
    stray: List[str] = []
    current: Optional[str] = None
    pending = ""
    for raw_line in unit_content.splitlines():
        line = raw_line.strip()
        if pending:
            line = pending + " " + line
            pending = ""
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current is None or "=" not in line:
            stray.append(line)
    return stray


def _last(section: Dict[str, List[str]], directive: str) -> Optional[str]:
    """Last value for a directive, which is the one systemd keeps for scalars."""
    values = section.get(directive)
    return values[-1] if values else None


class SystemdSupervisionHelper:
    """sd_notify client and unit-file auditor for a supervised trading bot.

    The environment is snapshotted at construction so that behaviour is
    deterministic and testable: pass ``env`` to exercise watchdog logic without
    mutating ``os.environ``.

    Args:
        notify_socket_path: Overrides ``$NOTIFY_SOCKET``. Leave unset in
            production -- systemd exports the real value.
        env: Environment mapping to read. Defaults to ``os.environ``.
    """

    def __init__(
        self,
        notify_socket_path: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._env: Mapping[str, str] = dict(os.environ if env is None else env)
        self.notify_socket_path: Optional[str] = (
            notify_socket_path or self._env.get("NOTIFY_SOCKET") or None
        )

    # -- transport ---------------------------------------------------------

    def sd_notify(self, state: str) -> bool:
        """Send one raw datagram to ``$NOTIFY_SOCKET``.

        Args:
            state: A pre-built payload. Use :func:`build_notify_message` to
                construct one safely; this method sends whatever it is given.

        Returns:
            True if the datagram was handed to the kernel. False if there is no
            notify socket (the normal case outside systemd), if the address is
            not a form this platform can use, or if the send failed. Never
            raises -- a bot must not die because its supervisor is absent.

        Note:
            Delivery to the socket is not acceptance by systemd. If the unit is
            not ``Type=notify``/``notify-reload`` and ``NotifyAccess=`` is unset
            or ``none``, systemd discards the message and this still returns
            True. ``validate_unit_file_content`` catches that configuration.
        """
        if not self.notify_socket_path:
            logger.debug("systemd notify disabled (no NOTIFY_SOCKET): %s", state)
            return False

        addr = self.notify_socket_path
        if addr.startswith("@"):
            # sd_notify(3): a leading '@' denotes the Linux abstract namespace.
            addr = "\0" + addr[1:]
        elif not addr.startswith("/"):
            logger.warning(
                "NOTIFY_SOCKET=%r is neither an absolute path nor an abstract "
                "namespace socket ('@'); not sending notification.",
                self.notify_socket_path,
            )
            return False

        if not hasattr(socket, "AF_UNIX"):
            logger.warning(
                "AF_UNIX is unavailable on this platform; sd_notify is a no-op. "
                "This is expected off Linux and means the process is not under "
                "systemd supervision."
            )
            return False

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(addr)
                sock.sendall(state.encode("utf-8"))
            return True
        except OSError as exc:
            logger.warning("Failed to send systemd notification: %s", exc)
            return False

    # -- protocol wrappers -------------------------------------------------

    def notify_ready(self, status: str = "Trading bot initialized and ready.") -> bool:
        """Send ``READY=1``. Call only once every start-up dependency is live.

        With ``Type=notify`` systemd holds the unit in ``activating`` until this
        arrives, and holds dependent units with it. Sending it before broker
        authentication succeeds tells systemd a broken service is healthy and
        releases everything ordered ``After=`` this unit.

        Returns:
            True if the datagram was sent. Check it: a bot that believes it
            announced readiness but did not will be killed at
            ``TimeoutStartSec``.
        """
        return self.sd_notify(
            build_notify_message([("READY", "1"), ("STATUS", _sanitize_status(status))])
        )

    def notify_watchdog(self) -> bool:
        """Send ``WATCHDOG=1``, the keep-alive ping.

        Send it from a place whose liveness actually implies the trading loop is
        alive -- see :meth:`notify_watchdog_if_progressing`. A ping emitted by a
        thread that keeps running while the loop is wedged does not fail the
        watchdog; it disables it.
        """
        return self.sd_notify(build_notify_message([("WATCHDOG", "1")]))

    def notify_stopping(self, status: str = "Trading bot shutting down cleanly.") -> bool:
        """Send ``STOPPING=1`` as shutdown begins, before cancelling orders."""
        return self.sd_notify(
            build_notify_message([("STOPPING", "1"), ("STATUS", _sanitize_status(status))])
        )

    def notify_status(self, status: str) -> bool:
        """Send a ``STATUS=`` line only, surfaced by ``systemctl status``."""
        return self.sd_notify(
            build_notify_message([("STATUS", _sanitize_status(status))])
        )

    def notify_extend_timeout(self, seconds: float) -> bool:
        """Ask systemd for ``seconds`` more before it escalates to SIGKILL.

        ``TimeoutStopSec=`` is a hard budget: when it expires the process "will
        be forcibly terminated by SIGKILL", mid-unwind, with open orders still
        live at the broker. If cancellation is genuinely still progressing, send
        this repeatedly -- each message buys another window.

        Args:
            seconds: Positive, finite extension. sd_notify(3) takes
                microseconds; the conversion happens here.

        Raises:
            ValueError: If ``seconds`` is not positive and finite.
        """
        if not seconds > 0 or seconds == float("inf"):
            raise ValueError(
                f"extension must be a positive finite number of seconds, got {seconds!r}"
            )
        usec = int(seconds * 1_000_000)
        return self.sd_notify(build_notify_message([("EXTEND_TIMEOUT_USEC", str(usec))]))

    # -- watchdog ----------------------------------------------------------

    def watchdog_timeout_seconds(self) -> Optional[float]:
        """The watchdog timeout systemd configured, from ``$WATCHDOG_USEC``.

        Mirrors ``sd_watchdog_enabled(3)``: the watchdog counts as enabled only
        when ``WATCHDOG_USEC`` is set and ``WATCHDOG_PID`` is either unset or
        equal to this process's PID. The PID test is what stops a forked child
        from inheriting the belief that it owes systemd pings.

        Returns:
            Timeout in seconds, or None when the watchdog is not enabled for
            this process.
        """
        raw = self._env.get("WATCHDOG_USEC")
        if not raw:
            return None
        try:
            usec = int(raw)
        except ValueError:
            logger.warning(
                "WATCHDOG_USEC=%r is not an integer; treating watchdog as disabled", raw
            )
            return None
        if usec <= 0:
            logger.warning(
                "WATCHDOG_USEC=%r is not positive; treating watchdog as disabled", raw
            )
            return None

        raw_pid = self._env.get("WATCHDOG_PID")
        if raw_pid:
            try:
                if int(raw_pid) != os.getpid():
                    logger.debug(
                        "WATCHDOG_PID=%s does not match this process (%d); the watchdog "
                        "belongs to another process", raw_pid, os.getpid()
                    )
                    return None
            except ValueError:
                logger.warning(
                    "WATCHDOG_PID=%r is not an integer; treating watchdog as disabled", raw_pid
                )
                return None

        return usec / 1_000_000.0

    def watchdog_enabled(self) -> bool:
        """True when this process is expected to send ``WATCHDOG=1``."""
        return self.watchdog_timeout_seconds() is not None

    def watchdog_ping_interval_seconds(self, safety_factor: float = 0.5) -> Optional[float]:
        """How often to ping, derived from the timeout systemd actually set.

        ``sd_watchdog_enabled(3)``: "It is recommended that a daemon sends a
        keep-alive notification message to the service manager every half of the
        time returned here." Deriving the cadence rather than hard-coding it is
        the point -- an operator who lowers ``WatchdogSec=`` in a drop-in should
        not thereby arrange for the bot to be SIGABRTed on a timer.

        Args:
            safety_factor: Fraction of the timeout to wait between pings. Must
                be in ``(0, 1)``. Lower it if the loop's tail latency is close
                to half the timeout.

        Returns:
            Interval in seconds, or None when the watchdog is not enabled.

        Raises:
            ValueError: If ``safety_factor`` is outside ``(0, 1)``.
        """
        if not 0.0 < safety_factor < 1.0:
            raise ValueError(f"safety_factor must be in (0, 1), got {safety_factor!r}")
        timeout = self.watchdog_timeout_seconds()
        if timeout is None:
            return None
        return timeout * safety_factor

    def notify_watchdog_if_progressing(
        self,
        last_progress_monotonic: float,
        max_stall_seconds: float,
        now: Optional[float] = None,
    ) -> bool:
        """Ping only if the trading loop has made progress recently.

        This is the ping a supervised bot should actually use. The naive fix for
        "a slow REST call made us miss the deadline" is to move the ping onto
        its own thread, but that thread stays healthy precisely when the trading
        loop is wedged, so it converts the watchdog from a liveness check on the
        strategy into a liveness check on the pinger. Instead, let the loop
        stamp a monotonic timestamp each iteration and have the pinger refuse to
        vouch for it once that stamp goes stale.

        Args:
            last_progress_monotonic: ``time.monotonic()`` as of the loop's last
                completed iteration. Monotonic, not wall-clock: an NTP step must
                not be able to fake progress or fake a stall.
            max_stall_seconds: Longest gap between iterations still considered
                healthy. Keep it below ``WatchdogSec`` so systemd's own timeout
                is the backstop rather than the primary detector.
            now: Injectable clock reading, for tests.

        Returns:
            True if a ping was sent and accepted by the transport. False if the
            loop is stalled (deliberately withholding the ping) or the transport
            failed.

        Raises:
            ValueError: If ``max_stall_seconds`` is not positive.
        """
        if not max_stall_seconds > 0:
            raise ValueError(
                f"max_stall_seconds must be positive, got {max_stall_seconds!r}"
            )
        current = time.monotonic() if now is None else now
        stall = current - last_progress_monotonic
        # Exactly at the threshold still counts as healthy; only a strictly
        # longer stall withholds the ping.
        if stall > max_stall_seconds:
            logger.error(
                "Withholding WATCHDOG=1: trading loop has not progressed for %.3fs "
                "(limit %.3fs). Letting the systemd watchdog fire is the correct "
                "outcome here.", stall, max_stall_seconds,
            )
            return False
        return self.notify_watchdog()

    # -- pre-market healthcheck -------------------------------------------

    @staticmethod
    def run_premarket_healthcheck(
        secrets_dict: Mapping[str, str],
        broker_connectivity_fn: Callable[[], bool],
        is_holiday_fn: Optional[Callable[[datetime.date], bool]] = None,
        as_of_date: Optional[datetime.date] = None,
        required_secrets: Sequence[str] = DEFAULT_REQUIRED_SECRETS,
        exchange_timezone: Optional[datetime.tzinfo] = None,
    ) -> HealthCheckResult:
        """Run the ``ExecStartPre=`` gate: credentials, calendar, connectivity.

        Fails closed. A calendar lookup that raises is a fault, not an implicit
        "market open" -- if the bot cannot establish that the exchange is
        trading, it must not start.

        Args:
            secrets_dict: Mapping of secret name to value. Empty and
                whitespace-only values count as missing, which is what an unset
                environment variable read through a shell wrapper looks like.
            broker_connectivity_fn: Zero-argument probe returning True when the
                broker answered. **Give it its own timeout.** systemd bounds
                ``ExecStartPre=`` with ``TimeoutStartSec=``, but a probe that
                hangs burns that whole budget before the unit fails.
            is_holiday_fn: Exchange calendar predicate. Omitting it means
                "assume open", which is only safe if something else gates the
                schedule.
            as_of_date: The trading date to evaluate. Supply this, or
                ``exchange_timezone``, whenever the host clock is not on
                exchange time.
            required_secrets: Secret names that must be present and non-empty.
            exchange_timezone: Used to derive today's date in *exchange* local
                time when ``as_of_date`` is not given.

        Returns:
            A :class:`HealthCheckResult`. Use ``is_fault`` to decide the exit
            status and ``market_closed`` to decide whether to trade.

        Raises:
            PreMarketHealthCheckError: If the arguments cannot support a check
                at all (non-mapping secrets, non-callable probe, ``as_of_date``
                that is not a plain date).
        """
        if secrets_dict is None or not isinstance(secrets_dict, Mapping):
            raise PreMarketHealthCheckError(
                f"secrets_dict must be a mapping of secret name to value, got "
                f"{type(secrets_dict).__name__}"
            )
        if not callable(broker_connectivity_fn):
            raise PreMarketHealthCheckError("broker_connectivity_fn must be callable")

        today = _resolve_check_date(as_of_date, exchange_timezone)

        checks: Dict[str, bool] = {}
        details: List[str] = []
        blocking: List[str] = []

        # 1. Credentials. Presence only; the value itself is never logged.
        missing_keys = [
            k for k in required_secrets if not str(secrets_dict.get(k) or "").strip()
        ]
        checks["secrets_present"] = not missing_keys
        if missing_keys:
            message = f"Missing or empty required secrets: {sorted(missing_keys)}"
            details.append(message)
            blocking.append(message)

        # 2. Exchange calendar. A lookup failure is a fault, not an open market.
        market_closed = False
        if is_holiday_fn is None:
            checks["not_market_holiday"] = True
        else:
            try:
                market_closed = bool(is_holiday_fn(today))
                checks["not_market_holiday"] = not market_closed
                if market_closed:
                    details.append(
                        f"Exchange is closed on {today.isoformat()} (holiday calendar)."
                    )
            except Exception as exc:  # noqa: BLE001 - any calendar failure is fail-closed
                checks["not_market_holiday"] = False
                message = (
                    f"Exchange calendar lookup failed for {today.isoformat()}: {exc!r}"
                )
                details.append(message)
                blocking.append(message)

        # 3. Broker connectivity.
        try:
            reachable = bool(broker_connectivity_fn())
            if not reachable:
                message = "Broker connectivity probe returned False (broker unreachable)."
                details.append(message)
                blocking.append(message)
        except Exception as exc:  # noqa: BLE001 - probe implementations vary widely
            reachable = False
            message = f"Broker connectivity probe raised: {exc!r}"
            details.append(message)
            blocking.append(message)
        checks["broker_connectivity"] = reachable

        result = HealthCheckResult(
            passed=all(checks.values()),
            checks=checks,
            details=details,
            blocking_failures=blocking,
            market_closed=market_closed,
            as_of_date=today,
        )
        if blocking:
            logger.error("Pre-market healthcheck FAILED (fault): %s", blocking)
        elif market_closed:
            logger.info(
                "Pre-market healthcheck: exchange closed on %s, not starting.",
                today.isoformat(),
            )
        return result

    # -- unit-file audit ---------------------------------------------------

    @staticmethod
    def validate_unit_file_content(unit_content: str) -> UnitFileValidationReport:
        """Audit a ``.service`` file against trading-bot supervision requirements.

        Args:
            unit_content: The text of the unit file. Prefer ``systemctl cat``
                output over the file on disk: drop-ins under
                ``/etc/systemd/system/<unit>.d/`` override it and are invisible
                here.

        Returns:
            A :class:`UnitFileValidationReport`. It unpacks as
            ``(is_valid, issues)`` for the pre-2.0.0 call shape, and exposes
            ``findings``/``codes`` for programmatic branching. ``is_valid`` is
            True only when there are no findings at all.
        """
        findings: List[UnitFileFinding] = []
        sections = parse_unit_file(unit_content)
        unit = sections.get(_SECTION_UNIT, {})
        service = sections.get(_SECTION_SERVICE, {})

        for stray in _stray_lines(unit_content):
            findings.append(UnitFileFinding(
                code=CODE_MALFORMED_UNIT_FILE,
                severity=SEVERITY_HIGH,
                section=None,
                directive=None,
                description=f"Line is outside any [Section] or carries no '=': {stray!r}",
                remediation="Place every directive inside [Unit], [Service] or [Install]; "
                            "systemd ignores or rejects anything else.",
            ))

        if not service:
            findings.append(UnitFileFinding(
                code=CODE_MALFORMED_UNIT_FILE,
                severity=SEVERITY_CRITICAL,
                section=None,
                directive=None,
                description="No [Service] section found.",
                remediation="A trading-bot unit must be a service unit with a [Service] section.",
            ))
            return _finalise(findings)

        findings.extend(_audit_start_rate_limit(unit, service))
        findings.extend(_audit_restart_policy(service))
        findings.extend(_audit_watchdog(service))
        findings.extend(_audit_resources_and_lifecycle(service))

        return _finalise(findings)


def _resolve_check_date(
    as_of_date: Optional[datetime.date],
    exchange_timezone: Optional[datetime.tzinfo],
) -> datetime.date:
    """Decide which calendar date the healthcheck is about.

    The host clock answers "what date is it here", which is not the question. A
    UTC host asking an IST or US/Eastern calendar near midnight gets the wrong
    trading day, and the failure is silent in both directions: trading on a
    holiday, or refusing to start on a trading day.
    """
    if as_of_date is not None:
        if not isinstance(as_of_date, datetime.date) or isinstance(as_of_date, datetime.datetime):
            raise PreMarketHealthCheckError(
                f"as_of_date must be a datetime.date, got {type(as_of_date).__name__}"
            )
        return as_of_date
    if exchange_timezone is not None:
        return datetime.datetime.now(exchange_timezone).date()
    logger.warning(
        "Pre-market healthcheck is using the host's local date. Pass as_of_date or "
        "exchange_timezone unless this host's clock is on exchange time -- near "
        "midnight the two disagree and the holiday check silently answers the "
        "wrong day."
    )
    return datetime.date.today()


def _finalise(findings: List[UnitFileFinding]) -> UnitFileValidationReport:
    ordered = tuple(sorted(
        findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.code)
    ))
    return UnitFileValidationReport(is_valid=not ordered, findings=ordered)


def _audit_start_rate_limit(
    unit: Dict[str, List[str]], service: Dict[str, List[str]]
) -> List[UnitFileFinding]:
    """Check the crash-loop brake, including which section it was written in.

    systemd's directive table (``load-fragment-gperf.gperf.in``) accepts
    ``Service.StartLimitInterval``, ``Service.StartLimitBurst`` and
    ``Service.StartLimitAction`` only "for compatibility, they moved into Unit".
    ``Service.StartLimitIntervalSec`` -- the *current* spelling -- is absent from
    that table entirely, so writing it in ``[Service]`` is an unknown key:
    logged once at load time and otherwise ignored, leaving the unit on
    ``DefaultStartLimitIntervalSec`` (10s).
    """
    findings: List[UnitFileFinding] = []

    if "StartLimitIntervalSec" in service:
        findings.append(UnitFileFinding(
            code=CODE_START_LIMIT_IGNORED_IN_SERVICE_SECTION,
            severity=SEVERITY_CRITICAL,
            section=_SECTION_SERVICE,
            directive="StartLimitIntervalSec",
            description="StartLimitIntervalSec= is set in [Service], where systemd does not "
                        "recognise it. It is ignored, and the unit falls back to "
                        "DefaultStartLimitIntervalSec (10s) -- far shorter than intended, "
                        "which lets a crash loop run essentially unbounded.",
            remediation="Move StartLimitIntervalSec= to the [Unit] section.",
        ))
    for legacy in ("StartLimitInterval", "StartLimitBurst", "StartLimitAction"):
        if legacy in service:
            findings.append(UnitFileFinding(
                code=CODE_START_LIMIT_LEGACY_SERVICE_SECTION,
                severity=SEVERITY_HIGH,
                section=_SECTION_SERVICE,
                directive=legacy,
                description=f"{legacy}= is in [Service]. systemd honours it there only as a "
                            f"compatibility alias; the supported location is [Unit].",
                remediation=f"Move {legacy}= to the [Unit] section (and prefer the "
                            f"StartLimitIntervalSec= spelling for the interval).",
            ))

    interval_raw = _last(unit, "StartLimitIntervalSec") or _last(unit, "StartLimitInterval")
    burst_raw = _last(unit, "StartLimitBurst")

    if burst_raw is None and "StartLimitBurst" not in service:
        findings.append(UnitFileFinding(
            code=CODE_MISSING_START_RATE_LIMIT,
            severity=SEVERITY_HIGH,
            section=_SECTION_UNIT,
            directive="StartLimitBurst",
            description="No StartLimitBurst= anywhere in the unit; the crash-loop brake is "
                        "whatever the manager default happens to be.",
            remediation="Set StartLimitBurst= explicitly in [Unit] rather than inheriting "
                        "DefaultStartLimitBurst.",
        ))
    if (
        interval_raw is None
        and "StartLimitInterval" not in service
        and "StartLimitIntervalSec" not in service
    ):
        findings.append(UnitFileFinding(
            code=CODE_MISSING_START_RATE_LIMIT,
            severity=SEVERITY_HIGH,
            section=_SECTION_UNIT,
            directive="StartLimitIntervalSec",
            description="No StartLimitIntervalSec= in [Unit]; the rate-limit window defaults to "
                        "DefaultStartLimitIntervalSec (10s), which is too short to catch a bot "
                        "crash-looping against a broker outage on a multi-second RestartSec.",
            remediation="Set StartLimitIntervalSec= explicitly in [Unit], sized to the outage "
                        "you want to survive.",
        ))

    # Arithmetic reachability of the limiter. Derived from documented semantics
    # (a sliding window over start attempts spaced by RestartSec), not itself a
    # published systemd rule -- see references/standards.md.
    interval_sec = (
        parse_systemd_timespan(interval_raw) if interval_raw is not None
        else _DEFAULT_START_LIMIT_INTERVAL_SEC
    )
    restart_sec_raw = _last(service, "RestartSec")
    restart_sec = parse_systemd_timespan(restart_sec_raw) if restart_sec_raw is not None else None

    if restart_sec_raw is not None and restart_sec is None:
        findings.append(UnitFileFinding(
            code=CODE_UNPARSEABLE_DIRECTIVE,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_SERVICE,
            directive="RestartSec",
            description=f"RestartSec={restart_sec_raw!r} is not a parseable systemd time span.",
            remediation="Use a systemd.time(7) span such as '10s', '1min 30s', or a bare "
                        "number of seconds.",
        ))
    if interval_raw is not None and interval_sec is None:
        findings.append(UnitFileFinding(
            code=CODE_UNPARSEABLE_DIRECTIVE,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_UNIT,
            directive="StartLimitIntervalSec",
            description=f"StartLimitIntervalSec={interval_raw!r} is not a parseable systemd "
                        f"time span.",
            remediation="Use a systemd.time(7) span such as '600s' or '10min'.",
        ))

    effective_burst_raw = burst_raw if burst_raw is not None else _last(service, "StartLimitBurst")
    burst = _DEFAULT_START_LIMIT_BURST
    if effective_burst_raw is not None:
        try:
            burst = int(effective_burst_raw)
        except ValueError:
            findings.append(UnitFileFinding(
                code=CODE_UNPARSEABLE_DIRECTIVE,
                severity=SEVERITY_MEDIUM,
                section=_SECTION_UNIT,
                directive="StartLimitBurst",
                description=f"StartLimitBurst={effective_burst_raw!r} is not an integer.",
                remediation="Set StartLimitBurst= to a whole number of permitted starts.",
            ))
            burst = _DEFAULT_START_LIMIT_BURST

    # A zero in either field switches the limiter off outright: systemd's
    # ratelimit_configured() is `rl->interval > 0 && rl->burst > 0`, and an
    # unconfigured limit reports "below limit" unconditionally. This is a
    # documented way to disable rate limiting -- and for a bot that can crash-loop
    # against a broker it should never be arrived at by accident.
    if interval_sec == 0.0 or burst == 0:
        findings.append(UnitFileFinding(
            code=CODE_START_LIMIT_UNREACHABLE,
            severity=SEVERITY_HIGH,
            section=_SECTION_UNIT,
            directive="StartLimitIntervalSec" if interval_sec == 0.0 else "StartLimitBurst",
            description=(
                "Start rate limiting is disabled: systemd treats a limit as configured only "
                "when both the interval and the burst are greater than zero, and this unit "
                "sets one of them to 0. The bot may restart without any bound."
            ),
            remediation="Set both StartLimitIntervalSec= and StartLimitBurst= to positive "
                        "values, or record explicitly why this bot is exempt from a "
                        "crash-loop brake.",
        ))
    elif (
        restart_sec is not None
        and interval_sec is not None
        and interval_sec != float("inf")
        and burst > 1
        and restart_sec * (burst - 1) >= interval_sec
    ):
        findings.append(UnitFileFinding(
            code=CODE_START_LIMIT_UNREACHABLE,
            severity=SEVERITY_HIGH,
            section=_SECTION_UNIT,
            directive="StartLimitIntervalSec",
            description=(
                f"The start limit can never trip: {burst} attempts spaced by "
                f"RestartSec={restart_sec:g}s span at least {restart_sec * (burst - 1):g}s, "
                f"which is not shorter than the {interval_sec:g}s window. The bot will "
                f"restart forever against a broker outage."
            ),
            remediation=(
                "Widen StartLimitIntervalSec= beyond RestartSec x (StartLimitBurst - 1), "
                "or lower StartLimitBurst=."
            ),
        ))

    return findings


def _audit_restart_policy(service: Dict[str, List[str]]) -> List[UnitFileFinding]:
    findings: List[UnitFileFinding] = []
    restart = _last(service, "Restart")

    if restart is None or restart == "no":
        findings.append(UnitFileFinding(
            code=CODE_MISSING_RESTART_POLICY,
            severity=SEVERITY_HIGH,
            section=_SECTION_SERVICE,
            directive="Restart",
            description="No automatic restart is configured; a crashed bot stays down until "
                        "someone notices.",
            remediation="Set Restart=on-failure (which also covers watchdog timeouts) together "
                        "with a [Unit] start rate limit.",
        ))
    elif restart == "always":
        findings.append(UnitFileFinding(
            code=CODE_UNBOUNDED_RESTART_POLICY,
            severity=SEVERITY_HIGH,
            section=_SECTION_SERVICE,
            directive="Restart",
            description="Restart=always restarts the bot even after a deliberate clean exit, so "
                        "a bot that shuts itself down (holiday, kill switch, decommission) is "
                        "brought straight back up.",
            remediation="Use Restart=on-failure, which covers non-zero exits, fatal signals and "
                        "watchdog timeouts but respects a clean exit.",
        ))

    return findings


def _audit_watchdog(service: Dict[str, List[str]]) -> List[UnitFileFinding]:
    """Check that WATCHDOG=1 pings can actually reach systemd.

    ``NotifyAccess=`` "Takes one of none (the default), main, exec or all"; with
    ``none``, "all status update messages are ignored". For ``Type=notify`` (and
    ``notify-reload``) systemd forces it to ``main`` when unset or ``none``. Any
    other type with ``WatchdogSec=`` and no explicit ``NotifyAccess=`` therefore
    has every ping discarded and is terminated once per ``WatchdogSec``.
    """
    findings: List[UnitFileFinding] = []
    watchdog_raw = _last(service, "WatchdogSec")
    service_type = (_last(service, "Type") or "simple").strip()
    notify_access = (_last(service, "NotifyAccess") or "").strip()

    if watchdog_raw is None:
        findings.append(UnitFileFinding(
            code=CODE_MISSING_WATCHDOG,
            severity=SEVERITY_HIGH,
            section=_SECTION_SERVICE,
            directive="WatchdogSec",
            description="No WatchdogSec=; a bot whose event loop wedges while the process stays "
                        "alive is indistinguishable from a healthy one.",
            remediation="Set WatchdogSec= and send WATCHDOG=1 from the trading loop.",
        ))
        return findings

    if parse_systemd_timespan(watchdog_raw) is None:
        findings.append(UnitFileFinding(
            code=CODE_UNPARSEABLE_DIRECTIVE,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_SERVICE,
            directive="WatchdogSec",
            description=f"WatchdogSec={watchdog_raw!r} is not a parseable systemd time span.",
            remediation="Use a systemd.time(7) span such as '30s' or a bare number of seconds.",
        ))

    if service_type not in _NOTIFY_SERVICE_TYPES and notify_access in ("", "none"):
        findings.append(UnitFileFinding(
            code=CODE_WATCHDOG_PINGS_IGNORED,
            severity=SEVERITY_CRITICAL,
            section=_SECTION_SERVICE,
            directive="NotifyAccess",
            description=(
                f"WatchdogSec= is set but Type={service_type} and NotifyAccess= is "
                f"{notify_access or 'unset'} (default: none), so systemd ignores every "
                f"WATCHDOG=1 the bot sends and will terminate it once per watchdog interval."
            ),
            remediation="Use Type=notify (systemd then forces NotifyAccess=main), or set "
                        "NotifyAccess= explicitly.",
        ))

    return findings


def _audit_resources_and_lifecycle(service: Dict[str, List[str]]) -> List[UnitFileFinding]:
    findings: List[UnitFileFinding] = []

    if not service.get("ExecStart"):
        findings.append(UnitFileFinding(
            code=CODE_MISSING_EXEC_START,
            severity=SEVERITY_CRITICAL,
            section=_SECTION_SERVICE,
            directive="ExecStart",
            description="No ExecStart=; the unit starts nothing.",
            remediation="Set ExecStart= to the bot's entry point, with an absolute "
                        "interpreter path.",
        ))

    if not service.get("ExecStartPre"):
        findings.append(UnitFileFinding(
            code=CODE_MISSING_PREMARKET_HEALTHCHECK,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_SERVICE,
            directive="ExecStartPre",
            description="No ExecStartPre= gate; the bot will start and authenticate before "
                        "anything checks that credentials and broker connectivity are present.",
            remediation="Add an ExecStartPre= wrapper around run_premarket_healthcheck().",
        ))

    if not (service.get("MemoryMax") or service.get("MemoryHigh")):
        findings.append(UnitFileFinding(
            code=CODE_MISSING_MEMORY_LIMIT,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_SERVICE,
            directive="MemoryMax",
            description="Neither MemoryMax= nor MemoryHigh= is set; an order-book or tick-buffer "
                        "leak can take the whole host down rather than one unit.",
            remediation="Set MemoryHigh= as the throttle and MemoryMax= as the hard ceiling "
                        "(systemd.resource-control(5): MemoryMax is the 'last line of defense').",
        ))

    if not service.get("TimeoutStopSec"):
        findings.append(UnitFileFinding(
            code=CODE_MISSING_STOP_TIMEOUT,
            severity=SEVERITY_MEDIUM,
            section=_SECTION_SERVICE,
            directive="TimeoutStopSec",
            description="No explicit TimeoutStopSec=; how long the bot gets to cancel live "
                        "orders before SIGKILL is left to the manager default.",
            remediation="Set TimeoutStopSec= from a measured worst-case unwind, and send "
                        "EXTEND_TIMEOUT_USEC= if cancellation is still progressing.",
        ))

    return findings
