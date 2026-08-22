import unittest

from clock_synchronization_ptp_for_trading_hosts import (
    MIFID_HFT_MAX_DIVERGENCE_NS,
    PtpClockSyncManager,
)


class FakeClock:
    """Injectable monotonic clock so staleness is tested without sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestPtpClockSyncManager(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.manager = PtpClockSyncManager(
            max_allowed_offset_ns=100000.0,
            target_hft_offset_ns=1000.0,
            clock=self.clock,
        )

    # -- parsing: baseline formats --------------------------------------

    def test_parse_ptp4l_line(self):
        line = "ptp4l[12345.678]: master offset   -42 s2 freq   +1234 path delay   1500"
        sample = self.manager.parse_log_line(line)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.daemon, "ptp4l")
        self.assertEqual(sample.offset_ns, -42.0)
        self.assertEqual(sample.ptp_state, "s2")
        self.assertEqual(sample.path_delay_ns, 1500.0)
        self.assertEqual(sample.freq_adj_ppb, 1234.0)
        self.assertEqual(self.manager.ptp4l_state, "s2")

    def test_parse_phc2sys_line(self):
        line = "phc2sys[12345.678]: CLOCK_REALTIME phc offset   -15 s2 freq   +567"
        sample = self.manager.parse_log_line(line)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.daemon, "phc2sys")
        self.assertEqual(sample.offset_ns, -15.0)
        self.assertEqual(sample.ptp_state, "s2")
        self.assertEqual(self.manager.phc2sys_state, "s2")

    # -- parsing: regressions against real linuxptp output ---------------

    def test_negative_path_delay_is_parsed(self):
        """Regression: a negative path delay used to fail the whole line.

        Negative path delay is a real symptom of bad hardware timestamps. The
        previous ``path delay (\\d+)`` pattern rejected the line entirely, so
        the manager silently kept serving the last good offset and state -
        dropping exactly the samples that signal a broken timestamping path.
        """
        line = "ptp4l[600.123]: master offset 88 s2 freq -25937 path delay -2391"
        sample = self.manager.parse_log_line(line)

        self.assertIsNotNone(sample)
        self.assertEqual(sample.path_delay_ns, -2391.0)
        self.assertEqual(sample.offset_ns, 88.0)
        self.assertEqual(self.manager.latest_ptp4l_offset, 88.0)
        self.assertEqual(self.manager.unparsed_telemetry_lines, 0)

    def test_message_tag_prefix_is_parsed(self):
        """Regression: ``message_tag`` is a documented ptp4l option and is set
        by most orchestrated deployments. The previous pattern required
        ``master offset`` to follow ``]:`` immediately, so every offset line on
        a tagged deployment was dropped and the manager was permanently blind.
        """
        self.manager.parse_log_line(
            "ptp4l[600.123]: [ens1f0] master offset -31 s2 freq -6407 path delay 12224"
        )
        self.manager.parse_log_line(
            "phc2sys[600.124]: [ens1f0] CLOCK_REALTIME phc offset 9 s2 freq +12 delay 500"
        )

        self.assertEqual(self.manager.latest_ptp4l_offset, -31.0)
        self.assertEqual(self.manager.latest_phc2sys_offset, 9.0)
        self.assertEqual(self.manager.unparsed_telemetry_lines, 0)

    def test_syslog_prefixed_line_is_parsed(self):
        line = (
            "Aug 21 09:15:02 trade01 ptp4l[9182.501]: master offset 12 s2 "
            "freq +900 path delay 700"
        )
        sample = self.manager.parse_log_line(line)
        self.assertIsNotNone(sample)
        self.assertEqual(sample.offset_ns, 12.0)

    def test_sys_offset_variant_is_parsed(self):
        """phc2sys prints ``sys offset`` when the PHC is the sink."""
        sample = self.manager.parse_log_line(
            "phc2sys[64.100]: ens1f0 sys offset -21 s2 freq +33 delay 1100"
        )
        self.assertIsNotNone(sample)
        self.assertEqual(sample.daemon, "phc2sys")
        self.assertEqual(sample.offset_ns, -21.0)

    def test_summary_line_reports_max_not_rms(self):
        """``summary_interval``/``-u`` lines carry no servo state, and the
        worst case in the window - not the RMS - is what must clear a ceiling.
        """
        sample = self.manager.parse_log_line(
            "phc2sys[64.361]: CLOCK_REALTIME rms 9 max 20 freq -30264 +/- 4 "
            "delay 2683 +/- 0"
        )
        self.assertIsNotNone(sample)
        self.assertTrue(sample.is_summary)
        self.assertEqual(sample.offset_ns, 9.0)
        self.assertEqual(sample.max_offset_ns, 20.0)
        # Summary lines must not invent a servo state.
        self.assertIsNone(sample.ptp_state)
        self.assertEqual(self.manager.phc2sys_state, "UNKNOWN")
        # The tracked offset is the window maximum, not the RMS.
        self.assertEqual(self.manager.latest_phc2sys_offset, 20.0)

    def test_port_state_transition_is_tracked(self):
        self.manager.parse_log_line(
            "ptp4l[357.214]: port 1: LISTENING to UNCALIBRATED on RS_SLAVE"
        )
        self.assertEqual(self.manager.ptp4l_port_state, "UNCALIBRATED")
        self.manager.parse_log_line(
            "ptp4l[359.100]: port 1: UNCALIBRATED to SLAVE on MASTER_CLOCK_SELECTED"
        )
        self.assertEqual(self.manager.ptp4l_port_state, "SLAVE")

    def test_unparsed_telemetry_is_counted_not_swallowed(self):
        self.manager.parse_log_line("ptp4l[1.0]: master offset BOGUS s2 freq +1")
        self.assertEqual(self.manager.unparsed_telemetry_lines, 1)
        # A line that is not telemetry at all is not counted as a parse failure.
        self.manager.parse_log_line("ptp4l[1.0]: selected /dev/ptp0 as PTP clock")
        self.assertEqual(self.manager.unparsed_telemetry_lines, 1)

    def test_non_string_input_rejected(self):
        with self.assertRaises(TypeError):
            self.manager.parse_log_line(None)

    # -- compliance evaluation ------------------------------------------

    def test_compliance_evaluation_pass(self):
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset -150 s2 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -80 s2 freq +5"
        )

        comp = self.manager.evaluate_compliance()
        self.assertTrue(comp["is_synced"])
        self.assertTrue(comp["mifid_compliant"])
        self.assertTrue(comp["hft_ready"])
        self.assertEqual(comp["max_offset_ns"], 150.0)
        self.assertEqual(comp["combined_offset_ns"], 230.0)
        self.assertEqual(comp["reasons"], ())

    def test_compliance_evaluation_fail_high_offset(self):
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset -150000 s2 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -80 s2 freq +5"
        )

        comp = self.manager.evaluate_compliance()
        self.assertTrue(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertFalse(comp["hft_ready"])

    def test_compliance_evaluation_fail_unlocked_state(self):
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset -10 s0 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5"
        )

        comp = self.manager.evaluate_compliance()
        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertIn("ptp4l servo state s0", comp["reasons"])

    def test_serial_offsets_are_summed_not_maxed(self):
        """Two legs each inside the ceiling can breach it end to end.

        60 us of grandmaster-to-PHC error plus 60 us of PHC-to-CLOCK_REALTIME
        error is 120 us on the timestamp actually recorded. Taking the maximum
        of the legs reports 60 us and passes a 100 us ceiling that the host is
        in fact breaching.
        """
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset 60000 s2 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -60000 s2 freq +5"
        )

        comp = self.manager.evaluate_compliance()
        self.assertEqual(comp["max_offset_ns"], 60000.0)
        self.assertEqual(comp["combined_offset_ns"], 120000.0)
        self.assertFalse(comp["mifid_compliant"])

    def test_missing_daemon_fails_closed(self):
        """Only phc2sys reporting must never read as compliant."""
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5"
        )
        comp = self.manager.evaluate_compliance()

        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertEqual(comp["combined_offset_ns"], float("inf"))
        self.assertIn("no ptp4l telemetry", comp["reasons"])

    def test_fresh_manager_is_not_compliant(self):
        comp = self.manager.evaluate_compliance()
        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertFalse(comp["hft_ready"])

    def test_stale_telemetry_fails_closed(self):
        """Regression: a dead daemon used to leave ``s2`` and a small offset
        latched forever, so a host with no time sync reported compliant.
        """
        manager = PtpClockSyncManager(max_sample_age_s=5.0, clock=self.clock)
        manager.parse_log_line(
            "ptp4l[100.0]: master offset -10 s2 freq +10 path delay 500"
        )
        manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5"
        )
        self.assertTrue(manager.evaluate_compliance()["mifid_compliant"])

        self.clock.advance(6.0)
        comp = manager.evaluate_compliance()
        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertTrue(comp["telemetry_stale"])
        self.assertIn("ptp4l telemetry stale", comp["reasons"])
        self.assertEqual(comp["combined_offset_ns"], float("inf"))

    def test_stale_detection_disabled_by_default(self):
        """Documented blind spot, asserted so it cannot change silently."""
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset -10 s2 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5"
        )
        self.clock.advance(86400.0)
        self.assertTrue(self.manager.evaluate_compliance()["mifid_compliant"])

    def test_faulty_port_state_fails_closed(self):
        self.manager.parse_log_line(
            "ptp4l[100.0]: master offset -10 s2 freq +10 path delay 500"
        )
        self.manager.parse_log_line(
            "phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5"
        )
        self.manager.parse_log_line(
            "ptp4l[101.0]: port 1: SLAVE to FAULTY on FAULT_DETECTED"
        )

        comp = self.manager.evaluate_compliance()
        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertIn("ptp4l port state FAULTY", comp["reasons"])

    def test_default_ceiling_is_the_rts25_hft_row(self):
        manager = PtpClockSyncManager(clock=self.clock)
        self.assertEqual(manager.max_allowed_offset_ns, MIFID_HFT_MAX_DIVERGENCE_NS)
        self.assertEqual(MIFID_HFT_MAX_DIVERGENCE_NS, 100_000.0)

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            PtpClockSyncManager(max_allowed_offset_ns=0)
        with self.assertRaises(ValueError):
            PtpClockSyncManager(target_hft_offset_ns=-1)
        with self.assertRaises(ValueError):
            PtpClockSyncManager(max_sample_age_s=0)


if __name__ == "__main__":
    unittest.main()
