"""
Unit tests for cross-datacenter-clock-sync-validation skill.

Expected drift values are derived by hand from the probe inputs rather than by
re-running the implementation's own arithmetic.
"""
import unittest
from clock_sync_validator import (
    ClockProbeError,
    ClockSyncHealth,
    CrossDatacenterClockSyncValidator,
    DatacenterClockProbe,
    MIFID_HFT_MAX_UTC_DIVERGENCE_MS,
    MIFID_OTHER_ALGO_MAX_UTC_DIVERGENCE_MS,
    MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS,
    RESOLUTION_FLOOR_MS,
)

T0 = 1784948000.0


def probe(region, ts_delta_sec=0.0, offset_ms=0.0, rtt_ms=0.0, dispersion_ms=0.0):
    """Builds a probe with a sub-millisecond sync path unless told otherwise."""
    return DatacenterClockProbe(
        region_id=region,
        datacenter_name=region.upper(),
        timestamp_sec=T0 + ts_delta_sec,
        reported_offset_ms=offset_ms,
        rtt_ms=rtt_ms,
        root_dispersion_ms=dispersion_ms,
    )


def offset_pair(drift_ms, rtt_ms=0.0):
    """
    Two probes sharing one sampling instant, separated purely by their
    reported offsets. Drift is then exactly ``drift_ms`` with no epoch-
    magnitude quantization, which is what boundary assertions need.
    """
    return [
        probe("a", 0.0, offset_ms=0.0, rtt_ms=rtt_ms),
        probe("b", 0.0, offset_ms=drift_ms, rtt_ms=rtt_ms),
    ]


class TestCrossDatacenterClockSyncValidator(unittest.TestCase):

    def setUp(self):
        self.validator = CrossDatacenterClockSyncValidator(max_allowed_drift_ms=1.0)

    # ---------------- normal operation ----------------

    def test_acceptable_sync_across_regions(self):
        # NY4 clock reads T0 with a +0.05ms self-reported offset; LD4 reads
        # T0 + 0.3ms with +0.02ms. Relative disagreement by hand:
        #   (0 + 0.05) - (0.3 + 0.02) = -0.27  ->  0.27ms
        probes = [
            probe("us-east-1", 0.0, 0.05, rtt_ms=0.12),
            probe("eu-west-1", 0.0003, 0.02, rtt_ms=0.20),
        ]
        report = self.validator.validate_datacenter_sync(probes)

        self.assertTrue(report.is_arbitration_allowed)
        self.assertEqual(report.health, ClockSyncHealth.ACCEPTABLE)
        self.assertAlmostEqual(report.max_drift_ms, 0.27, delta=RESOLUTION_FLOOR_MS)
        self.assertEqual(report.vetoed_pairs, [])
        self.assertTrue(report.is_measurement_conclusive)

    def test_excellent_tier_for_ptp_class_agreement(self):
        # 0.04ms apart -> inside the 0.1ms excellent tier.
        report = self.validator.validate_datacenter_sync([
            probe("us-east-1", 0.0, 0.0, rtt_ms=0.05),
            probe("eu-west-1", 0.00004, 0.0, rtt_ms=0.05),
        ])
        self.assertEqual(report.health, ClockSyncHealth.EXCELLENT)
        self.assertTrue(report.is_arbitration_allowed)

    def test_all_pairs_are_evaluated_and_worst_pair_reported(self):
        # Three regions -> three unordered pairs. Worst pair is a<->c at 3.0ms.
        report = self.validator.validate_datacenter_sync([
            probe("a", 0.0, rtt_ms=0.1),
            probe("b", 0.0005, rtt_ms=0.1),
            probe("c", 0.003, rtt_ms=0.1),
        ])
        self.assertEqual(len(report.pairwise_drift_ms), 3)
        self.assertAlmostEqual(report.pairwise_drift_ms["a<->b"], 0.5, delta=RESOLUTION_FLOOR_MS)
        self.assertAlmostEqual(report.pairwise_drift_ms["b<->c"], 2.5, delta=RESOLUTION_FLOOR_MS)
        self.assertAlmostEqual(report.pairwise_drift_ms["a<->c"], 3.0, delta=RESOLUTION_FLOOR_MS)
        self.assertAlmostEqual(report.max_drift_ms, 3.0, delta=RESOLUTION_FLOOR_MS)
        self.assertCountEqual(report.vetoed_pairs, ["b<->c", "a<->c"])

    # ---------------- tier boundaries ----------------

    def test_tier_boundaries_are_inclusive_upper_bounds(self):
        v = CrossDatacenterClockSyncValidator(
            max_allowed_drift_ms=1.0, excellent_drift_ms=0.1, degraded_ceiling_ms=5.0
        )
        # Drift is built from reported offsets, not epoch deltas, so each
        # boundary lands exactly on the threshold (see RESOLUTION_FLOOR_MS).
        cases = [
            (0.1, ClockSyncHealth.EXCELLENT, True),      # 0.1ms exactly
            (0.1001, ClockSyncHealth.ACCEPTABLE, True),  # just over 0.1ms
            (1.0, ClockSyncHealth.ACCEPTABLE, True),     # 1.0ms exactly: allowed
            (1.0001, ClockSyncHealth.DEGRADED, False),   # just over the limit
            (5.0, ClockSyncHealth.DEGRADED, False),      # 5.0ms exactly
            (5.0001, ClockSyncHealth.BREACH, False),     # just over 5.0ms
        ]
        for drift_ms, expected_health, expected_allowed in cases:
            with self.subTest(drift_ms=drift_ms):
                report = v.validate_datacenter_sync(offset_pair(drift_ms))
                self.assertAlmostEqual(report.max_drift_ms, drift_ms, places=6)
                self.assertEqual(report.health, expected_health)
                self.assertEqual(report.is_arbitration_allowed, expected_allowed)

    def test_degraded_tier_denies_arbitration(self):
        # Regression: references/standards.md previously described DEGRADED as
        # "warning alert logged", implying arbitration continues. It does not -
        # anything above max_allowed_drift_ms is vetoed.
        report = self.validator.validate_datacenter_sync(offset_pair(2.0))
        self.assertEqual(report.health, ClockSyncHealth.DEGRADED)
        self.assertFalse(report.is_arbitration_allowed)
        self.assertIn("CLOCK_UNSYNC_VETO", report.message)

    def test_clock_sync_drift_breach_veto(self):
        report = self.validator.validate_datacenter_sync(offset_pair(8.0, rtt_ms=0.1))
        self.assertFalse(report.is_arbitration_allowed)
        self.assertEqual(report.health, ClockSyncHealth.BREACH)
        self.assertAlmostEqual(report.max_drift_ms, 8.0, places=6)

    # ---------------- fail-closed behaviour ----------------

    def test_insufficient_probes_fail_closed(self):
        # Regression: an empty or single-probe set previously returned
        # EXCELLENT with arbitration permitted, so a failed remote probe read
        # as "one healthy region, proceed".
        for probes in ([], [probe("us-east-1")]):
            with self.subTest(n=len(probes)):
                report = self.validator.validate_datacenter_sync(probes)
                self.assertEqual(report.health, ClockSyncHealth.UNKNOWN)
                self.assertFalse(report.is_arbitration_allowed)
                self.assertFalse(report.is_measurement_conclusive)
                self.assertEqual(report.pairwise_drift_ms, {})
                self.assertIn("CLOCK_UNSYNC_VETO", report.message)

    def test_non_finite_probe_values_are_rejected(self):
        # Regression: NaN silently produced EXCELLENT + arbitration allowed,
        # because `nan > max_observed_drift` is False and left the running
        # maximum at 0.0.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ClockProbeError):
                    DatacenterClockProbe("a", "NY4", bad, 0.0, 0.1)
                with self.assertRaises(ClockProbeError):
                    DatacenterClockProbe("a", "NY4", T0, bad, 0.1)
                with self.assertRaises(ClockProbeError):
                    DatacenterClockProbe("a", "NY4", T0, 0.0, bad)

    def test_duplicate_region_ids_are_rejected(self):
        # Regression: duplicate region_ids collapsed onto one pair key, so
        # three probes produced a single entry and a breaching pair could be
        # overwritten out of the report entirely.
        with self.assertRaises(ClockProbeError) as ctx:
            self.validator.validate_datacenter_sync([
                probe("a", 0.0), probe("a", 0.5), probe("a", 0.0),
            ])
        self.assertIn("unique", str(ctx.exception))

    def test_imprecise_measurement_cannot_certify_the_limit(self):
        # Two clocks 0.3ms apart by point estimate, but each measured over a
        # 70ms root-delay path: uncertainty = 0.5*70 + 0.5*70 = 70ms >> 1ms.
        # The point estimate is within limit, yet nothing is evidenced.
        report = self.validator.validate_datacenter_sync(offset_pair(0.3, rtt_ms=70.0))
        self.assertLessEqual(report.max_drift_ms, 1.0)
        self.assertEqual(report.health, ClockSyncHealth.ACCEPTABLE)
        self.assertFalse(report.is_measurement_conclusive)
        self.assertFalse(report.is_arbitration_allowed)
        self.assertAlmostEqual(report.max_worst_case_drift_ms, 70.3, places=6)

    def test_uncertainty_adds_rather_than_subtracts(self):
        # Guards the documentation defect this skill previously carried
        # (drift = |Ta - Tb| - RTT/2). Uncertainty must widen the worst case.
        tight = self.validator.validate_datacenter_sync(offset_pair(0.5, rtt_ms=0.0))
        loose = self.validator.validate_datacenter_sync(offset_pair(0.5, rtt_ms=0.4))
        self.assertAlmostEqual(tight.max_drift_ms, loose.max_drift_ms, places=9)
        self.assertGreater(loose.max_worst_case_drift_ms, tight.max_worst_case_drift_ms)
        # 0.5ms point estimate + (0.2 + 0.2) uncertainty = 0.9ms
        self.assertAlmostEqual(loose.max_worst_case_drift_ms, 0.9, places=6)

    def test_dispersion_contributes_to_the_error_bound(self):
        # chrony: clock_error <= |offset| + root_dispersion + 0.5 * root_delay
        p = probe("a", 0.0, offset_ms=0.2, rtt_ms=0.4, dispersion_ms=0.05)
        self.assertAlmostEqual(p.clock_error_bound_ms, 0.2 + 0.05 + 0.2, places=6)

    # ---------------- input validation ----------------

    def test_probe_field_validation(self):
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("", "NY4", T0, 0.0, 0.1)
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("a", "   ", T0, 0.0, 0.1)
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("a", "NY4", T0, 0.0, -0.1)          # negative rtt
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("a", "NY4", T0, 0.0, 0.1, -0.1)     # negative dispersion
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("a", "NY4", True, 0.0, 0.1)         # bool as timestamp
        with self.assertRaises(ClockProbeError):
            DatacenterClockProbe("a", "NY4", "1784948000", 0.0, 0.1)  # string

    def test_validator_threshold_validation(self):
        with self.assertRaises(ClockProbeError):
            CrossDatacenterClockSyncValidator(max_allowed_drift_ms=0.0)
        with self.assertRaises(ClockProbeError):
            CrossDatacenterClockSyncValidator(max_allowed_drift_ms=-1.0)
        with self.assertRaises(ClockProbeError):
            CrossDatacenterClockSyncValidator(max_allowed_drift_ms=float("nan"))
        with self.assertRaises(ClockProbeError):
            # excellent tier above the allowed limit is incoherent
            CrossDatacenterClockSyncValidator(max_allowed_drift_ms=1.0, excellent_drift_ms=2.0)
        with self.assertRaises(ClockProbeError):
            # degraded ceiling below the allowed limit is incoherent
            CrossDatacenterClockSyncValidator(max_allowed_drift_ms=1.0, degraded_ceiling_ms=0.5)
        with self.assertRaises(ClockProbeError):
            CrossDatacenterClockSyncValidator(max_sampling_skew_ms=-1.0)

    def test_non_probe_entries_are_rejected(self):
        with self.assertRaises(ClockProbeError):
            self.validator.validate_datacenter_sync([probe("a"), {"region_id": "b"}])

    # ---------------- sampling-skew guard ----------------

    def test_sampling_skew_guard_annotates_the_veto(self):
        # The guard is advisory: it cannot distinguish skew from drift, it can
        # only tell the operator the reading is too large to be plausible drift.
        v = CrossDatacenterClockSyncValidator(
            max_allowed_drift_ms=1.0, max_sampling_skew_ms=50.0
        )
        report = v.validate_datacenter_sync([probe("a", 0.0), probe("b", 0.5)])  # 500ms
        self.assertFalse(report.is_arbitration_allowed)
        self.assertIn("simultaneous snapshot", report.message)

        # Disabled by default, so a plain breach message carries no skew note.
        plain = self.validator.validate_datacenter_sync([probe("a", 0.0), probe("b", 0.5)])  # noqa: E501
        self.assertNotIn("simultaneous snapshot", plain.message)

    # ---------------- sourced constants ----------------

    def test_rts25_constants_match_annex_table_2(self):
        # Commission Delegated Regulation (EU) 2017/574, Annex Table 2:
        # HFT technique 100 microseconds; any other trading activity 1 ms.
        self.assertEqual(MIFID_HFT_MAX_UTC_DIVERGENCE_MS, 0.1)
        self.assertEqual(MIFID_OTHER_ALGO_MAX_UTC_DIVERGENCE_MS, 1.0)
        # Pairwise budget is the sum of two single-clock ceilings.
        self.assertEqual(MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS, 0.2)

    def test_validator_accepts_the_implied_hft_pairwise_budget(self):
        v = CrossDatacenterClockSyncValidator(
            max_allowed_drift_ms=MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS,
            excellent_drift_ms=0.05,
            degraded_ceiling_ms=1.0,
        )
        # 0.15ms apart: inside the 0.2ms implied HFT pairwise budget.
        ok = v.validate_datacenter_sync(offset_pair(0.15, rtt_ms=0.02))
        self.assertTrue(ok.is_arbitration_allowed)
        # 0.25ms apart: outside it.
        bad = v.validate_datacenter_sync(offset_pair(0.25, rtt_ms=0.02))
        self.assertFalse(bad.is_arbitration_allowed)

    def test_epoch_magnitude_quantization_stays_within_the_resolution_floor(self):
        # Regression: the drift computation used to fold each offset into a
        # ~1.8e9 epoch magnitude before subtracting, quantizing a 0.05ms
        # offset to 0.050068ms. Differencing the epoch readings first keeps
        # the error inside the documented floor.
        report = self.validator.validate_datacenter_sync([
            probe("a", 0.0, offset_ms=0.05),
            probe("b", 0.0, offset_ms=0.0),
        ])
        # Same sampling instant, so drift is exactly the offset difference.
        self.assertAlmostEqual(report.max_drift_ms, 0.05, places=9)

        # And an epoch-derived delta stays within one resolution floor.
        epoch_derived = self.validator.validate_datacenter_sync([
            probe("a", 0.0), probe("b", 0.002),
        ])
        self.assertAlmostEqual(epoch_derived.max_drift_ms, 2.0, delta=RESOLUTION_FLOOR_MS)
        self.assertLess(RESOLUTION_FLOOR_MS, MIFID_HFT_MAX_UTC_DIVERGENCE_MS / 100.0)


if __name__ == "__main__":
    unittest.main()
