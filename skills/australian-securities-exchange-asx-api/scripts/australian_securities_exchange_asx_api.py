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
  - classify inbound sequence-number anomalies so a reconnect takes the FIX-correct
    recovery action (ResendRequest vs. Logout-and-terminate).

Schedule currency: the phase table below reflects the ASX cash-market schedule as
published at 2026-09-03, i.e. AFTER ASX Service Release 15 (effective 23 June 2025)
removed the staggered alphabetical opening rotation and introduced the Post Close
phase. Documents predating that date show a staggered open between 10:00 and 10:09
and no Post Close phase; do not copy the schedule from them.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
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
_NORMAL_START = time(9, 59, 45)                  # end of OSPA levelling period
_PRE_CSPA_START = time(16, 0, 0)
_CLOSING_AUCTION_START = time(16, 10, 0)         # CSPA, randomised 30s window
_POST_CLOSE_START = time(16, 11, 0)              # nominal close of CSPA
_ADJUST_START = time(16, 21, 30)                 # end of Post Close trading
_ADJUST_END = time(18, 50, 0)                    # Purge Orders begins; day is over

# Resolved once at import. ZoneInfoNotFoundError subclasses KeyError; ImportError
# covers hosts without the `zoneinfo` module at all.
try:  # pragma: no cover - depends on host tzdata availability
    from zoneinfo import ZoneInfo

    _SYDNEY_TZ: Optional[tzinfo] = ZoneInfo("Australia/Sydney")
except (ImportError, KeyError):  # pragma: no cover - fallback path
    _SYDNEY_TZ = None

_tz_fallback_warned = False


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
    PRE_OPEN = "PRE_OPEN"                  # 07:00 - 09:59, orders queued, no matching
    OPENING_AUCTION = "OPENING_AUCTION"    # 09:59 - 09:59:45, Opening Single Price Auction
    NORMAL = "NORMAL"                      # 09:59:45 - 16:00, continuous matching
    PRE_CSPA = "PRE_CSPA"                  # 16:00 - 16:10, pre closing auction
    CLOSING_AUCTION = "CLOSING_AUCTION"    # 16:10 - 16:11, Closing Single Price Auction
    POST_CLOSE = "POST_CLOSE"              # 16:11 - 16:21:30, trading AT the CSPA price
    ADJUST = "ADJUST"                      # 16:21:30 - 18:50, amend/cancel only, no matching
    CLOSED = "CLOSED"                      # 18:50 through to next 07:00


# Phases in which ASX Trade accepts NEW orders. Note POST_CLOSE accepts new orders
# only at the CSPA price — anything else is rejected by the exchange.
_NEW_ORDER_PHASES = frozenset({
    AsxMarketPhase.PRE_OPEN,
    AsxMarketPhase.OPENING_AUCTION,
    AsxMarketPhase.NORMAL,
    AsxMarketPhase.PRE_CSPA,
    AsxMarketPhase.CLOSING_AUCTION,
    AsxMarketPhase.POST_CLOSE,
})

# Phases in which resting orders may still be amended or cancelled. ADJUST accepts
# no new orders but does permit tidying up the book, which is why it is modelled
# separately from CLOSED: a risk unwind must know it can still pull orders.
_AMEND_CANCEL_PHASES = frozenset(_NEW_ORDER_PHASES | {AsxMarketPhase.ADJUST})


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

    Passing a naive datetime taken from a non-Sydney host clock (for example
    `datetime.now()` on a UTC server) is therefore a 10-11 hour error. Always pass
    a tz-aware datetime in production.

    This table covers the ASX cash market (equities) on a normal trading day. It
    does not model trading halts, non-trading days, ASX 24 derivatives hours, or
    the three ETFs that open an hour late during AEDT — see references/standards.md.
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
        if t < _POST_CLOSE_START:
            return AsxMarketPhase.CLOSING_AUCTION
        if t < _ADJUST_START:
            return AsxMarketPhase.POST_CLOSE
        if t < _ADJUST_END:
            return AsxMarketPhase.ADJUST
        return AsxMarketPhase.CLOSED

    @staticmethod
    def is_order_entry_window(dt: datetime) -> bool:
        """
        True during phases where ASX Trade accepts NEW orders: PRE_OPEN (queued),
        OPENING_AUCTION (restricted), NORMAL, PRE_CSPA, CLOSING_AUCTION (restricted),
        and POST_CLOSE (at the CSPA price only).

        False in ADJUST and CLOSED. ADJUST still permits amend/cancel — use
        `is_amend_cancel_window` before concluding that resting orders are untouchable.
        """
        return AsxSessionSchedule.phase_at(dt) in _NEW_ORDER_PHASES

    @staticmethod
    def is_amend_cancel_window(dt: datetime) -> bool:
        """
        True during phases where resting orders may still be amended or cancelled.

        This is a superset of `is_order_entry_window`: it additionally covers ADJUST
        (16:21:30-18:50 Sydney), where ASX accepts no new orders and executes no
        trades but does allow participants to tidy up the book. A kill-switch or
        end-of-day unwind that gates on `is_order_entry_window` alone will wrongly
        conclude it cannot pull its own resting orders during ADJUST.
        """
        return AsxSessionSchedule.phase_at(dt) in _AMEND_CANCEL_PHASES


def _as_sydney_wallclock(dt: datetime) -> time:
    """Return the wall-clock time-of-day of `dt` expressed in Sydney local time."""
    global _tz_fallback_warned
    if dt.tzinfo is not None:
        if _SYDNEY_TZ is not None:
            dt = dt.astimezone(_SYDNEY_TZ)
        else:  # pragma: no cover - only reachable on hosts without IANA tzdata
            # Approximate Sydney as UTC+10 (AEST). This is WRONG BY ONE HOUR during
            # AEDT (roughly October-April) and can therefore misclassify the market
            # phase near a session boundary. Production hosts must ship tzdata.
            if not _tz_fallback_warned:
                logger.warning(
                    "IANA tzdata for 'Australia/Sydney' is unavailable; falling back "
                    "to a fixed UTC+10 (AEST) offset. Market-phase decisions will be "
                    "wrong by one hour during AEDT. Install tzdata on this host.",
                )
                _tz_fallback_warned = True
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
    # FIX HeartBtInt (tag 108) in seconds. ASX FIX spec: recommended 30s, maximum
    # supported 60s; a value below 10s triggers a Logout (SessionStatus 1409=101).
    # Only honoured for FIX.
    heartbeat_interval_seconds: int = 30


class InboundSeqNumStatus(Enum):
    """
    Classification of an inbound FIX `MsgSeqNum (34)` against the expected number.

    The recovery action differs per case, which is why a single boolean is not
    enough (see `AsxSequenceTracker.classify_inbound`):

    - IN_SEQUENCE: process the message normally.
    - GAP: received > expected. Issue a ResendRequest (2) for the missing range
      before resuming order traffic.
    - POSS_DUP: received <= expected with PossDupFlag (43) = Y. This is a legitimate
      retransmission in response to our own ResendRequest — discard it if already
      processed. It is NOT a session error.
    - TOO_LOW: received < expected with PossDupFlag not set. Per the FIX session
      layer this is unrecoverable: send Logout (5) with SessionStatus (1409) = 9
      and terminate the connection. Do NOT issue a ResendRequest.
    """
    IN_SEQUENCE = "IN_SEQUENCE"
    GAP = "GAP"
    POSS_DUP = "POSS_DUP"
    TOO_LOW = "TOO_LOW"


class AsxSequenceTracker:
    """
    Monotonic outbound sequence-number tracker for a FIX session.

    ASX Trade FIX sessions are bounded by a single trading day and are NOT ended by
    connectivity loss or a Logout; sequence numbers reset on an ASX Trade system
    restart or an explicit ResetSeqNumFlag (141=Y) logon. On reconnect, a mismatch
    between the number the exchange sends and the number we expect must be
    classified before acting — a forward gap calls for a ResendRequest (2), while a
    too-low number is a fatal session error, and treating them alike corrupts the
    session.

    This tracker is deliberately minimal: it owns no I/O, only the counter and the
    classification predicates, so it stays deterministic and unit-testable.
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
    def classify_inbound(
        last_seen: Optional[int],
        received: int,
        poss_dup: bool = False,
    ) -> InboundSeqNumStatus:
        """
        Classify an inbound `MsgSeqNum (34)` against the expected next number.

        `last_seen` is the last in-sequence inbound number processed, or None if no
        message has been seen on this session yet (the expected number is then 1).
        `poss_dup` is the value of PossDupFlag (43) on the received message.

        Raises ValueError for non-positive sequence numbers, which are invalid in
        FIX and almost always indicate a parsing bug rather than a session gap.
        """
        if received < 1:
            raise ValueError(f"MsgSeqNum must be >= 1; got {received}.")
        if last_seen is not None and last_seen < 1:
            raise ValueError(f"last_seen must be >= 1 or None; got {last_seen}.")

        expected = 1 if last_seen is None else last_seen + 1
        if received == expected:
            return InboundSeqNumStatus.IN_SEQUENCE
        if received > expected:
            return InboundSeqNumStatus.GAP
        # received < expected
        return InboundSeqNumStatus.POSS_DUP if poss_dup else InboundSeqNumStatus.TOO_LOW

    @staticmethod
    def detect_inbound_gap(last_seen: Optional[int], received: int) -> bool:
        """
        Return True if `received` is not exactly the expected next inbound number.

        Retained for callers that only need a boolean tripwire. It cannot express
        which recovery action applies and reports a legitimate PossDupFlag (43) = Y
        retransmission as a gap; prefer `classify_inbound` for recovery decisions.
        """
        return (
            AsxSequenceTracker.classify_inbound(last_seen, received)
            is not InboundSeqNumStatus.IN_SEQUENCE
        )

    def reset(self) -> None:
        """
        Reset to 1 — use only after a confirmed ResetSeqNumFlag (141=Y) logon.

        Per the ASX Trade FIX Order Entry Specification, a 141=Y session cannot
        retrieve GTC/GTD orders via a ResendRequest. Resetting therefore leaves live
        resting orders in the market that this session can no longer enumerate;
        reconcile against drop copy before resuming order flow.
        """
        self._next_outbound = 1
        logger.info(
            "ASX FIX outbound sequence tracker reset to 1 (ResetSeqNumFlag 141=Y); "
            "GTC/GTD orders are NOT recoverable via ResendRequest on this session.",
        )


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
        """Validates the connection configuration and physical network topology."""
        if not self.config.host or not self.config.host.strip():
            raise ValueError("host must be a non-empty string.")
        if not self.config.comp_id or not self.config.comp_id.strip():
            raise ValueError("comp_id must be a non-empty string (assigned by ASX).")
        if not 1 <= self.config.port <= 65535:
            raise ValueError(f"port must be in [1, 65535]; got {self.config.port}.")

        # OUCH is provisioned on an ALC cross connect and ITCH is delivered by
        # multicast from the ASX Trade platform; neither survives a remote,
        # non-colocated path. This engine treats co-location as mandatory for both
        # as a deployment policy — see references/standards.md.
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

        # FIX HeartBtInt (tag 108) is enforced by the ASX FIX gateway: recommended
        # 30s, maximum supported 60s, and below 10s triggers a Logout. The spec also
        # words the lower bound as "greater than 10 seconds", so exactly 10s is
        # ambiguous — 30s is the only value ASX actually recommends.
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
        """
        Return the ASX Trade market phase at `at`.

        `at` defaults to the current instant as a tz-aware UTC datetime, which the
        schedule converts to Sydney local time. It deliberately does NOT default to
        `datetime.now()`: that returns the host's naive local wall-clock, which the
        schedule would read as Sydney time and misclassify by 10-11 hours on the
        UTC-configured hosts most deployments run on.
        """
        dt = at if at is not None else datetime.now(timezone.utc)
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
            # Order-entry protocols may log on outside the matching window (e.g. to
            # queue PRE_OPEN orders), but logging on when ASX accepts no new orders
            # is almost always a scheduling bug — warn loudly.
            if phase not in _NEW_ORDER_PHASES:
                logger.warning(
                    "ASX %s connect attempted during %s phase (no new order entry "
                    "accepted); verify the session scheduler and host timezone.",
                    self.config.protocol.value, phase.value,
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
