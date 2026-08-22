"""
clock-synchronization-ptp-for-trading-hosts:
Parsing and health evaluation of ``linuxptp`` (``ptp4l`` + ``phc2sys``)
telemetry for hosts that must stamp reportable events against UTC.

Scope
-----
This module reads what the sync stack *reports*. It does not configure PTP, it
does not discipline any clock, and it cannot see the one failure that matters
most - a clock that is precisely synchronized to the wrong timescale. See
"What a small offset does not prove" below before treating a green result as
evidence of anything.

The two offsets are serial, not alternatives
--------------------------------------------
``ptp4l`` reports the offset between the grandmaster and the NIC's PTP
Hardware Clock. ``phc2sys`` reports the offset between the PHC and the clock
the application actually reads (normally ``CLOCK_REALTIME``). An event stamped
from ``CLOCK_REALTIME`` carries **both** errors, in series. Taking the maximum
of the two - as this module previously did - understates the end-to-end error
of the timestamp that is actually written to the record. ``combined_offset_ns``
(the sum of the magnitudes) is the conservative bound and is what gates the
compliance verdict; ``max_offset_ns`` is retained for continuity.

What a small offset does not prove
----------------------------------
``ptp4l`` keeps the PHC on the **PTP timescale (TAI)**, which does not apply
leap seconds. ``phc2sys`` only converts to UTC when told to: per phc2sys(8),
``-w`` "keep[s] the offset between the sink and source times updated according
to the currentUtcOffset value obtained from ptp4l" when ``-O`` is not used.
Run without ``-w`` and without ``-O``, ``CLOCK_REALTIME`` is disciplined onto
TAI and sits a whole number of seconds away from UTC (37 s at the time of
writing), while every daemon reports single-digit-nanosecond offsets and this
module reports ``mifid_compliant: True``. Offset telemetry measures agreement
with the source, never correctness of the timescale. Verify the timescale out
of band against an independent UTC reference.

Units
-----
Everything in this module is **nanoseconds**, matching ``linuxptp`` log output
(``master offset``, ``path delay`` in ns; ``freq`` in parts per billion). The
sibling skill ``clock-drift-monitoring-alerting-thresholds`` speaks
microseconds; convert at the boundary.

Thresholds are jurisdictional, not universal
--------------------------------------------
``MIFID_HFT_MAX_DIVERGENCE_NS`` (100 us) is one row of one EU table -
Commission Delegated Regulation (EU) 2017/574 ("RTS 25"), Annex Table 2, for a
member using a high frequency algorithmic trading technique. Non-HFT EU algo
flow is bound to 1 ms; a US CAT reporter is bound by FINRA Rule 6820 to 50 ms.
``target_hft_offset_ns`` is an **engineering target with no regulatory basis**.
See ``references/standards.md``.

Silence is not health
---------------------
State here is sticky: a dead ``ptp4l`` stops emitting offsets, so the last
``s2`` and the last small offset persist forever and the host reports compliant
with no time synchronization at all. Set ``max_sample_age_s`` to make absence
of telemetry fail closed. It defaults to ``None`` (disabled) only because the
correct value is your log interval times a tolerance, which this module cannot
know.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Sourced regulatory ceilings (nanoseconds). See references/standards.md. ---
# RTS 25 Annex Table 2, member/participant using an HFT technique.
MIFID_HFT_MAX_DIVERGENCE_NS = 100_000.0
# RTS 25 Annex Table 2, "any other trading activity".
MIFID_OTHER_ALGO_MAX_DIVERGENCE_NS = 1_000_000.0
# FINRA Rule 6820 / CAT NMS Plan, Industry Member business clocks.
CAT_MAX_DIVERGENCE_NS = 50_000_000.0

# Servo states printed in the offset line: s0 unlocked, s1 clock step, s2 locked.
LOCKED_SERVO_STATES = frozenset({"s2"})
# IEEE 1588 port states meaning "synchronizing to a selected source".
# ``SLAVE`` is the classic linuxptp spelling; ``TIME_RECEIVER``/``CLIENT`` are
# the IEEE 1588-2019 replacements. Any state outside this set fails closed.
SYNCHRONIZED_PORT_STATES = frozenset({"SLAVE", "TIME_RECEIVER", "CLIENT"})

# Log lines carry a free-form ``message_tag`` (ptp4l(8): "The tag which is added
# to all messages printed to the standard output or system log"), so nothing may
# be assumed about what sits between the daemon timestamp and the keyword.
_PREFIX = r"{daemon}\[[^\]]*\]:.*?"

_PTP4L_OFFSET_RE = re.compile(
    _PREFIX.format(daemon="ptp4l")
    + r"\bmaster\s+offset\s+([+-]?\d+)\s+(\S+)\s+freq\s+([+-]?\d+)"
    # Path delay is optional and MAY BE NEGATIVE. A negative path delay is a
    # real, reportable symptom of bad hardware timestamps; a parser that
    # rejects the line drops exactly the samples that matter most.
    r"(?:\s+path\s+delay\s+([+-]?\d+))?"
)

_PHC2SYS_OFFSET_RE = re.compile(
    _PREFIX.format(daemon="phc2sys")
    # "phc offset" when syncing a system clock from a PHC, "sys offset" when
    # syncing a PHC from the system clock (phc2sys -a without -r).
    + r"\b(?:phc|sys)\s+offset\s+([+-]?\d+)\s+(\S+)\s+freq\s+([+-]?\d+)"
    r"(?:\s+delay\s+([+-]?\d+))?"
)

# summary_interval / phc2sys -u emit aggregate lines carrying no servo state.
# rms and max are unsigned magnitudes.
_SUMMARY_RE = re.compile(
    r"(ptp4l|phc2sys)\[[^\]]*\]:.*?\brms\s+(\d+)\s+max\s+(\d+)\s+freq\s+([+-]?\d+)"
)

_PORT_STATE_RE = re.compile(
    _PREFIX.format(daemon="ptp4l")
    + r"\bport\s+\d+[^:]*:\s+([A-Z_]+)\s+to\s+([A-Z_]+)\s+on\s+\S+"
)

# Cheap pre-filter used to notice telemetry we failed to parse.
_LOOKS_LIKE_TELEMETRY_RE = re.compile(
    r"(?:ptp4l|phc2sys)\[[^\]]*\]:.*?\b(?:offset|rms)\b"
)

_MAX_UNPARSED_WARNINGS = 10


@dataclass(frozen=True)
class PtpTelemetrySample:
    """One parsed telemetry line.

    ``offset_ns`` is signed for instantaneous samples. For summary samples
    (``is_summary=True``) it carries the RMS magnitude, which is unsigned and
    is *not* interchangeable with an instantaneous offset; ``max_offset_ns``
    then holds the worst case over the summary window and is the figure to
    compare against a divergence ceiling.
    """

    daemon: str
    offset_ns: float
    freq_adj_ppb: float
    path_delay_ns: Optional[float] = None
    ptp_state: Optional[str] = None
    max_offset_ns: Optional[float] = None
    is_summary: bool = False


class PtpClockSyncManager:
    """
    Parses ``ptp4l`` and ``phc2sys`` log output, tracks synchronization state,
    and evaluates the end-to-end offset against a configured divergence ceiling.

    Not thread-safe: drive it from a single reader loop, or hold your own lock.

    Args:
        max_allowed_offset_ns: Divergence ceiling for the compliance verdict.
            Defaults to the RTS 25 HFT row (100 us). Set it from the row that
            binds *your* activity and jurisdiction, and leave headroom below
            the ceiling for detection latency.
        target_hft_offset_ns: Internal engineering target. No regulatory basis.
        max_sample_age_s: Telemetry older than this is treated as absent and
            fails the sync check. ``None`` disables staleness detection, which
            means a dead daemon reports as healthy indefinitely.
        clock: Monotonic time source, injectable for tests. Never the wall
            clock - this module exists because the wall clock is suspect.
    """

    def __init__(
        self,
        max_allowed_offset_ns: float = MIFID_HFT_MAX_DIVERGENCE_NS,
        target_hft_offset_ns: float = 1000.0,
        max_sample_age_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not max_allowed_offset_ns > 0:
            raise ValueError("max_allowed_offset_ns must be positive")
        if not target_hft_offset_ns > 0:
            raise ValueError("target_hft_offset_ns must be positive")
        if max_sample_age_s is not None and not max_sample_age_s > 0:
            raise ValueError("max_sample_age_s must be positive or None")

        self.max_allowed_offset_ns = max_allowed_offset_ns
        self.target_hft_offset_ns = target_hft_offset_ns
        self.max_sample_age_s = max_sample_age_s
        self._clock = clock

        self.ptp4l_state = "UNKNOWN"
        self.phc2sys_state = "UNKNOWN"
        self.ptp4l_port_state = "UNKNOWN"
        self.latest_ptp4l_offset: Optional[float] = None
        self.latest_phc2sys_offset: Optional[float] = None
        self.unparsed_telemetry_lines = 0

        self._ptp4l_seen_at: Optional[float] = None
        self._phc2sys_seen_at: Optional[float] = None
        self._unparsed_warnings = 0

        if max_sample_age_s is None:
            logger.warning(
                "PtpClockSyncManager built with max_sample_age_s=None: a PTP "
                "daemon that dies will leave the last good state latched and "
                "this manager will keep reporting it as synchronized."
            )

    # -- parsing ---------------------------------------------------------

    def parse_log_line(self, line: str) -> Optional[PtpTelemetrySample]:
        """
        Parse one ``ptp4l``/``phc2sys`` log line, update internal state, and
        return the structured sample.

        Returns ``None`` for lines carrying no offset telemetry. Port-state
        transition lines update ``ptp4l_port_state`` and also return ``None``;
        a line that looks like telemetry but does not parse increments
        ``unparsed_telemetry_lines`` rather than disappearing silently.
        """
        if not isinstance(line, str):
            raise TypeError(f"line must be str, got {type(line).__name__}")

        # Fast reject. These patterns open with a lazy ``.*?``, so running five
        # of them over every line of a mixed syslog stream is both wasted work
        # and a backtracking hazard on long non-PTP lines.
        if "ptp4l[" not in line and "phc2sys[" not in line:
            return None

        match = _PTP4L_OFFSET_RE.search(line)
        if match:
            delay = match.group(4)
            return self._record(
                PtpTelemetrySample(
                    daemon="ptp4l",
                    offset_ns=float(match.group(1)),
                    freq_adj_ppb=float(match.group(3)),
                    path_delay_ns=None if delay is None else float(delay),
                    ptp_state=match.group(2),
                )
            )

        match = _PHC2SYS_OFFSET_RE.search(line)
        if match:
            delay = match.group(4)
            return self._record(
                PtpTelemetrySample(
                    daemon="phc2sys",
                    offset_ns=float(match.group(1)),
                    freq_adj_ppb=float(match.group(3)),
                    path_delay_ns=None if delay is None else float(delay),
                    ptp_state=match.group(2),
                )
            )

        match = _SUMMARY_RE.search(line)
        if match:
            # No servo state on a summary line, so state is left untouched
            # rather than invented. Compare max, not rms, against a ceiling.
            return self._record(
                PtpTelemetrySample(
                    daemon=match.group(1),
                    offset_ns=float(match.group(2)),
                    freq_adj_ppb=float(match.group(4)),
                    max_offset_ns=float(match.group(3)),
                    is_summary=True,
                )
            )

        match = _PORT_STATE_RE.search(line)
        if match:
            self.ptp4l_port_state = match.group(2)
            logger.info("ptp4l port state %s -> %s", match.group(1), match.group(2))
            return None

        if _LOOKS_LIKE_TELEMETRY_RE.search(line):
            self.unparsed_telemetry_lines += 1
            if self._unparsed_warnings < _MAX_UNPARSED_WARNINGS:
                self._unparsed_warnings += 1
                logger.warning("unparsed PTP telemetry line: %s", line.strip())
        return None

    def _record(self, sample: PtpTelemetrySample) -> PtpTelemetrySample:
        now = self._clock()
        # A summary line reports a window, so the worst case in that window is
        # the number that must clear the ceiling.
        observed = (
            sample.max_offset_ns
            if sample.is_summary and sample.max_offset_ns is not None
            else sample.offset_ns
        )
        if sample.daemon == "ptp4l":
            self.latest_ptp4l_offset = observed
            self._ptp4l_seen_at = now
            if sample.ptp_state is not None:
                self.ptp4l_state = sample.ptp_state
        else:
            self.latest_phc2sys_offset = observed
            self._phc2sys_seen_at = now
            if sample.ptp_state is not None:
                self.phc2sys_state = sample.ptp_state
        return sample

    # -- evaluation ------------------------------------------------------

    def _is_stale(self, seen_at: Optional[float], now: float) -> bool:
        if seen_at is None:
            return True
        if self.max_sample_age_s is None:
            return False
        return (now - seen_at) > self.max_sample_age_s

    def _servo_locked(self, state: str) -> bool:
        return state in LOCKED_SERVO_STATES or state in SYNCHRONIZED_PORT_STATES

    def evaluate_compliance(self) -> Dict[str, object]:
        """
        Evaluate the current sync state. Fails closed: missing, stale or
        unlocked telemetry is non-compliant, never "no breach detected".

        ``combined_offset_ns`` (|ptp4l| + |phc2sys|) bounds the end-to-end
        error of a timestamp read from ``CLOCK_REALTIME`` and is what gates
        ``mifid_compliant``/``hft_ready``. ``max_offset_ns`` is the larger of
        the two legs and is reported for continuity only - it is not a bound on
        the error of the recorded timestamp.
        """
        now = self._clock()
        ptp4l_stale = self._is_stale(self._ptp4l_seen_at, now)
        phc2sys_stale = self._is_stale(self._phc2sys_seen_at, now)

        reasons: List[str] = []
        if ptp4l_stale:
            reasons.append(
                "no ptp4l telemetry"
                if self._ptp4l_seen_at is None
                else "ptp4l telemetry stale"
            )
        if phc2sys_stale:
            reasons.append(
                "no phc2sys telemetry"
                if self._phc2sys_seen_at is None
                else "phc2sys telemetry stale"
            )
        if not self._servo_locked(self.ptp4l_state):
            reasons.append(f"ptp4l servo state {self.ptp4l_state}")
        if not self._servo_locked(self.phc2sys_state):
            reasons.append(f"phc2sys servo state {self.phc2sys_state}")
        # Port state is only judged once a transition has actually been
        # observed; a caller feeding offset lines alone must not be failed for
        # a state that was never reported.
        if self.ptp4l_port_state != "UNKNOWN" and (
            self.ptp4l_port_state not in SYNCHRONIZED_PORT_STATES
        ):
            reasons.append(f"ptp4l port state {self.ptp4l_port_state}")

        is_synced = not reasons

        ptp4l_abs = (
            abs(self.latest_ptp4l_offset)
            if self.latest_ptp4l_offset is not None and not ptp4l_stale
            else float("inf")
        )
        phc2sys_abs = (
            abs(self.latest_phc2sys_offset)
            if self.latest_phc2sys_offset is not None and not phc2sys_stale
            else float("inf")
        )

        max_offset = max(ptp4l_abs, phc2sys_abs)
        combined_offset = ptp4l_abs + phc2sys_abs

        if combined_offset > self.max_allowed_offset_ns:
            reasons.append(
                f"combined offset {combined_offset:.0f}ns exceeds ceiling "
                f"{self.max_allowed_offset_ns:.0f}ns"
            )

        mifid_compliant = is_synced and combined_offset <= self.max_allowed_offset_ns
        hft_ready = is_synced and combined_offset <= self.target_hft_offset_ns

        return {
            "is_synced": is_synced,
            "max_offset_ns": max_offset,
            "combined_offset_ns": combined_offset,
            "ptp4l_offset_ns": self.latest_ptp4l_offset,
            "phc2sys_offset_ns": self.latest_phc2sys_offset,
            "mifid_compliant": mifid_compliant,
            "hft_ready": hft_ready,
            "ptp4l_state": self.ptp4l_state,
            "phc2sys_state": self.phc2sys_state,
            "ptp4l_port_state": self.ptp4l_port_state,
            "telemetry_stale": ptp4l_stale or phc2sys_stale,
            "unparsed_telemetry_lines": self.unparsed_telemetry_lines,
            "reasons": tuple(reasons),
        }
