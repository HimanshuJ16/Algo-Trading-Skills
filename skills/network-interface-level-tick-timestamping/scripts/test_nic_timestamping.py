"""
Unit tests for network-interface-level-tick-timestamping.

Control-message fixtures are built byte-for-byte from the kernel UAPI layouts
(``struct scm_timestamping`` = ``timespec[3]``, ``struct timespec``,
``struct __kernel_old_timeval``) rather than by calling the module's own
decoder, and every expected nanosecond value is derived by hand from the
constants fed into the fixture.
"""
import logging
import socket
import struct
import unittest

from nic_timestamping import (
    NS_PER_SECOND,
    SO_TIMESTAMP_OLD,
    SO_TIMESTAMPING_NEW,
    SO_TIMESTAMPING_OLD,
    SO_TIMESTAMPNS_OLD,
    SOF_TIMESTAMPING_RAW_HARDWARE,
    SOF_TIMESTAMPING_RX_HARDWARE,
    SOF_TIMESTAMPING_RX_SOFTWARE,
    SOF_TIMESTAMPING_SOFTWARE,
    NICHardwareTimestamperEngine,
    TimestampSource,
    decode_scm_timestamping,
)

# The engine logs an ERROR line for degradation and clock faults, which is the
# intended production behaviour but only noise in a test run.
logging.disable(logging.CRITICAL)

# Arbitrary but fixed capture second. All expectations below are this second
# plus a hand-written sub-second offset.
BASE_SEC = 1_784_948_000
BASE_NS = BASE_SEC * NS_PER_SECOND


def scm_timestamping(software=(0, 0), legacy=(0, 0), hardware=(0, 0), slot=16):
    """Build a ``struct scm_timestamping`` payload: three timespec slots."""
    fmt = "=qq" if slot == 16 else "=ii"
    return b"".join(struct.pack(fmt, s, n) for s, n in (software, legacy, hardware))


def timespec(sec, nsec, slot=16):
    return struct.pack("=qq" if slot == 16 else "=ii", sec, nsec)


def timeval(sec, usec, slot=16):
    return struct.pack("=qq" if slot == 16 else "=ii", sec, usec)


class TestScmTimestampingDecoding(unittest.TestCase):
    """ts[2] is the hardware slot; ts[0] is software; ts[1] is deprecated."""

    def setUp(self):
        self.engine = NICHardwareTimestamperEngine()

    def test_hardware_slot_is_read_from_ts2_not_ts0(self):
        # Regression: the previous decoder read offset 0 of scm_timestamping
        # and labelled it HARDWARE_NIC, i.e. it reported the kernel software
        # timestamp as a wire timestamp.
        cmsg = scm_timestamping(software=(BASE_SEC, 900_000), hardware=(BASE_SEC, 400_000))
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, cmsg)],
            app_receipt_ns=BASE_NS + 1_000_000,
        )
        self.assertEqual(pkt.source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(pkt.timestamp_ns, BASE_NS + 400_000)
        self.assertEqual(pkt.hardware_timestamp_ns, BASE_NS + 400_000)
        self.assertEqual(pkt.software_timestamp_ns, BASE_NS + 900_000)
        # 1,000,000 ns app - 400,000 ns hardware = 600,000 ns = 600 us.
        self.assertEqual(pkt.capture_delay_ns, 600_000)
        self.assertEqual(pkt.kernel_queueing_jitter_us, 600.0)
        self.assertFalse(pkt.degraded)
        self.assertTrue(pkt.cross_timebase_comparison)

    def test_software_only_scm_timestamping_is_not_labelled_hardware(self):
        # ts[2] all-zero means the adapter did not timestamp this packet.
        cmsg = scm_timestamping(software=(BASE_SEC, 250_000))
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, cmsg)],
            app_receipt_ns=BASE_NS + 300_000,
        )
        self.assertEqual(pkt.source, TimestampSource.KERNEL_SOFTWARE)
        self.assertIsNone(pkt.hardware_timestamp_ns)
        self.assertTrue(pkt.degraded)
        self.assertFalse(pkt.cross_timebase_comparison)
        self.assertEqual(pkt.capture_delay_ns, 50_000)

    def test_deprecated_ts1_slot_is_ignored(self):
        cmsg = scm_timestamping(
            software=(BASE_SEC, 100), legacy=(BASE_SEC, 777_777), hardware=(BASE_SEC, 200)
        )
        software_ns, hardware_ns = decode_scm_timestamping(cmsg)
        self.assertEqual(software_ns, BASE_NS + 100)
        self.assertEqual(hardware_ns, BASE_NS + 200)

    def test_32bit_old_layout_24_bytes(self):
        cmsg = scm_timestamping(
            software=(BASE_SEC, 111), hardware=(BASE_SEC, 222), slot=8
        )
        self.assertEqual(len(cmsg), 24)
        self.assertEqual(decode_scm_timestamping(cmsg), (BASE_NS + 111, BASE_NS + 222))

    def test_scm_timestamping_new_cmsg_type_is_accepted(self):
        cmsg = scm_timestamping(hardware=(BASE_SEC, 12_345))
        ts_ns, source = self.engine.unpack_ancillary_timestamp(
            [(socket.SOL_SOCKET, SO_TIMESTAMPING_NEW, cmsg)], BASE_NS
        )
        self.assertEqual(source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(ts_ns, BASE_NS + 12_345)

    def test_truncated_control_buffer_is_rejected_not_misdecoded(self):
        truncated = scm_timestamping(hardware=(BASE_SEC, 500))[:32]
        self.assertEqual(decode_scm_timestamping(truncated), (None, None))
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, truncated)],
            app_receipt_ns=BASE_NS,
        )
        self.assertEqual(pkt.source, TimestampSource.APPLICATION_FALLBACK)
        self.assertTrue(pkt.degraded)


class TestCmsgTypeDiscrimination(unittest.TestCase):
    """A 16-byte SOL_SOCKET payload is ambiguous by length; type decides."""

    def setUp(self):
        self.engine = NICHardwareTimestamperEngine(use_hardware_timestamping=False)

    def test_scm_timestampns_is_kernel_software_not_hardware(self):
        # Regression: the previous decoder classified any >=16-byte SOL_SOCKET
        # payload as HARDWARE_NIC, so SO_TIMESTAMPNS - a kernel receive-path
        # software timestamp - was reported as a wire timestamp.
        cmsg = timespec(BASE_SEC, 500_000)
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPNS_OLD, cmsg)],
            app_receipt_ns=BASE_NS + 1_000_000,
        )
        self.assertEqual(pkt.source, TimestampSource.KERNEL_SOFTWARE)
        self.assertEqual(pkt.timestamp_ns, BASE_NS + 500_000)
        self.assertEqual(pkt.kernel_queueing_jitter_us, 500.0)
        self.assertIsNone(pkt.hardware_timestamp_ns)

    def test_scm_timestamp_timeval_is_microseconds_not_nanoseconds(self):
        # Regression: a 16-byte struct timeval was unpacked as a timespec, so
        # tv_usec=500_000 (half a second) decoded as 500_000 ns - 1000x small.
        cmsg = timeval(BASE_SEC, 500_000)
        self.assertEqual(len(cmsg), 16)
        ts_ns, source = self.engine.unpack_ancillary_timestamp(
            [(socket.SOL_SOCKET, SO_TIMESTAMP_OLD, cmsg)], BASE_NS
        )
        self.assertEqual(source, TimestampSource.KERNEL_SO_TIMESTAMP)
        self.assertEqual(ts_ns, BASE_NS + 500_000_000)

    def test_hardware_wins_when_multiple_cmsgs_arrive_out_of_order(self):
        # SCM_TIMESTAMPNS listed first; the hardware timestamp must still win.
        ancillary = [
            (socket.SOL_SOCKET, SO_TIMESTAMPNS_OLD, timespec(BASE_SEC, 800_000)),
            (socket.SOL_SOCKET, SO_TIMESTAMPING_OLD,
             scm_timestamping(hardware=(BASE_SEC, 100_000))),
        ]
        ts_ns, source = self.engine.unpack_ancillary_timestamp(ancillary, BASE_NS)
        self.assertEqual(source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(ts_ns, BASE_NS + 100_000)

    def test_non_socket_level_cmsg_is_ignored(self):
        ancillary = [(socket.IPPROTO_IP, SO_TIMESTAMPING_OLD,
                      scm_timestamping(hardware=(BASE_SEC, 1)))]
        ts_ns, source = self.engine.unpack_ancillary_timestamp(ancillary, BASE_NS)
        self.assertEqual(source, TimestampSource.APPLICATION_FALLBACK)
        self.assertEqual(ts_ns, BASE_NS)


class TestClockFaultReporting(unittest.TestCase):
    """A negative capture delay is diagnostic and must not be clamped."""

    def setUp(self):
        self.engine = NICHardwareTimestamperEngine()

    def test_undisciplined_phc_reports_signed_negative_delay(self):
        # Regression: the previous engine applied max(0.0, ...), so a NIC clock
        # running 250 us ahead of CLOCK_REALTIME reported 0.0 us of jitter and
        # the PTP fault was invisible.
        cmsg = scm_timestamping(hardware=(BASE_SEC, 250_000))
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, cmsg)],
            app_receipt_ns=BASE_NS,
        )
        self.assertEqual(pkt.capture_delay_ns, -250_000)
        self.assertEqual(pkt.kernel_queueing_jitter_us, -250.0)
        self.assertTrue(pkt.cross_timebase_comparison)

    def test_zero_capture_delay_boundary(self):
        cmsg = scm_timestamping(hardware=(BASE_SEC, 1))
        pkt = self.engine.process_packet_with_nic_timestamp(
            b"TICK", [(socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, cmsg)],
            app_receipt_ns=BASE_NS + 1,
        )
        self.assertEqual(pkt.capture_delay_ns, 0)
        self.assertEqual(pkt.kernel_queueing_jitter_us, 0.0)


class TestFallbackAndValidation(unittest.TestCase):

    def setUp(self):
        self.engine = NICHardwareTimestamperEngine()

    def test_application_fallback_when_ancillary_empty(self):
        pkt = self.engine.process_packet_with_nic_timestamp(b"TICK", [], BASE_NS)
        self.assertEqual(pkt.source, TimestampSource.APPLICATION_FALLBACK)
        self.assertEqual(pkt.timestamp_ns, BASE_NS)
        self.assertIsNone(pkt.capture_delay_ns)
        self.assertTrue(pkt.degraded)

    def test_fallback_is_not_degraded_when_hardware_not_required(self):
        engine = NICHardwareTimestamperEngine(use_hardware_timestamping=False)
        pkt = engine.process_packet_with_nic_timestamp(b"TICK", [], BASE_NS)
        self.assertFalse(pkt.degraded)

    def test_float_epoch_is_rejected(self):
        # 1_784_948_000.001 * 1e9 cannot be represented exactly; binary64
        # spacing at ~1.78e18 is 256 ns, so a float epoch silently loses
        # more precision than this skill is trying to measure.
        with self.assertRaises(TypeError):
            self.engine.process_packet_with_nic_timestamp(b"TICK", [], 1_784_948_000.001)

    def test_negative_epoch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_packet_with_nic_timestamp(b"TICK", [], -1)

    def test_out_of_range_nanoseconds_are_rejected(self):
        # tv_nsec must be < 1e9; a larger value means the buffer is not a
        # timespec and must not be silently multiplied out.
        cmsg = scm_timestamping(hardware=(BASE_SEC, NS_PER_SECOND))
        self.assertEqual(decode_scm_timestamping(cmsg), (None, None))

    def test_malformed_ancillary_entries_are_skipped(self):
        ancillary = [
            "not-a-tuple",
            (socket.SOL_SOCKET, SO_TIMESTAMPING_OLD, "not-bytes"),
            (socket.SOL_SOCKET, SO_TIMESTAMPING_OLD,
             scm_timestamping(hardware=(BASE_SEC, 42))),
        ]
        ts_ns, source = self.engine.unpack_ancillary_timestamp(ancillary, BASE_NS)
        self.assertEqual(source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(ts_ns, BASE_NS + 42)


class TestSocketOptionFlags(unittest.TestCase):

    def test_hardware_only_flag_bitmap(self):
        engine = NICHardwareTimestamperEngine(use_hardware_timestamping=True)
        self.assertEqual(
            engine.timestamping_flags(),
            SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE,
        )
        # 1<<2 | 1<<6 == 68. Independently derived from linux/net_tstamp.h.
        self.assertEqual(engine.timestamping_flags(), 68)

    def test_software_fallback_flag_bitmap(self):
        engine = NICHardwareTimestamperEngine(use_hardware_timestamping=False)
        expected = (
            SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE
            | SOF_TIMESTAMPING_RX_SOFTWARE | SOF_TIMESTAMPING_SOFTWARE
        )
        self.assertEqual(engine.timestamping_flags(), expected)
        # 1<<2 | 1<<6 | 1<<3 | 1<<4 == 92.
        self.assertEqual(engine.timestamping_flags(), 92)

    def test_default_option_number_is_so_timestamping_not_timestampns(self):
        # Regression: the previous engine only ever attempted SO_TIMESTAMPNS
        # (35), which cannot deliver a hardware timestamp at all.
        engine = NICHardwareTimestamperEngine()
        self.assertEqual(engine.so_timestamping, SO_TIMESTAMPING_OLD)
        self.assertNotEqual(engine.so_timestamping, SO_TIMESTAMPNS_OLD)

    def test_architecture_override_is_honoured(self):
        # sparc numbers SO_TIMESTAMPING_OLD 0x0023 (35), not 37.
        engine = NICHardwareTimestamperEngine(
            so_timestamping=0x0023, scm_timestamping_types={0x0023}
        )
        self.assertEqual(engine.so_timestamping, 0x0023)
        ts_ns, source = engine.unpack_ancillary_timestamp(
            [(socket.SOL_SOCKET, 0x0023, scm_timestamping(hardware=(BASE_SEC, 7)))],
            BASE_NS,
        )
        self.assertEqual(source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(ts_ns, BASE_NS + 7)


if __name__ == "__main__":
    unittest.main()
