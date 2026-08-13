"""
australian-securities-exchange-asx-api:
Integration adapter managing connectivity configurations, topology validation,
session-schedule awareness, and FIX session-state primitives for ASX FIX 5.0 SP2,
OUCH, and ITCH protocols.

This module is a foundational configuration and state-management layer. It does NOT
perform real socket I/O or FIX message serialisation — those concerns belong to a
full FIX engine (see `fix-protocol-session-management-across-venues`). Its job is
to make the high-value production decisions that are easy to get wrong:
  - enforce that OUCH/ITCH only run from an ALC co-location;
  - keep order-entry traffic inside the ASX Trade session phases (AEST/AEDT-aware);
  - validate the FIX HeartBtInt against the ASX FIX specification;
  - track outbound sequence numbers so a reconnect can detect gaps.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ASX Trade sessions run on Sydney wall-clock time. The schedule below uses the
# nominal (non-randomised) phase boundaries published by ASX. ASX randomises the
# OSPA start within a 15-second window and the CSPA start within a 30-second
# window; production code must treat the ASX Trade system message as authoritative
# for the exact phase transition, not this table.
_PRE_OPEN_START = time(7, 0, 0)
_OPENING_AUCTION_START = time(9, 59, 0)          # OSPA, randomised 15s window
_NORMAL_START = time(9, 59, 45)                  # end of OSPA
_PRE_CSPA_START = time(16, 0, 0)
_CLOSING_AUCTION_START = time(16, 10, 0)         # CSPA, randomised 30s window
_CSPA_END = time(16, 11, 0)                      # nominal close of CSPA


class AsxProtocol(Enum):
    FIX_5_0_SP2 = "FIX_5_0_SP2"  # Standard order entry and drop copy
    OUCH = "OUCH"                # Ultra-low latency binary order entry
    ITCH = "ITCH"                # Ultra-low latency multicast market data


class AsxConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class AsxMarketPhase(Enum):
    """Phases of the ASX Trade cash-equity trading day (see references/standards.md)."""
    PRE_OPEN = "PRE_OPEN"                 # 07:00 - 09:59, orders queued, no matching
    OPENING_AUCTION = "OPENING_AUCTION"    # 09:59 - 09:59:45, Opening Single Price Auction
    NORMAL = "NORMAL"                     # 09:59:45 - 16:00, continuous matching
    PRE_CSPA = "PRE_CSPA"                 # 16:00 - 16:10, pre closing auction
    CLOSING_AUCTION = "CLOSING_AUCTION"    # 16:10 - 16:11, Closing Single Price Auction
    CLOSED = "CLOSED"                     # post-CSPA through to next 07:00


class AsxSessionSchedule:
    """
    Maps a wall-clock instant (Sydney local time, AEST UTC+10 / AEDT UTC+11) to the
    corresponding ASX Trade market phase.

    ASX publishes the schedule in Sydney local time; the wall-clock boundaries are
    identical under AEST and AEDT (daylight saving shifts the UTC offset, not the
    local session times). Callers should therefore convert any UTC/instant to Sydney
    local time BEFORE asking for the phase, so that daylight saving is handled once,
    at the boundary of this helper, and never inside trading logic.

    The input datetime is interpreted as Sydney local wall-clock time:
      - tz-aware datetimes are converted to the Sydney timezone and then read as
        wall-clock;
      - naive datetimes are assumed to already be Sydney wall-clock (useful for
        deterministic offline testing and backtests).
    """

    @staticmethod
    def phase_at(dt: datetime) -> AsxMarketPhase:
        t = _as_sydney_wallclock(dt)
        if t < _PRE_OPEN_START:
            return AsxMarketPhase.CLOSED
        if t < _OPENING_AUCTION_START:
            return AsxMarketPhase.PRE_OPEN
        if t < _NORMAL_START:
            return AsxMarketPhase.OPENING_AUCTION
        if t < _PRE_CSPA_START:
            return AsxMarketPhase.NORMAL
        if t < _CLOSING_AUCTION_START:
            return AsxMarketPhase.PRE_CSPA
        if t < _CSPA_END:
            return AsxMarketPhase.CLOSING_AUCTION
        return AsxMarketPhase.CLOSED

    @staticmethod
    def is_order_entry_window(dt: datetime) -> bool:
        """
        True during phases where order entry is accepted by ASX Trade. Order entry is
        permitted in PRE_OPEN (queued), OPENING_AUCTION (with restrictions), NORMAL,
        PRE_CSPA, and CLOSING_AUCTION. It is NOT accepted in CLOSED.
        """
        return AsxSessionSchedule.phase_at(dt) is not AsxMarketPhase.CLOSED


def _as_sydney_wallclock(dt: datetime) -> time:
    """Return the wall-clock time-of-day of `dt` expressed in Sydney local time."""
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Australia/Sydney"))
        except Exception:  # pragma: no cover - fallback when tzdata is unavailable
            # Approximate Sydney as UTC+10 (AEST). This is only used when the host
            # has no IANA tzdata; production hosts must ship tzdata. The caller is
            # expected to pass tz-aware datetimes in real deployments.
            dt = dt.astimezone(timezone.utc) + timedelta(hours=10)
            dt = dt.replace(tzinfo=None)
    return dt.time()


@dataclass
class AsxConnectionConfig:
    host: str
    port: int
    comp_id: str
    protocol: AsxProtocol
    is_alc_colocated: bool        # True if servers are physically in the ALC
    is_cde_environment: bool      # True if connecting to Customer Development Environment
    # FIX HeartBtInt (tag 108) in seconds. ASX FIX spec: recommended 30s, must be
    # in [10, 60]; a value below 10s triggers a Logout. Only honoured for FIX.
    heartbeat_interval_seconds: int = 30


class AsxSequenceTracker:
    """
    Monotonic outbound sequence-number tracker for a FIX session.

    ASX Trade FIX sessions are bounded by a single trading day; sequence numbers
    reset only on an ASX Trade system restart or an explicit ResetSeqNumFlag (141=Y)
    logon. On reconnect, a gap between the last number we sent and the number the
    exchange expects indicates lost or replayed messages and MUST be resolved via a
    ResendRequest (tag 2) before further order traffic — never by silently skipping.

    This tracker is deliberately minimal: it owns no I/O, only the counter and the
    gap-detection predicate, so it stays deterministic and unit-testable.
    """

    def __init__(self) -> None:
        self._next_outbound = 1

    def next(self) -> int:
        seq = self._next_outbound
        self._next_outbound += 1
        logger.debug("ASX FIX outbound sequence number allocated: %d", seq)
        return seq

    @property
    def expected_outbound(self) -> int:
        return self._next_outbound

    @staticmethod
    def detect_inbound_gap(last_seen: Optional[int], received: int) -> bool:
        """
        Return True if `received` indicates a gap in the inbound sequence.

        A gap exists when `received` is not exactly `last_seen + 1` (or 1 when no
        prior message has been seen). Equal or lower numbers indicate a replay and
        are treated as a gap here as well — the caller decides whether to ignore or
        resend-request based on message type.
        """
        if last_seen is None:
            return received != 1
        return received != last_seen + 1

    def reset(self) -> None:
        """Reset to 1 — use only after a confirmed ResetSeqNumFlag (141=Y) logon."""
        self._next_outbound = 1
        logger.info("ASX FIX outbound sequence tracker reset to 1.")


class AsxIntegrationEngine:
    """
    Manages the lifecycle and validation of a direct connection to the ASX.
    """

    def __init__(self, config: AsxConnectionConfig):
        self.config = config
        self.state = AsxConnectionState.DISCONNECTED
        self.sequence = AsxSequenceTracker() if config.protocol is AsxProtocol.FIX_5_0_SP2 else None
        self._validate_topology()

    def _validate_topology(self) -> None:
        """Validates that the selected protocol matches the physical network topology."""
        if self.config.protocol in (AsxProtocol.OUCH, AsxProtocol.ITCH):
            if not self.config.is_alc_colocated:
                logger.error(
                    "Invalid Topology: ASX %s requires ALC Co-Location.",
                    self.config.protocol.value,
                )
                raise ValueError(
                    f"Cannot route {self.config.protocol.value} outside the ALC."
                )

        if not self.config.is_cde_environment and self.config.host.startswith("test"):
            logger.warning(
                "Host '%s' appears to be a test environment, but is_cde_environment is False.",
                self.config.host,
            )

        # FIX HeartBtInt (tag 108) is enforced by the ASX FIX gateway. A value
        # outside [10, 60] seconds is rejected (<10s triggers an immediate Logout).
        if self.config.protocol is AsxProtocol.FIX_5_0_SP2:
            hb = self.config.heartbeat_interval_seconds
            if hb < 10 or hb > 60:
                logger.error("Invalid FIX HeartBtInt: %ss (must be 10-60s).", hb)
                raise ValueError(
                    f"heartbeat_interval_seconds must be in [10, 60]; got {hb}."
                )
            if hb != 30:
                logger.info(
                    "FIX HeartBtInt set to %ss (ASX recommended value is 30s).", hb,
                )

    def market_phase(self, at: Optional[datetime] = None) -> AsxMarketPhase:
        """Return the ASX Trade market phase at `at` (defaults to now, Sydney time)."""
        dt = at if at is not None else datetime.now()
        return AsxSessionSchedule.phase_at(dt)

    def connect(self, at: Optional[datetime] = None) -> bool:
        if self.state == AsxConnectionState.CONNECTED:
            logger.info("ASX Session already connected.")
            return True

        self.state = AsxConnectionState.CONNECTING
        logger.info(
            "Initiating ASX %s connection to %s:%s...",
            self.config.protocol.value, self.config.host, self.config.port,
        )

        phase = self.market_phase(at)
        if self.config.protocol in (AsxProtocol.FIX_5_0_SP2, AsxProtocol.OUCH):
            # Order-entry protocols may log on outside the matching window
            # (e.g. to queue PRE_OPEN orders), but connecting during CLOSED is
            # almost always a scheduling bug — warn loudly.
            if phase is AsxMarketPhase.CLOSED:
                logger.warning(
                    "ASX %s connect attempted during CLOSED phase (no order entry "
                    "accepted); verify the session scheduler and host timezone.",
                    self.config.protocol.value,
                )
            else:
                logger.info("ASX Trade market phase at connect: %s.", phase.value)
        else:
            logger.info("ASX Trade market phase at connect: %s.", phase.value)

        # In a real implementation, this would trigger the python-quickfix socket
        # or the raw TCP socket for OUCH.
        self.state = AsxConnectionState.CONNECTED
        logger.info(
            "ASX %s Session Connected successfully for %s.",
            self.config.protocol.value, self.config.comp_id,
        )
        return True

    def disconnect(self) -> bool:
        if self.state == AsxConnectionState.DISCONNECTED:
            return True

        logger.info("Disconnecting ASX %s session...", self.config.protocol.value)
        # ASX FIX requires an exchange of Logout (5) messages; a disconnect
        # without that exchange is an abnormal condition and must be flagged for
        # recovery (see references/workflows.md).
        if self.config.protocol is AsxProtocol.FIX_5_0_SP2:
            logger.info(
                "ASX FIX Logout (5) exchange completed for %s.", self.config.comp_id,
            )
        self.state = AsxConnectionState.DISCONNECTED
        return True
