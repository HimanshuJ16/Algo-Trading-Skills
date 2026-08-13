"""
b3-brazil-exchange-api-integration:
Configuration validator and connection-state manager for the B3 PUMA Trading
System, covering the legacy FIX/FAST stack and the Binary (SBE/FIXP) stack.

Scope and honesty note
----------------------
This module performs **configuration validation and state management only**. It
opens no sockets, sends no FIX/FIXP messages, and performs no network I/O of any
kind. ``connect()`` drives the state machine and delegates to an injected
``connector`` callable; with the default connector it simulates success without
touching the network. Do not read a successful ``connect()`` as evidence of
reachability, credentials, or entitlement — wire a real connector (OnixS,
B2BITS, or your own socket layer) for that.

Why the SBE gap-recovery constraint exists
------------------------------------------
B3's legacy FIX/FAST UMDF publishes recovery services alongside the incremental
multicast stream — a TCP Replayer, a TCP Historical Replayer, and a Snapshot
Recovery stream. The newer Binary UMDF (SBE) feed is reported by connectivity
vendors to have **no TCP recovery channel for gap filling**; recovery there
relies on sequence tracking and snapshot/refresh mechanisms instead.

That asymmetry is the reason this module requires
``enable_application_gap_recovery=True`` for ``MODERN_BINARY_SBE``. See
``references/standards.md`` for sources and date-qualification — vendor-reported
feed capabilities change, so verify against B3's current specification before
relying on this constraint.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
import ipaddress
import logging
import re
import threading

logger = logging.getLogger(__name__)

__all__ = [
    "B3ProtocolSuite",
    "B3ConnectionState",
    "B3ConfigurationError",
    "B3ConnectionError",
    "B3ConnectionConfig",
    "B3IntegrationEngine",
    "COMP_ID_PATTERN",
]

# Conservative CompID whitelist. NOTE: this pattern is a defensive default
# chosen by this skill, NOT a constraint quoted from a B3 specification. It
# rejects whitespace, control characters, and delimiters that have no business
# in a session identifier. Confirm the real limits with B3 Membership Services
# and widen or narrow via B3ConnectionConfig(comp_id_pattern=...) as needed.
COMP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,12}$")


class B3ProtocolSuite(Enum):
    """B3 PUMA Trading System protocol suites.

    B3 has run the binary interfaces in parallel with the legacy ones since
    mid-2023. They are not equivalent long-term choices — see
    ``references/standards.md`` for the FIX order-entry gateway reduction
    schedule before selecting LEGACY_FIX_FAST for new work.
    """

    LEGACY_FIX_FAST = "LEGACY_FIX_FAST"      # FIX 4.4 order entry + UMDF FIX/FAST market data
    MODERN_BINARY_SBE = "MODERN_BINARY_SBE"  # Binary EntryPoint (FIXP/SBE) + Binary UMDF (SBE)


class B3ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class B3ConfigurationError(ValueError):
    """Raised when a configuration violates a validated B3 constraint.

    Subclasses ``ValueError`` so existing ``except ValueError`` callers keep
    working.
    """


class B3ConnectionError(RuntimeError):
    """Raised when the injected connector fails to establish a session."""


@dataclass(frozen=True)
class B3ConnectionConfig:
    """Configuration for a B3 PUMA Trading System session.

    Frozen: validation runs once in ``__post_init__``, so the config must not be
    mutable afterwards. A mutable config could be edited after the engine had
    already validated it, silently defeating every check below.

    Attributes:
        comp_id: B3-assigned SenderCompID.
        password: FIX/FIXP logon password. Excluded from ``repr`` so it does not
            leak into logs, tracebacks, or crash reporters.
        order_entry_ip: IPv4 address of the order entry gateway (unicast).
        market_data_multicast_ip: IPv4 multicast group for the UMDF feed.
        protocol_suite: LEGACY_FIX_FAST or MODERN_BINARY_SBE.
        enable_application_gap_recovery: Must be True for MODERN_BINARY_SBE.
        require_multicast_market_data: Enforce that the market data address is
            actually multicast. Set False only for isolated test rigs that
            replay over unicast.
        comp_id_pattern: Override for the CompID whitelist.
    """

    comp_id: str
    password: str = field(repr=False)
    order_entry_ip: str
    market_data_multicast_ip: str
    protocol_suite: B3ProtocolSuite
    enable_application_gap_recovery: bool
    require_multicast_market_data: bool = True
    comp_id_pattern: re.Pattern = field(default=COMP_ID_PATTERN, repr=False)

    def __post_init__(self) -> None:
        self._validate_comp_id()
        self._validate_protocol_suite()
        self._validate_ip_addresses()

    def _validate_comp_id(self) -> None:
        if not isinstance(self.comp_id, str):
            raise B3ConfigurationError(
                f"comp_id must be a string, got {type(self.comp_id).__name__}"
            )
        if not self.comp_id_pattern.fullmatch(self.comp_id):
            # Repr the value so control characters are visible in the message.
            raise B3ConfigurationError(
                f"comp_id {self.comp_id!r} does not match "
                f"{self.comp_id_pattern.pattern}. Session identifiers must not "
                "contain whitespace, control characters, or delimiters."
            )

    def _validate_protocol_suite(self) -> None:
        if not isinstance(self.protocol_suite, B3ProtocolSuite):
            raise B3ConfigurationError(
                f"protocol_suite must be a B3ProtocolSuite, "
                f"got {type(self.protocol_suite).__name__}"
            )

    @staticmethod
    def _parse_ipv4(value: str, label: str) -> ipaddress.IPv4Address:
        # IPv4Address accepts ints and packed bytes; 12345 would silently become
        # 0.0.48.57. Require a dotted-quad string so a mistyped config fails loudly.
        if not isinstance(value, str):
            raise B3ConfigurationError(
                f"{label} must be a dotted-quad string, got {type(value).__name__}"
            )
        try:
            return ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError as exc:
            raise B3ConfigurationError(f"{label} is not a valid IPv4 address: {exc}") from exc

    def _validate_ip_addresses(self) -> None:
        order_entry = self._parse_ipv4(self.order_entry_ip, "order_entry_ip")
        if order_entry.is_multicast:
            raise B3ConfigurationError(
                f"order_entry_ip {self.order_entry_ip} is a multicast address; "
                "order entry is a point-to-point TCP session."
            )

        market_data = self._parse_ipv4(
            self.market_data_multicast_ip, "market_data_multicast_ip"
        )
        if self.require_multicast_market_data and not market_data.is_multicast:
            raise B3ConfigurationError(
                f"market_data_multicast_ip {self.market_data_multicast_ip} is not a "
                "multicast address. UMDF is distributed over UDP multicast; a unicast "
                "address here means no market data will arrive in production. Pass "
                "require_multicast_market_data=False only for an isolated test rig."
            )


class B3IntegrationEngine:
    """Validates B3 topology constraints and manages connection state.

    Performs no network I/O itself. Supply ``connector`` to integrate a real
    session layer; it is called by :meth:`connect` and must raise on failure.

    Thread safety: state transitions are serialised under a re-entrant lock, so
    ``connect``/``disconnect``/``state`` are safe to call from multiple threads.
    The injected ``connector`` is invoked while the lock is held, so it must not
    call back into this engine.
    """

    def __init__(
        self,
        config: B3ConnectionConfig,
        connector: Optional[Callable[[B3ConnectionConfig], None]] = None,
        disconnector: Optional[Callable[[B3ConnectionConfig], None]] = None,
    ) -> None:
        if not isinstance(config, B3ConnectionConfig):
            raise B3ConfigurationError(
                f"config must be a B3ConnectionConfig, got {type(config).__name__}"
            )
        self.config = config
        self._connector = connector
        self._disconnector = disconnector
        self._lock = threading.RLock()
        self._state = B3ConnectionState.DISCONNECTED
        self._validate_b3_architecture()

    @property
    def state(self) -> B3ConnectionState:
        with self._lock:
            return self._state

    def _validate_b3_architecture(self) -> None:
        """Enforces the SBE gap-recovery constraint.

        The Binary UMDF (SBE) feed is reported to have no TCP recovery channel
        for gap filling, unlike the legacy FIX/FAST UMDF which publishes a TCP
        Replayer and TCP Historical Replayer. An SBE consumer must therefore
        track sequence numbers itself and recover via snapshot/refresh.
        """
        if self.config.protocol_suite is B3ProtocolSuite.MODERN_BINARY_SBE:
            if not self.config.enable_application_gap_recovery:
                logger.error(
                    "Invalid configuration: MODERN_BINARY_SBE requires "
                    "application-level gap recovery."
                )
                raise B3ConfigurationError(
                    "B3 Binary UMDF (SBE) has no TCP recovery channel for gap filling; "
                    "enable_application_gap_recovery must be True and the application "
                    "must implement sequence tracking with snapshot-based recovery."
                )

    def connect(self) -> bool:
        """Drives DISCONNECTED/FAILED -> CONNECTING -> CONNECTED.

        Returns True if the session is CONNECTED on return. With no ``connector``
        injected this performs **no network I/O** and simply reports success —
        it is not evidence of reachability or valid credentials.

        Raises:
            B3ConnectionError: If the injected connector fails.
        """
        with self._lock:
            if self._state is B3ConnectionState.CONNECTED:
                logger.info("B3 session already connected.")
                return True

            self._state = B3ConnectionState.CONNECTING
            logger.info(
                "Initiating B3 %s connection for %s...",
                self.config.protocol_suite.value,
                self.config.comp_id,
            )

            if self._connector is None:
                self._state = B3ConnectionState.CONNECTED
                logger.warning(
                    "B3 %s session marked CONNECTED WITHOUT network I/O: no connector "
                    "injected. This is configuration validation only, not a live session.",
                    self.config.protocol_suite.value,
                )
                return True

            try:
                self._connector(self.config)
            except Exception as exc:
                self._state = B3ConnectionState.FAILED
                logger.error(
                    "B3 %s connection failed for %s: %s",
                    self.config.protocol_suite.value,
                    self.config.comp_id,
                    exc,
                )
                raise B3ConnectionError(
                    f"failed to establish B3 {self.config.protocol_suite.value} session"
                ) from exc

            self._state = B3ConnectionState.CONNECTED
            logger.info(
                "B3 %s session connected.", self.config.protocol_suite.value
            )
            return True

    def disconnect(self) -> bool:
        """Drives any state -> DISCONNECTED. Idempotent.

        A failing ``disconnector`` is logged and the state still becomes
        DISCONNECTED: a shutdown path that raises would strand the session.
        """
        with self._lock:
            if self._state is B3ConnectionState.DISCONNECTED:
                return True

            logger.info(
                "Disconnecting B3 %s session...", self.config.protocol_suite.value
            )
            if self._disconnector is not None:
                try:
                    self._disconnector(self.config)
                except Exception:
                    logger.exception(
                        "B3 disconnector raised for %s; forcing DISCONNECTED.",
                        self.config.comp_id,
                    )
            self._state = B3ConnectionState.DISCONNECTED
            return True
