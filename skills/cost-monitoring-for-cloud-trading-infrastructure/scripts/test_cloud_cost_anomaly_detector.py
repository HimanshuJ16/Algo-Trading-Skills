import unittest
from cloud_cost_anomaly_detector import (
    CloudCostAnomalyDetector, CostRecord
)

class TestCloudCostAnomalyDetector(unittest.TestCase):

    def setUp(self):
        self.detector = CloudCostAnomalyDetector(z_warning_threshold=2.0, z_critical_threshold=3.0)

        # 14 days of baseline compute cost around $100/day (std dev = $5)
        self.history = []
        for i in range(14):
            cost = 100.0 + (i % 3) * 2.5 - 2.5
            self.history.append(CostRecord(
                timestamp=f"2025-05-{i+1:02d}", service_name="EC2_Spot", category="COMPUTE",
                environment="PROD", cost_usd=cost, units_consumed=24.0
            ))

    @staticmethod
    def _record(cost: float, environment: str = "PROD") -> CostRecord:
        return CostRecord(
            timestamp="2025-05-15", service_name="EC2_Spot", category="COMPUTE",
            environment=environment, cost_usd=cost, units_consumed=24.0
        )

    def test_normal_cost_spending(self):
        report = self.detector.analyze_service_cost(
            self._record(102.0), self.history, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "NORMAL")
        self.assertLess(report.z_score, 2.0)

    def test_critical_cost_spike_anomaly(self):
        # Current spend jumps to $500 (Baseline ~ $100 -> Z-score >> 3.0)
        report = self.detector.analyze_service_cost(
            self._record(500.0), self.history, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "CRITICAL")
        self.assertGreaterEqual(report.z_score, 3.0)
        self.assertGreater(report.percentage_change_pct, 100.0)

    def test_unit_cost_calculation(self):
        report = self.detector.analyze_service_cost(
            self._record(200.0), self.history, trading_volume=10000.0
        )
        # Unit cost = $200 / 10,000 trades = $0.02 per trade
        self.assertEqual(report.unit_cost_usd, 0.02)

    # --- Environment-scoped baselines ---------------------------------------

    def test_baseline_scoped_to_environment(self):
        # DEV spend for the same service must not dilute the PROD baseline
        # (old behavior mixed environments: mean ~74 instead of 100).
        mixed_history = self.history + [
            CostRecord(
                timestamp=f"2025-05-{i+1:02d}", service_name="EC2_Spot",
                category="COMPUTE", environment="DEV", cost_usd=5.0, units_consumed=1.0
            )
            for i in range(5)
        ]
        report = self.detector.analyze_service_cost(
            self._record(102.0), mixed_history, trading_volume=1000.0
        )
        # PROD-only baseline mean (1397.5 / 14); mixing in DEV would drag it
        # to ~74.9.
        self.assertAlmostEqual(report.baseline_mean_usd, 1397.5 / 14, places=2)
        self.assertEqual(report.severity, "NORMAL")

    # --- Validation: bad telemetry must be loud, not silently NORMAL ---------

    def test_non_finite_costs_rejected(self):
        with self.assertRaises(ValueError):
            self.detector.analyze_service_cost(
                self._record(float("nan")), self.history
            )
        with self.assertRaises(ValueError):
            self.detector.compute_z_score(100.0, [100.0, float("inf")])
        with self.assertRaises(ValueError):
            self.detector.analyze_service_cost(
                self._record(102.0), self.history, trading_volume=-5.0
            )

    def test_threshold_constructor_validation(self):
        with self.assertRaises(ValueError):
            CloudCostAnomalyDetector(z_warning_threshold=3.0, z_critical_threshold=2.0)
        with self.assertRaises(ValueError):
            CloudCostAnomalyDetector(z_warning_threshold=float("nan"))

    # --- Boundary and degenerate-baseline behavior ----------------------------

    def test_severity_compares_unrounded_z(self):
        # mean=100.25, std=0.25 -> z = 1.996, which ROUNDS to 2.0 but is
        # below the warning threshold; the old code compared the rounded
        # value and flagged WARNING.
        history = [self._record(100.0), self._record(100.5)]
        report = self.detector.analyze_service_cost(
            self._record(100.749), history, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "NORMAL")
        self.assertEqual(report.z_score, 2.0)  # displayed rounded, decided unrounded

    def test_zero_mean_baseline_spike_is_critical(self):
        # A $0 baseline (new service) with a $500 first bill: the percentage
        # increase is unbounded, so the CRITICAL gate must not be bypassed
        # (old behavior forced pct_change to 0 -> WARNING only).
        zero_history = [self._record(0.0) for _ in range(3)]
        report = self.detector.analyze_service_cost(
            self._record(500.0), zero_history
        )
        self.assertEqual(report.severity, "CRITICAL")
        self.assertEqual(report.percentage_change_pct, float("inf"))

    def test_flat_baseline_z_is_dollar_deviation(self):
        flat_history = [self._record(100.0) for _ in range(5)]
        # +$3 on a perfectly flat baseline: z = 3.0 but only +3% spend, so the
        # pct gate keeps it at WARNING instead of CRITICAL.
        report = self.detector.analyze_service_cost(self._record(103.0), flat_history)
        self.assertEqual(report.severity, "WARNING")
        self.assertEqual(report.z_score, 3.0)

        # +40% on a flat baseline: both gates fire -> CRITICAL
        report2 = self.detector.analyze_service_cost(self._record(140.0), flat_history)
        self.assertEqual(report2.severity, "CRITICAL")

    def test_empty_history_reports_unknown_not_healthy(self):
        report = self.detector.analyze_service_cost(self._record(100.0), [])
        self.assertEqual(report.severity, "NORMAL")
        self.assertIn("no baseline history", report.recommendation)
        self.assertIn("UNKNOWN", report.recommendation)

    def test_zero_trading_volume_unit_cost_is_unbounded(self):
        # $500 of spend with zero executed trades is the worst possible unit
        # economics; the old behavior reported $0.00/trade, which reads as
        # perfect efficiency for a halted strategy still burning compute.
        report = self.detector.analyze_service_cost(
            self._record(500.0), self.history, trading_volume=0.0
        )
        self.assertEqual(report.unit_cost_usd, float("inf"))

    def test_zero_volume_zero_spend_unit_cost_is_zero(self):
        report = self.detector.analyze_service_cost(
            self._record(0.0), self.history, trading_volume=0.0
        )
        self.assertEqual(report.unit_cost_usd, 0.0)

    # --- Flat-baseline materiality floor -------------------------------------

    def test_flat_baseline_immaterial_deviation_does_not_warn(self):
        # $3 on a $100,000/day reserved-capacity baseline is +0.003%, but the
        # flat-baseline "z" is a dollar deviation, so it reaches 3.0 and the
        # old behavior paged on-call.
        flat_large = [self._record(100_000.0) for _ in range(14)]
        report = self.detector.analyze_service_cost(
            self._record(100_003.0), flat_large, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "NORMAL")
        self.assertEqual(report.z_score, 3.0)
        self.assertIn("materiality floor", report.recommendation)

    def test_flat_baseline_material_deviation_still_warns(self):
        # Same $3 absolute deviation, but on a $100/day baseline it is +3% -
        # above the 1% floor, so the warning must survive.
        flat_small = [self._record(100.0) for _ in range(14)]
        report = self.detector.analyze_service_cost(
            self._record(103.0), flat_small, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "WARNING")

    def test_materiality_floor_does_not_suppress_real_z_scores(self):
        # Non-flat baseline (std ~ $500 on a $100k mean): +$1,500 is only
        # +1.5% but a genuine 3-sigma statistical outlier. The floor must not
        # apply here.
        history = [
            self._record(100_000.0 + offset)
            for offset in (-500.0, 500.0) * 7
        ]
        report = self.detector.analyze_service_cost(
            self._record(101_500.0), history, trading_volume=1000.0
        )
        self.assertEqual(report.severity, "WARNING")
        self.assertLess(report.percentage_change_pct, 2.0)

    def test_flat_baseline_floor_is_configurable_and_validated(self):
        lenient = CloudCostAnomalyDetector(flat_baseline_min_pct_change=0.0)
        flat_large = [self._record(100_000.0) for _ in range(14)]
        report = lenient.analyze_service_cost(self._record(100_003.0), flat_large)
        self.assertEqual(report.severity, "WARNING")
        with self.assertRaises(ValueError):
            CloudCostAnomalyDetector(flat_baseline_min_pct_change=-1.0)
        with self.assertRaises(ValueError):
            CloudCostAnomalyDetector(flat_baseline_min_pct_change=float("nan"))

    def test_compute_z_score_public_api_unchanged(self):
        z, mean, std = self.detector.compute_z_score(110.0, [100.0, 100.0, 100.0])
        self.assertEqual((z, mean, std), (10.0, 100.0, 0.0))
        z2, mean2, std2 = self.detector.compute_z_score(101.0, [])
        self.assertEqual((z2, mean2, std2), (0.0, 101.0, 0.0))


if __name__ == '__main__':
    unittest.main()
