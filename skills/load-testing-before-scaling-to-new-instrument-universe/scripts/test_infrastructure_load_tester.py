import logging
import math
import unittest

from infrastructure_load_tester import (
    STATUS_FAILED_IOPS,
    STATUS_FAILED_MEMORY,
    STATUS_FAILED_NETWORK,
    STATUS_PASSED,
    HardwareCapacitySpec,
    InfrastructureLoadTesterEngine,
    UniverseScaleSpec,
)


class TestInfrastructureLoadTesterEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Keep the engine's own error logging out of the test runner's stderr without
        # disabling logging globally, which would break assertLogs below.
        logging.getLogger("infrastructure_load_tester").addHandler(logging.NullHandler())

    def setUp(self):
        self.engine = InfrastructureLoadTesterEngine(max_safe_utilization_pct=80.0)
        self.hardware = HardwareCapacitySpec(
            available_ram_gb=64.0, max_network_mbps=1000.0, max_db_iops=50000.0
        )
        # Headroom on every axis, so a test can breach exactly one resource on purpose.
        self.roomy_hardware = HardwareCapacitySpec(
            available_ram_gb=1000.0, max_network_mbps=1000.0, max_db_iops=1_000_000.0
        )

    # ---------------------------------------------------------------- projections

    def test_successful_universe_scaling_load(self):
        # Scale 50 -> 500 symbols. Expected values derived by hand, not from the code:
        #   peak msg/sec = 500 * 10.0 * 5.0                       = 25,000
        #   RAM GB       = 500 * 20 MB * 1.25 / 1024              = 12.20703125
        #   Mbps         = 25,000 * 320 B * 8 bits / 1e6          = 64.0
        #   write IOPS   = 25,000 * 0.50 / 1.0                    = 12,500
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec(
                universe_name="S&P 500 Scaling",
                current_universe_size=50,
                target_universe_size=500,
            ),
            self.hardware,
        )

        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.projected_peak_msg_rate_per_sec, 25000.0)
        self.assertAlmostEqual(report.projected_ram_required_gb, 12.20703125, places=8)
        self.assertAlmostEqual(report.projected_network_mbps, 64.0, places=8)
        self.assertAlmostEqual(report.projected_db_iops, 12500.0, places=8)
        self.assertAlmostEqual(report.universe_scale_factor, 10.0, places=8)
        self.assertEqual(report.breached_resources, [])
        self.assertTrue(report.is_ram_capacity_ok)
        self.assertTrue(report.is_network_capacity_ok)
        self.assertTrue(report.is_db_iops_capacity_ok)

    def test_utilization_percentages_recomputable_from_report(self):
        # Reported projections are unrounded, so a caller can re-derive every ratio.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("Recompute", 50, 500), self.hardware
        )
        self.assertAlmostEqual(
            report.ram_utilization_pct,
            report.projected_ram_required_gb / self.hardware.available_ram_gb * 100.0,
            places=10,
        )
        self.assertAlmostEqual(
            report.network_utilization_pct,
            report.projected_network_mbps / self.hardware.max_network_mbps * 100.0,
            places=10,
        )

    def test_memory_allocation_buffer_is_applied_multiplicatively(self):
        # Buffer of 1.0 removes the headroom: 500 * 20 MB / 1024 = 9.765625 GB.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("NoBuffer", 50, 500, memory_allocation_buffer=1.0),
            self.hardware,
        )
        self.assertAlmostEqual(report.projected_ram_required_gb, 9.765625, places=8)

    def test_wire_overhead_factor_inflates_bandwidth(self):
        # Payload-only 64.0 Mbps at a 1.25 wire factor = 80.0 Mbps.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("Framed", 50, 500, wire_overhead_factor=1.25),
            self.hardware,
        )
        self.assertAlmostEqual(report.projected_network_mbps, 80.0, places=8)

    def test_ticks_per_write_io_reduces_projected_iops(self):
        # A writer coalescing 10 ticks per IO needs a tenth of the IOPS: 12,500 -> 1,250.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("Batched", 50, 500, ticks_per_write_io=10.0),
            self.hardware,
        )
        self.assertAlmostEqual(report.projected_db_iops, 1250.0, places=8)

    # ---------------------------------------------------------------- gate verdicts

    def test_all_breached_resources_are_reported_not_just_the_first(self):
        # Scale 50 -> 5,000 symbols breaches RAM *and* DB IOPS:
        #   RAM  = 5,000 * 20 * 1.25 / 1024 = 122.07 GB -> 190.7% of 64 GB
        #   IOPS = 125,000                              -> 250.0% of 50,000
        #   Net  = 320.0 Mbps                           ->  32.0% of 1,000 (fine)
        # Reporting only the memory breach hides a second scale-up blocker.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("Russell 3000 Scaling", 50, 5000), self.hardware
        )

        self.assertEqual(report.status, STATUS_FAILED_MEMORY)
        self.assertEqual(report.breached_resources, ["ram", "db_iops"])
        self.assertFalse(report.is_ram_capacity_ok)
        self.assertTrue(report.is_network_capacity_ok)
        self.assertFalse(report.is_db_iops_capacity_ok)
        self.assertGreater(report.ram_utilization_pct, 100.0)
        self.assertIn("DB IOPS", report.audit_notes)

    def test_network_only_breach(self):
        # 1,000 symbols -> 50,000 msg/sec -> 128.0 Mbps against a 100 Mbps NIC.
        hardware = HardwareCapacitySpec(
            available_ram_gb=1000.0, max_network_mbps=100.0, max_db_iops=1_000_000.0
        )
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("NetBound", 100, 1000), hardware
        )
        self.assertEqual(report.status, STATUS_FAILED_NETWORK)
        self.assertEqual(report.breached_resources, ["network"])
        self.assertAlmostEqual(report.projected_network_mbps, 128.0, places=8)

    def test_db_iops_only_breach(self):
        # 1,000 symbols -> 50,000 msg/sec -> 25,000 write IOPS against a 10,000 limit.
        hardware = HardwareCapacitySpec(
            available_ram_gb=1000.0, max_network_mbps=1000.0, max_db_iops=10000.0
        )
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("IopsBound", 100, 1000), hardware
        )
        self.assertEqual(report.status, STATUS_FAILED_IOPS)
        self.assertEqual(report.breached_resources, ["db_iops"])
        self.assertAlmostEqual(report.projected_db_iops, 25000.0, places=8)

    def test_utilization_exactly_at_ceiling_passes(self):
        # 6,250 symbols -> 312,500 msg/sec -> exactly 800.0 Mbps = 80.00% of 1,000 Mbps.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("ExactlyAtCeiling", 100, 6250), self.roomy_hardware
        )
        self.assertAlmostEqual(report.network_utilization_pct, 80.0, places=9)
        self.assertEqual(report.status, STATUS_PASSED)

    def test_breach_that_rounds_back_to_the_ceiling_still_fails(self):
        # 800.4 Mbps of 1,000 = 80.04% utilization, which rounds to 80.0% at one decimal
        # place. Comparing the *rounded* figure against the 80.0% ceiling passed this
        # over-capacity plan; the comparison must use the unrounded value.
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec(
                "JustOverCeiling", 100, 6250, avg_ticks_sec_per_symbol=10.005
            ),
            self.roomy_hardware,
        )
        self.assertEqual(round(report.network_utilization_pct, 1), 80.0)
        self.assertGreater(report.network_utilization_pct, 80.0)
        self.assertEqual(report.status, STATUS_FAILED_NETWORK)

    def test_shrinking_universe_is_flagged(self):
        with self.assertLogs("infrastructure_load_tester", level="WARNING") as captured:
            report = self.engine.audit_universe_scaling_load(
                UniverseScaleSpec("Shrink", 500, 50), self.hardware
            )
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertLess(report.universe_scale_factor, 1.0)
        self.assertTrue(any("smaller than" in line for line in captured.output))

    # ---------------------------------------------------------------- input validation

    def test_non_positive_hardware_capacity_is_rejected(self):
        # A negative capacity used to invert the ratio into a negative utilization, which
        # cleared the ceiling check and returned PASSED; a zero capacity raised
        # ZeroDivisionError out of the middle of the audit.
        for kwargs in (
            {"available_ram_gb": -64.0},
            {"available_ram_gb": 0.0},
            {"max_network_mbps": 0.0},
            {"max_db_iops": -1.0},
            {"available_cpu_cores": 0},
        ):
            base = {
                "available_ram_gb": 64.0,
                "max_network_mbps": 1000.0,
                "max_db_iops": 50000.0,
            }
            base.update(kwargs)
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    HardwareCapacitySpec(**base)

    def test_mutated_spec_is_revalidated_at_audit_time(self):
        hardware = HardwareCapacitySpec(
            available_ram_gb=64.0, max_network_mbps=1000.0, max_db_iops=50000.0
        )
        hardware.max_db_iops = 0.0  # dataclasses are mutable after construction
        with self.assertRaises(ValueError):
            self.engine.audit_universe_scaling_load(
                UniverseScaleSpec("Mutated", 50, 500), hardware
            )

    def test_non_positive_universe_sizes_are_rejected(self):
        with self.assertRaises(ValueError):
            UniverseScaleSpec("Zero", 50, 0)
        with self.assertRaises(ValueError):
            UniverseScaleSpec("Negative", 50, -10)
        with self.assertRaises(ValueError):
            UniverseScaleSpec("NoCurrent", 0, 500)

    def test_non_finite_load_inputs_are_rejected(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    UniverseScaleSpec("NonFinite", 50, 500, avg_ticks_sec_per_symbol=value)
                with self.assertRaises(ValueError):
                    UniverseScaleSpec("NonFinite", 50, 500, memory_mb_per_orderbook=value)

    def test_db_write_fraction_must_be_a_fraction(self):
        for value in (-0.1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    UniverseScaleSpec("BadFraction", 50, 500, db_write_fraction=value)
        # 0.0 and 1.0 are both legitimate: persist nothing, or persist every tick.
        self.assertEqual(
            self.engine.audit_universe_scaling_load(
                UniverseScaleSpec("NoPersist", 50, 500, db_write_fraction=0.0),
                self.hardware,
            ).projected_db_iops,
            0.0,
        )

    def test_multipliers_below_one_are_rejected(self):
        # A peak below the average, or a "buffer" that shrinks the requirement, would
        # quietly under-state the load the gate is supposed to catch.
        with self.assertRaises(ValueError):
            UniverseScaleSpec("SubUnitPeak", 50, 500, peak_volatility_multiplier=0.5)
        with self.assertRaises(ValueError):
            UniverseScaleSpec("SubUnitBuffer", 50, 500, memory_allocation_buffer=0.9)
        with self.assertRaises(ValueError):
            UniverseScaleSpec("SubUnitWire", 50, 500, wire_overhead_factor=0.8)
        with self.assertRaises(ValueError):
            UniverseScaleSpec("SubUnitBatch", 50, 500, ticks_per_write_io=0.5)

    def test_empty_universe_name_is_rejected(self):
        with self.assertRaises(ValueError):
            UniverseScaleSpec("   ", 50, 500)

    def test_invalid_utilization_ceiling_is_rejected(self):
        for ceiling in (0.0, -10.0, 100.1, float("nan")):
            with self.subTest(ceiling=ceiling):
                with self.assertRaises(ValueError):
                    InfrastructureLoadTesterEngine(max_safe_utilization_pct=ceiling)

    def test_report_contains_no_nan_values(self):
        report = self.engine.audit_universe_scaling_load(
            UniverseScaleSpec("Sane", 50, 500), self.hardware
        )
        for value in (
            report.projected_peak_msg_rate_per_sec,
            report.projected_ram_required_gb,
            report.projected_network_mbps,
            report.projected_db_iops,
            report.ram_utilization_pct,
            report.network_utilization_pct,
            report.db_iops_utilization_pct,
            report.universe_scale_factor,
        ):
            self.assertTrue(math.isfinite(value))


if __name__ == '__main__':
    unittest.main()
