import unittest
from fpga_based_market_data_processing_evaluation import (
    FpgaMarketDataEvaluationEngine, LatencyProfile, StrategyAlphaMetrics, FpgaHardwareCosts, FpgaEvaluationReport
)

class TestFpgaMarketDataEvaluationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine(
            min_latency_reduction_threshold_ns=1000.0,
            min_roi_net_benefit_usd=25000.0
        )
        # Software CPU Profile: p50 = 2,500ns, p99 = 8,000ns
        self.cpu_prof = LatencyProfile(p50_latency_ns=2500.0, p99_latency_ns=8000.0, max_latency_ns=15000.0, std_dev_jitter_ns=1200.0)
        # FPGA SmartNIC Profile: p50 = 250ns, p99 = 300ns
        self.fpga_prof = LatencyProfile(p50_latency_ns=250.0, p99_latency_ns=300.0, max_latency_ns=450.0, std_dev_jitter_ns=15.0)

    def test_fpga_recommended_for_high_frequency_alpha(self):
        metrics = StrategyAlphaMetrics(daily_trade_frequency=15_000, alpha_half_life_ns=3_000.0, estimated_annual_alpha_gain_usd=250_000.0)
        costs = FpgaHardwareCosts(smartnic_hardware_usd=15_000.0, ip_core_licensing_usd=25_000.0, annual_engineering_maintenance_usd=20_000.0) # TCO = $60,000

        report = self.engine.evaluate_fpga_acceleration(self.cpu_prof, self.fpga_prof, metrics, costs)

        self.assertEqual(report.recommendation, "FPGA_RECOMMENDED")
        self.assertEqual(report.median_latency_reduction_ns, 2250.0)
        self.assertEqual(report.total_cost_of_ownership_usd, 60000.0)
        self.assertEqual(report.net_annual_roi_usd, 190000.0)

    def test_software_cpu_sufficient_when_financial_roi_low(self):
        # Strategy only makes $40,000 alpha vs $60,000 TCO -> Software CPU Sufficient!
        metrics = StrategyAlphaMetrics(daily_trade_frequency=200, alpha_half_life_ns=60_000.0, estimated_annual_alpha_gain_usd=40_000.0)
        costs = FpgaHardwareCosts(smartnic_hardware_usd=15_000.0, ip_core_licensing_usd=25_000.0, annual_engineering_maintenance_usd=20_000.0)

        report = self.engine.evaluate_fpga_acceleration(self.cpu_prof, self.fpga_prof, metrics, costs)

        self.assertEqual(report.recommendation, "SOFTWARE_CPU_SUFFICIENT")
        self.assertLess(report.net_annual_roi_usd, 25000.0)

if __name__ == '__main__':
    unittest.main()
