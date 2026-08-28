"""
network-interface-level-tick-timestamping: Linux ``SCM_TIMESTAMPING`` ancillary
control-message decoder for NIC hardware packet timestamps.

The defect this module exists to prevent is **mislabelling a kernel software
timestamp as a hardware wire timestamp**. Three facts from the Linux kernel
networking documentation drive the whole design; see ``references/standards.md``
for sources.

1. ``cmsg_type`` -- not the control-buffer length -- identifies which timestamp
   was delivered. On an LP64 host a 16-byte ``SOL_SOCKET`` control payload is
   ``SCM_TIMESTAMPNS`` (``struct timespec``, kernel *software* receive path) or
   ``SCM_TIMESTAMP`` (``struct __kernel_old_timeval``, *microseconds*). Neither
   is a hardware timestamp, and the two are indistinguishable by size.
2. Only ``SCM_TIMESTAMPING`` carries a hardware timestamp, and only in
   ``ts[2]``. ``ts[0]`` holds the software timestamp and ``ts[1]`` is
   deprecated. A decoder that reads ``ts[0]`` out of a ``scm_timestamping``
   struct and calls it "hardware" is reporting the kernel receive path.
3. The kernel does **not** convert hardware timestamps to system time. ``ts[2]``
   is read from the adapter's PTP hardware clock (PHC), an independent clock.
   Subtracting it from ``CLOCK_REALTIME`` measures the PHC-to-system offset plus
   the capture delay, and goes negative when the PHC is not disciplined by
   ``phc2sys``/``sfptpd``. That negative value is diagnostic and is therefore
   reported signed, never clamped to zero.

Timestamps are handled as integer nanoseconds throughout. IEEE 754 binary64 has
a 53-bit significand, so the representable spacing at a nanosecond epoch of
~1.7e18 is 256 ns -- coarser than the sub-microsecond effects this skill exists
to measure. Float epochs are rejected rather than silently truncated.

This module targets Linux. ``enable_nic_timestamping`` is a no-op returning
``False`` elsewhere; the decoder is pure and runs anywhere for testing.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import socket
import struct
import sys
import time
from typing import Iterable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

NS_PER_MICROSECOND = 1_000
NS_PER_SECOND = 1_000_000_000
US_PER_SECOND = 1_000_000

# ---------------------------------------------------------------------------
# Socket option / control-message constants.
#
# CPython's ``socket`` module does not export SO_TIMESTAMP*, SCM_TIMESTAMP* or
# the SOF_TIMESTAMPING_* flags on any platform, so ``hasattr(socket, ...)``
# guards on those names always fail and must never be used to gate activation.
# The values below are the asm-generic table (also used by x86-64, arm64, mips
# and alpha). sparc and parisc use different numbers -- pass explicit overrides
# to NICHardwareTimestamperEngine on those architectures.
# ---------------------------------------------------------------------------
SO_TIMESTAMP_OLD = 29
SO_TIMESTAMPNS_OLD = 35
SO_TIMESTAMPING_OLD = 37
SO_TIMESTAMP_NEW = 63
SO_TIMESTAMPNS_NEW = 64
SO_TIMESTAMPING_NEW = 65

# The kernel delivers each option's own numeric value as cmsg_type.
SCM_TIMESTAMP_TYPES = frozenset({SO_TIMESTAMP_OLD, SO_TIMESTAMP_NEW})
SCM_TIMESTAMPNS_TYPES = frozenset({SO_TIMESTAMPNS_OLD, SO_TIMESTAMPNS_NEW})
SCM_TIMESTAMPING_TYPES = frozenset({SO_TIMESTAMPING_OLD, SO_TIMESTAMPING_NEW})

# SO_TIMESTAMPING flag bits (linux/net_tstamp.h).
SOF_TIMESTAMPING_TX_HARDWARE = 1 << 0
SOF_TIMESTAMPING_TX_SOFTWARE = 1 << 1
SOF_TIMESTAMPING_RX_HARDWARE = 1 << 2
SOF_TIMESTAMPING_RX_SOFTWARE = 1 << 3
SOF_TIMESTAMPING_SOFTWARE = 1 << 4
SOF_TIMESTAMPING_RAW_HARDWARE = 1 << 6

# struct scm_timestamping is timespec[3]: 48 bytes on LP64 and for every
# *_NEW variant, 24 bytes for the *_OLD variant on a 32-bit host.
_SCM_TIMESTAMPING_SIZES = {48: 16, 24: 8}
_TIMESPEC_SIZES = frozenset({16, 8})
_SLOT_FORMAT = {16: "=qq", 8: "=ii"}


class TimestampSource(Enum):
    """Which capture point produced ``NICTimestampedPacket.timestamp_ns``."""

    #: scm_timestamping ts[2] -- the adapter (PHC) timestamp. NOT on the
    #: system-clock timebase unless phc2sys/sfptpd is disciplining it.
    HARDWARE_NIC = "HARDWARE_NIC"
    #: scm_timestamping ts[0] or SCM_TIMESTAMPNS -- kernel receive path,
    #: nanosecond resolution, CLOCK_REALTIME timebase.
    KERNEL_SOFTWARE = "KERNEL_SOFTWARE"
    #: SCM_TIMESTAMP -- kernel receive path, struct timeval, MICROSECOND
    #: resolution. Retained under its original name for compatibility.
    KERNEL_SO_TIMESTAMP = "KERNEL_SO_TIMESTAMP"
    #: No usable ancillary timestamp; the application clock was used.
    APPLICATION_FALLBACK = "APPLICATION_FALLBACK"


#: Capture points that read the system clock, and are therefore directly
#: comparable with an application ``CLOCK_REALTIME`` reading.
SYSTEM_TIMEBASE_SOURCES = frozenset({
    TimestampSource.KERNEL_SOFTWARE,
    TimestampSource.KERNEL_SO_TIMESTAMP,
    TimestampSource.APPLICATION_FALLBACK,
})


@dataclass(frozen=True)
class AncillaryTimestamps:
    """Every usable timestamp decoded from one packet's control messages."""

    hardware_ns: Optional[int] = None
    software_ns: Optional[int] = None
    software_coarse_ns: Optional[int] = None

    def select(self) -> Optional[Tuple[int, "TimestampSource"]]:
        """Highest-fidelity capture point available, or ``None``."""
        if self.hardware_ns is not None:
            return self.hardware_ns, TimestampSource.HARDWARE_NIC
        if self.software_ns is not None:
            return self.software_ns, TimestampSource.KERNEL_SOFTWARE
        if self.software_coarse_ns is not None:
            return self.software_coarse_ns, TimestampSource.KERNEL_SO_TIMESTAMP
        return None


@dataclass
class NICTimestampedPacket:
    """A received packet with its authoritative capture timestamp."""

    payload: bytes
    timestamp_ns: int
    source: TimestampSource
    #: ``capture_delay_ns`` expressed in microseconds. SIGNED: a negative value
    #: means the selected timestamp is ahead of the application clock, which
    #: for a HARDWARE_NIC source is the signature of an undisciplined PHC.
    kernel_queueing_jitter_us: float
    app_timestamp_ns: int = 0
    hardware_timestamp_ns: Optional[int] = None
    software_timestamp_ns: Optional[int] = None
    #: ``app_timestamp_ns - timestamp_ns``, signed. ``None`` when the selected
    #: timestamp *is* the application timestamp.
    capture_delay_ns: Optional[int] = None
    #: True when ``capture_delay_ns`` subtracts across two clocks (PHC vs
    #: CLOCK_REALTIME) and is therefore an offset plus a delay, not a delay.
    cross_timebase_comparison: bool = False
    #: True when hardware timestamps were required but not delivered.
    degraded: bool = False


def _require_nanos(value: int, field_name: str) -> int:
    """Reject float epochs: binary64 spacing at ~1.7e18 ns is 256 ns."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field_name} must be integer nanoseconds (e.g. time.time_ns()); "
            f"got {type(value).__name__}. A float cannot represent a nanosecond "
            f"epoch: binary64 spacing at ~1.7e18 ns is 256 ns."
        )
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative epoch, got {value}")
    return value


def _decode_slot(buf: bytes, offset: int, slot_size: int) -> Optional[Tuple[int, int]]:
    """Decode one ``timespec``/``timeval`` slot, or ``None`` if implausible."""
    fmt = _SLOT_FORMAT.get(slot_size)
    if fmt is None:
        return None
    try:
        seconds, fraction = struct.unpack_from(fmt, buf, offset)
    except struct.error:
        return None
    if seconds < 0 or fraction < 0:
        return None
    return seconds, fraction


def _timespec_to_ns(seconds: int, nanos: int) -> Optional[int]:
    if not 0 <= nanos < NS_PER_SECOND:
        return None
    if seconds == 0 and nanos == 0:
        return None  # the kernel leaves unfilled slots zeroed
    return (seconds * NS_PER_SECOND) + nanos


def _timeval_to_ns(seconds: int, micros: int) -> Optional[int]:
    if not 0 <= micros < US_PER_SECOND:
        return None
    if seconds == 0 and micros == 0:
        return None
    return (seconds * NS_PER_SECOND) + (micros * NS_PER_MICROSECOND)


def decode_scm_timestamping(cmsg_data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """
    Decode a ``struct scm_timestamping`` payload into ``(software_ns, hardware_ns)``.

    ``ts[0]`` is the software timestamp, ``ts[1]`` is deprecated and ignored,
    ``ts[2]`` is the hardware timestamp. Unfilled slots are all-zero and decode
    to ``None``.
    """
    slot_size = _SCM_TIMESTAMPING_SIZES.get(len(cmsg_data))
    if slot_size is None:
        logger.warning(
            "SCM_TIMESTAMPING control payload has unexpected length %d (expected "
            "48 on LP64 or 24 on a 32-bit host); recvmsg ancbufsize may be "
            "truncating the control buffer.",
            len(cmsg_data),
        )
        return None, None

    software_slot = _decode_slot(cmsg_data, 0, slot_size)
    hardware_slot = _decode_slot(cmsg_data, 2 * slot_size, slot_size)
    software_ns = None if software_slot is None else _timespec_to_ns(*software_slot)
    hardware_ns = None if hardware_slot is None else _timespec_to_ns(*hardware_slot)
    return software_ns, hardware_ns


class NICHardwareTimestamperEngine:
    """
    Decodes NIC hardware packet timestamps from ``recvmsg`` ancillary data and
    reports the capture delay against the application clock.

    ``use_hardware_timestamping=True`` (the default) requests
    ``SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE`` only, and
    marks any packet that arrives without a hardware timestamp as ``degraded``.
    Set it to ``False`` to additionally request the kernel software timestamps
    and accept them without raising a degradation flag.

    Control-message type numbers default to the asm-generic table. Pass
    ``so_timestamping`` and the ``scm_*_types`` overrides on sparc or parisc,
    whose ``SO_*`` numbering differs.
    """

    def __init__(
        self,
        use_hardware_timestamping: bool = True,
        *,
        so_timestamping: Optional[int] = None,
        scm_timestamping_types: Optional[Iterable[int]] = None,
        scm_timestampns_types: Optional[Iterable[int]] = None,
        scm_timestamp_types: Optional[Iterable[int]] = None,
    ) -> None:
        self.use_hardware_timestamping = use_hardware_timestamping
        self.so_timestamping = (
            so_timestamping
            if so_timestamping is not None
            else getattr(socket, "SO_TIMESTAMPING", SO_TIMESTAMPING_OLD)
        )
        self.scm_timestamping_types = frozenset(
            SCM_TIMESTAMPING_TYPES if scm_timestamping_types is None
            else scm_timestamping_types
        )
        self.scm_timestampns_types = frozenset(
            SCM_TIMESTAMPNS_TYPES if scm_timestampns_types is None
            else scm_timestampns_types
        )
        self.scm_timestamp_types = frozenset(
            SCM_TIMESTAMP_TYPES if scm_timestamp_types is None
            else scm_timestamp_types
        )

    # -- socket configuration ------------------------------------------------

    def timestamping_flags(self) -> int:
        """The ``SOF_TIMESTAMPING_*`` bitmap this engine requests."""
        flags = SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE
        if not self.use_hardware_timestamping:
            flags |= SOF_TIMESTAMPING_RX_SOFTWARE | SOF_TIMESTAMPING_SOFTWARE
        return flags

    def enable_nic_timestamping(self, sock: socket.socket) -> bool:
        """
        Enable ``SO_TIMESTAMPING`` receive timestamping on ``sock``.

        Returns True only if the kernel accepted the option. A False return is
        an operational alarm: every subsequent packet will fall back to the
        application clock. ``SO_TIMESTAMP``/``SO_TIMESTAMPNS`` are deliberately
        never set alongside ``SO_TIMESTAMPING`` -- the kernel fabricates a
        substitute software timestamp in ``ts[0]`` when both are enabled with
        ``SOF_TIMESTAMPING_SOFTWARE``, and that value is indistinguishable from
        a real capture.

        Kernel acceptance means the *option* was set, not that the adapter
        timestamps in hardware. Confirm the adapter's advertised capabilities
        with ``ethtool -T <iface>`` before trusting a HARDWARE_NIC label.
        """
        if not sys.platform.startswith("linux"):
            logger.error(
                "SO_TIMESTAMPING is a Linux socket option; platform is %r. "
                "Hardware tick timestamping is unavailable on this host.",
                sys.platform,
            )
            return False
        flags = self.timestamping_flags()
        try:
            sock.setsockopt(socket.SOL_SOCKET, self.so_timestamping, flags)
        except OSError as exc:
            logger.error(
                "setsockopt(SOL_SOCKET, SO_TIMESTAMPING=%d, flags=0x%x) failed: %s. "
                "Tick timestamps will degrade to the application clock.",
                self.so_timestamping,
                flags,
                exc,
            )
            return False
        logger.info(
            "SO_TIMESTAMPING enabled (option %d, flags 0x%x, hardware_required=%s). "
            "Verify adapter capability with `ethtool -T`.",
            self.so_timestamping,
            flags,
            self.use_hardware_timestamping,
        )
        return True

    # -- ancillary decoding --------------------------------------------------

    def decode_ancillary(
        self, ancillary_data: Sequence[Tuple[int, int, bytes]]
    ) -> AncillaryTimestamps:
        """
        Decode every ``SOL_SOCKET`` timestamp control message for one packet.

        All control messages are inspected before a capture point is chosen;
        ``recvmsg`` does not guarantee an ordering that puts the hardware
        timestamp first.
        """
        hardware_ns: Optional[int] = None
        software_ns: Optional[int] = None
        coarse_ns: Optional[int] = None

        for entry in ancillary_data:
            try:
                cmsg_level, cmsg_type, cmsg_data = entry
            except (TypeError, ValueError):
                logger.warning("Skipping malformed ancillary entry: %r", entry)
                continue
            if cmsg_level != socket.SOL_SOCKET:
                continue
            if not isinstance(cmsg_data, (bytes, bytearray)):
                logger.warning(
                    "Ancillary cmsg_type %r carried %s, not bytes; skipping.",
                    cmsg_type, type(cmsg_data).__name__,
                )
                continue
            cmsg_data = bytes(cmsg_data)

            if cmsg_type in self.scm_timestamping_types:
                decoded_sw, decoded_hw = decode_scm_timestamping(cmsg_data)
                if decoded_sw is not None:
                    software_ns = decoded_sw
                if decoded_hw is not None:
                    hardware_ns = decoded_hw
            elif cmsg_type in self.scm_timestampns_types:
                value = self._decode_single(cmsg_data, _timespec_to_ns, "SCM_TIMESTAMPNS")
                if value is not None:
                    software_ns = value
            elif cmsg_type in self.scm_timestamp_types:
                value = self._decode_single(cmsg_data, _timeval_to_ns, "SCM_TIMESTAMP")
                if value is not None:
                    coarse_ns = value

        return AncillaryTimestamps(
            hardware_ns=hardware_ns,
            software_ns=software_ns,
            software_coarse_ns=coarse_ns,
        )

    @staticmethod
    def _decode_single(cmsg_data: bytes, converter, label: str) -> Optional[int]:
        """Decode a single-slot ``timespec``/``timeval`` control payload."""
        if len(cmsg_data) not in _TIMESPEC_SIZES:
            logger.warning(
                "%s payload of %d bytes is not a single 16- or 8-byte slot; "
                "check the recvmsg ancbufsize.", label, len(cmsg_data),
            )
            return None
        slot = _decode_slot(cmsg_data, 0, len(cmsg_data))
        if slot is None:
            return None
        return converter(*slot)

    def unpack_ancillary_timestamp(
        self,
        ancillary_data: Sequence[Tuple[int, int, bytes]],
        app_receipt_ns: int,
    ) -> Tuple[int, TimestampSource]:
        """
        Select the authoritative capture timestamp for one packet.

        Precedence: NIC hardware (``ts[2]``) > kernel nanosecond software >
        kernel microsecond ``timeval`` > the supplied application timestamp.
        """
        app_receipt_ns = _require_nanos(app_receipt_ns, "app_receipt_ns")
        selected = self.decode_ancillary(ancillary_data).select()
        if selected is None:
            return app_receipt_ns, TimestampSource.APPLICATION_FALLBACK
        return selected

    # -- packet processing ---------------------------------------------------

    def process_packet_with_nic_timestamp(
        self,
        payload: bytes,
        ancillary_data: Sequence[Tuple[int, int, bytes]],
        app_receipt_ns: Optional[int] = None,
    ) -> NICTimestampedPacket:
        """
        Attach the authoritative capture timestamp to a received packet.

        ``app_receipt_ns`` must be integer nanoseconds on the system clock
        (``time.time_ns()``); floats are rejected because binary64 cannot
        represent a nanosecond epoch exactly. When omitted, ``time.time_ns()``
        is read here -- which is later than the real receive instant by however
        long the caller took to reach this call.
        """
        if app_receipt_ns is None:
            app_ns = time.time_ns()
        else:
            app_ns = _require_nanos(app_receipt_ns, "app_receipt_ns")

        decoded = self.decode_ancillary(ancillary_data)
        selected = decoded.select()

        if selected is None:
            if self.use_hardware_timestamping:
                logger.error(
                    "No usable ancillary timestamp on this packet; falling back to "
                    "the application clock. Tick timestamps are no longer "
                    "wire-accurate."
                )
            return NICTimestampedPacket(
                payload=payload,
                timestamp_ns=app_ns,
                source=TimestampSource.APPLICATION_FALLBACK,
                kernel_queueing_jitter_us=0.0,
                app_timestamp_ns=app_ns,
                hardware_timestamp_ns=None,
                software_timestamp_ns=None,
                capture_delay_ns=None,
                cross_timebase_comparison=False,
                degraded=self.use_hardware_timestamping,
            )

        ts_ns, source = selected
        capture_delay_ns = app_ns - ts_ns
        cross_timebase = source not in SYSTEM_TIMEBASE_SOURCES
        degraded = (
            self.use_hardware_timestamping
            and source is not TimestampSource.HARDWARE_NIC
        )

        if degraded:
            logger.error(
                "Hardware timestamping was required but the packet carried only a "
                "%s timestamp. Check `ethtool -T` and the driver hwtstamp config.",
                source.value,
            )
        if capture_delay_ns < 0:
            if cross_timebase:
                logger.error(
                    "NIC timestamp %d ns is %d ns AHEAD of the system clock. The "
                    "adapter PHC is an independent clock and is not disciplined to "
                    "CLOCK_REALTIME -- run phc2sys/sfptpd before reading any "
                    "cross-layer subtraction as a capture delay.",
                    ts_ns, -capture_delay_ns,
                )
            else:
                logger.error(
                    "Kernel timestamp %d ns postdates the application timestamp "
                    "%d ns by %d ns; the system clock stepped during capture.",
                    ts_ns, app_ns, -capture_delay_ns,
                )

        return NICTimestampedPacket(
            payload=payload,
            timestamp_ns=ts_ns,
            source=source,
            kernel_queueing_jitter_us=round(capture_delay_ns / NS_PER_MICROSECOND, 3),
            app_timestamp_ns=app_ns,
            hardware_timestamp_ns=decoded.hardware_ns,
            software_timestamp_ns=decoded.software_ns,
            capture_delay_ns=capture_delay_ns,
            cross_timebase_comparison=cross_timebase,
            degraded=degraded,
        )
