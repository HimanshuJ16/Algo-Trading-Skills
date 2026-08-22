"""
clock-drift-monitoring-alerting-thresholds:
Continuous evaluation of PTP clock-offset telemetry against the MiFID II
RTS 25 business-clock accuracy limits, with an automated halt on breach.

What this actually enforces
---------------------------
Commission Delegated Regulation (EU) 2017/574 ("RTS 25"), Annex Table 2, sets
the maximum divergence from UTC for a *member or participant of an EU trading
venue* by the type of trading activity:

  - high frequency algorithmic trading technique .... 100 microseconds
  - any other trading activity ..................... 1 millisecond
  - voice / RFQ with human intervention / negotiated  1 second

The 100 microsecond figure this module defaults to is therefore **not a
universal clock-accuracy rule**. It is the HFT row of one EU table. A firm
running algorithmic but non-HFT flow is held to 1 millisecond, and a US CAT
reporter is held to 50 *milliseconds* by FINRA Rule 6820 - 500x looser. Setting
``critical_threshold_us=100`` for a book that is not running an HFT technique
into an EU venue is a self-inflicted outage, not compliance. See
``references/standards.md`` for the sourced figures.

Units: this module speaks microseconds
--------------------------------------
``ptp4l`` reports ``master offset`` in **nanoseconds**. Passing a raw ptp4l
offset straight into :meth:`ClockDriftMonitor.process_telemetry` under-reports
drift by a factor of 1000 - a genuine 120us breach arrives as 120ns and reads
HEALTHY forever. Convert with :func:`offset_us_from_ptp4l_ns`.

Why the timers use a monotonic clock
------------------------------------
Holdover duration and telemetry staleness are measured with
:func:`time.monotonic`, never with the wall clock. This monitor exists because
the wall clock is suspect; timing a clock fault with the faulty clock is
circular, and a step correction mid-holdover would silently reset or overshoot
the grace period.

Fail-closed defaults
--------------------
``holdover_grace_s`` defaults to ``0.0`` - loss of the grandmaster halts
trading immediately. Holdover tolerance is a property of the host's oscillator,
not something this module can guess, so there is no safe non-zero default.
Derive it from the measured drift rate of your own oscillator.
``max_telemetry_age_s`` defaults to ``None``, which disables staleness
detection; a monitor that only ever reacts to telemetry it receives cannot
detect the most common failure of all, which is the PTP daemon dying and the
telemetry simply stopping. Configure it.
"""
import logging
import math
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# RTS 25 Annex Table 2 rows, in microseconds. Sourced, not tuned.
MIFID_HFT_MAX_DIVERGENCE_US = 100.0
MIFID_OTHER_ALGO_MAX_DIVERGENCE_US = 1_000.0
# FINRA Rule 6820 / CAT NMS Plan, Industry Member business clocks.
CAT_MAX_DIVERGENCE_US = 50_000.0


class ClockTelemetryError(ValueError):
    """
    Raised when a telemetry reading is not usable.

    NaN is the specific hazard this guards. ``abs(nan) >= 100`` is ``False``
    and so is ``abs(nan) >= 50``, so an unparsed or corrupted offset that
    reaches the threshold comparisons unchecked is classified HEALTHY and
    trading continues against an unknown clock. Rejecting the reading loudly
    is the only safe handling.
    """


class PtpState(Enum):
    """
    Normalized PTP synchronization state.

    This is an abstraction over the daemon, not a mirror of it. ``ptp4l``
    exposes IEEE 1588 *port* states (INITIALIZING, LISTENING, UNCALIBRATED,
    SLAVE, MASTER, PASSIVE, FAULTY, DISABLED) and servo states (s0 unlocked,
    s1 clock step, s2 locked); it has no HOLDOVER state of its own - holdover
    is a grandmaster/boundary-clock concept. Mapping your daemon's output onto
    these three values is the integrator's job; ``references/workflows.md``
    gives the mapping this skill assumes.
    """

    LOCKED = "LOCKED"
    HOLDOVER = "HOLDOVER"
    UNLOCKED = "UNLOCKED"


class MonitorStatus(Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def offset_us_from_ptp4l_ns(offset_ns: float) -> float:
    """
    Convert a ``ptp4l``/``phc2sys`` ``master offset`` reading to microseconds.

    ptp4l logs the offset in nanoseconds. This exists so the conversion is
    written down once rather than open-coded - or forgotten - at each call site.
    """
    if isinstance(offset_ns, bool) or not isinstance(offset_ns, (int, float)):
        raise ClockTelemetryError(f"offset_ns must be a real number, got {offset_ns!r}")
    if not math.isfinite(offset_ns):
        raise ClockTelemetryError(f"offset_ns must be finite, got {offset_ns!r}")
    return float(offset_ns) / 1000.0


class ClockDriftMonitor:
    """
    Monitors PTP offset and state, enforcing a configured maximum divergence
    from UTC and triggering an automated halt on breach.

    The monitor latches: once it halts trading it stays halted and ignores
    subsequent telemetry until :meth:`reset` is called deliberately. That is
    intentional - a clock that breached and then drifted back inside the limit
    has still stamped non-compliant times on reportable events, and those
    records need remediation before order origination resumes.

    Not thread-safe. Drive it from a single polling loop, or guard it with
    your own lock; concurrent calls can race on the latch and fire the kill
    switch callback twice.
    """

    def __init__(
        self,
        kill_switch_callback: Callable[[str], None],
        warning_threshold_us: float = 50.0,
        critical_threshold_us: float = MIFID_HFT_MAX_DIVERGENCE_US,
        holdover_grace_s: float = 0.0,
        max_telemetry_age_s: Optional[float] = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        kill_switch_callback: called once, with a reason string, on breach.
        warning_threshold_us: non-blocking alert level, absolute microseconds.
        critical_threshold_us: halt level, absolute microseconds. Defaults to
            the RTS 25 HFT limit. Consider setting it *below* the regulatory
            figure: a halt that fires exactly at the limit fires after
            non-compliant timestamps have already been written.
        holdover_grace_s: how long the host may run in HOLDOVER before halting.
            Defaults to 0.0 (halt immediately). Derive it from the oscillator's
            drift rate: ``grace = critical_threshold_us / drift_us_per_second``.
        max_telemetry_age_s: if set, :meth:`check_liveness` halts when no
            telemetry has arrived for this long.
        monotonic_clock: injectable time source for tests. Must be monotonic.
        """
        if not callable(kill_switch_callback):
            raise TypeError("kill_switch_callback must be callable")
        for label, value in (
            ("warning_threshold_us", warning_threshold_us),
            ("critical_threshold_us", critical_threshold_us),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label} must be a real number, got {value!r}")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and positive, got {value!r}")
        if warning_threshold_us >= critical_threshold_us:
            # Otherwise the WARNING tier is unreachable and the first signal
            # operations ever gets is the halt itself.
            raise ValueError(
                f"warning_threshold_us ({warning_threshold_us}) must be below "
                f"critical_threshold_us ({critical_threshold_us})")
        if not math.isfinite(holdover_grace_s) or holdover_grace_s < 0:
            raise ValueError(
                f"holdover_grace_s must be finite and >= 0, got {holdover_grace_s!r}")
        if max_telemetry_age_s is not None and (
                not math.isfinite(max_telemetry_age_s) or max_telemetry_age_s <= 0):
            raise ValueError(
                f"max_telemetry_age_s must be finite and > 0, got {max_telemetry_age_s!r}")

        self.warning_threshold_us = float(warning_threshold_us)
        self.critical_threshold_us = float(critical_threshold_us)
        self.holdover_grace_s = float(holdover_grace_s)
        self.max_telemetry_age_s = max_telemetry_age_s
        self.kill_switch_callback = kill_switch_callback
        self._clock = monotonic_clock

        self.current_status = MonitorStatus.HEALTHY
        self.trading_halted = False
        self._holdover_since: Optional[float] = None
        self._last_telemetry_at: Optional[float] = None
        self._staleness_disabled_warned = False

    def process_telemetry(self, offset_us: float, ptp_state: PtpState) -> MonitorStatus:
        """
        Evaluate one offset/state reading and return the resulting status.

        offset_us: clock divergence from the grandmaster in **microseconds**,
            signed; the sign is discarded. Must be finite - see
            :class:`ClockTelemetryError`.
        """
        if self.trading_halted:
            logger.warning("Telemetry ignored; trading is currently halted due to previous breach.")
            return MonitorStatus.CRITICAL

        if not isinstance(ptp_state, PtpState):
            raise ClockTelemetryError(f"ptp_state must be a PtpState, got {ptp_state!r}")
        abs_offset = self._validated_abs_offset(offset_us)

        now = self._clock()
        self._last_telemetry_at = now

        # 1. State evaluation. An unlocked servo makes the offset reading
        #    meaningless, so state is checked before the number it produced.
        if ptp_state == PtpState.UNLOCKED:
            logger.critical("PTP STATE UNLOCKED. Clock synchronization lost entirely.")
            return self._trigger_kill_switch("PTP_UNLOCKED")

        if ptp_state == PtpState.HOLDOVER:
            if self._holdover_since is None:
                self._holdover_since = now
                logger.warning(
                    "PTP entered HOLDOVER; grandmaster lost. Grace period %.3fs.",
                    self.holdover_grace_s)
            held_for = now - self._holdover_since
            if held_for >= self.holdover_grace_s:
                logger.critical(
                    "HOLDOVER exceeded grace: held %.3fs, allowed %.3fs.",
                    held_for, self.holdover_grace_s)
                return self._trigger_kill_switch(f"HOLDOVER_EXPIRED_{held_for:.3f}s")
        else:
            if self._holdover_since is not None:
                logger.info("PTP re-locked to grandmaster; holdover timer cleared.")
            self._holdover_since = None

        # 2. Threshold evaluation. The comparison is >= so a reading sitting
        #    exactly on the limit halts: RTS 25 states a *maximum* divergence,
        #    and a kill switch is not the place to argue the boundary.
        if abs_offset >= self.critical_threshold_us:
            logger.critical(
                "REGULATORY BREACH: drift %sus reached the limit of %sus.",
                abs_offset, self.critical_threshold_us)
            return self._trigger_kill_switch(f"DRIFT_BREACH_{abs_offset}us")

        if abs_offset >= self.warning_threshold_us:
            if self.current_status != MonitorStatus.WARNING:
                logger.warning(
                    "Drift %sus exceeds warning threshold of %sus.",
                    abs_offset, self.warning_threshold_us)
            self.current_status = MonitorStatus.WARNING
            return MonitorStatus.WARNING

        if self._holdover_since is not None:
            # Inside grace and inside the offset limits, but the grandmaster is
            # gone. Reporting HEALTHY here would hide a lost grandmaster from
            # operations for the entire grace window.
            self.current_status = MonitorStatus.WARNING
            return MonitorStatus.WARNING

        self.current_status = MonitorStatus.HEALTHY
        return MonitorStatus.HEALTHY

    def check_liveness(self) -> MonitorStatus:
        """
        Halt if telemetry has stopped arriving.

        Call this from the polling loop on every tick, including ticks where no
        telemetry was read. :meth:`process_telemetry` is purely reactive and
        cannot observe the absence of data; a crashed ``ptp4l`` produces no
        offsets at all, and silence must not read as health.
        """
        if self.trading_halted:
            return MonitorStatus.CRITICAL
        if self.max_telemetry_age_s is None:
            if not self._staleness_disabled_warned:
                logger.warning(
                    "check_liveness() called but max_telemetry_age_s is not configured; "
                    "telemetry staleness is NOT being detected.")
                self._staleness_disabled_warned = True
            return self.current_status
        if self._last_telemetry_at is None:
            # Never received a reading. Treated as a fault rather than waited
            # on, so a daemon that never starts is caught at startup.
            logger.critical("No PTP telemetry has ever been received.")
            return self._trigger_kill_switch("TELEMETRY_NEVER_RECEIVED")

        age = self._clock() - self._last_telemetry_at
        if age >= self.max_telemetry_age_s:
            logger.critical(
                "PTP telemetry stale: %.3fs since last reading, limit %.3fs.",
                age, self.max_telemetry_age_s)
            return self._trigger_kill_switch(f"TELEMETRY_STALE_{age:.3f}s")
        return self.current_status

    def reset(self, operator: str, reason: str) -> None:
        """
        Clear the latched halt after remediation.

        Deliberately requires an operator identity and a reason, and logs both
        at CRITICAL: resuming order origination after a clock breach is a
        compliance decision, and RTS 25 Article 4 requires the traceability
        system's design and operation to be documented and reviewed annually.
        An unattributed reset leaves nothing to review.
        """
        if not operator or not reason:
            raise ValueError(
                "reset() requires a non-empty operator and reason for the audit trail")
        logger.critical(
            "Clock drift halt RESET by %s. Reason: %s. Previous status: %s.",
            operator, reason, self.current_status.value)
        self.trading_halted = False
        self.current_status = MonitorStatus.HEALTHY
        self._holdover_since = None
        # Restart the staleness window rather than clearing it. Clearing would
        # make the first check_liveness() after a reset halt immediately on
        # TELEMETRY_NEVER_RECEIVED if the loop checks liveness before it reads.
        self._last_telemetry_at = self._clock()

    def _validated_abs_offset(self, offset_us: float) -> float:
        if isinstance(offset_us, bool) or not isinstance(offset_us, (int, float)):
            raise ClockTelemetryError(f"offset_us must be a real number, got {offset_us!r}")
        if not math.isfinite(offset_us):
            raise ClockTelemetryError(
                f"offset_us must be finite, got {offset_us!r}; a non-finite reading means the "
                "clock state is unknown and must not be classified as healthy")
        return abs(float(offset_us))

    def _trigger_kill_switch(self, reason: str) -> MonitorStatus:
        """
        Latch the halt, then invoke the callback.

        The latch is set *before* the callback runs, so a callback that raises
        still leaves the monitor halted. The failure is logged and re-raised so
        the supervising loop escalates, rather than continuing on the
        assumption that the engine was actually stopped.
        """
        self.current_status = MonitorStatus.CRITICAL
        self.trading_halted = True
        logger.critical("Executing kill switch callback. Reason: %s", reason)
        try:
            self.kill_switch_callback(reason)
        except Exception:
            logger.critical(
                "KILL SWITCH CALLBACK FAILED for reason %s; the trading engine may still be "
                "live. Escalate to a manual halt immediately.", reason, exc_info=True)
            raise
        return MonitorStatus.CRITICAL
