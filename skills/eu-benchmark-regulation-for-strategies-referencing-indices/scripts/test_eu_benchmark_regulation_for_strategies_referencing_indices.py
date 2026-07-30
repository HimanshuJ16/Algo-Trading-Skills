import unittest
from eu_benchmark_regulation_for_strategies_referencing_indices import (
    EuBmrComplianceEngine, BenchmarkSpec, StrategyBenchmarkUsage, EuBmrAuditReport
)

class TestEuBmrComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EuBmrComplianceEngine()
        
        # EURO STOXX 50 (ESMA Registered, STOXX Ltd)
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_STOXX50",
            benchmark_name="EURO STOXX 50",
            administrator_name="STOXX Ltd",
            is_esma_registered=True,
            category="SIGNIFICANT",
            fallback_benchmark_name="STOXX Europe 600"
        ))

        # Unregistered Custom Index
        self.engine.register_benchmark(BenchmarkSpec(
            benchmark_id="BM_CUSTOM_ALPHA",
            benchmark_name="Custom Prop Alpha Index",
            administrator_name="Unlicensed Vendor",
            is_esma_registered=False,
            category="NON_SIGNIFICANT",
            fallback_benchmark_name="NONE"
        ))

    def test_compliant_esma_registered_benchmark_with_fallback(self):
        usage = StrategyBenchmarkUsage(
            strategy_id="STRAT_EU_EQUITY_01",
            strategy_name="EU Index Arbitrage",
            referenced_benchmark_id="BM_STOXX50",
            has_written_fallback_plan=True
        )
        report = self.engine.audit_strategy_bmr_compliance(usage)
        
        self.assertEqual(report.compliance_status, "BMR_COMPLIANT")
        self.assertTrue(report.is_esma_registered)
        self.assertTrue(report.has_written_fallback_plan)
        self.assertEqual(report.fallback_benchmark_name, "STOXX Europe 600")

    def test_unauthorized_benchmark_violation(self):
        usage = StrategyBenchmarkUsage(
            strategy_id="STRAT_EU_EQUITY_02",
            strategy_name="Custom Prop Arbitrage",
            referenced_benchmark_id="BM_CUSTOM_ALPHA",
            has_written_fallback_plan=True
        )
        report = self.engine.audit_strategy_bmr_compliance(usage)
        
        self.assertEqual(report.compliance_status, "UNAUTHORIZED_BENCHMARK_VIOLATION")
        self.assertFalse(report.is_esma_registered)

    def test_missing_article_28_2_fallback_plan_violation(self):
        usage = StrategyBenchmarkUsage(
            strategy_id="STRAT_EU_EQUITY_03",
            strategy_name="EU Index Hedging",
            referenced_benchmark_id="BM_STOXX50",
            has_written_fallback_plan=False              # Missing Article 28(2) Written Plan!
        )
        report = self.engine.audit_strategy_bmr_compliance(usage)
        
        self.assertEqual(report.compliance_status, "MISSING_FALLBACK_PLAN_VIOLATION")
        self.assertFalse(report.has_written_fallback_plan)

if __name__ == '__main__':
    unittest.main()
