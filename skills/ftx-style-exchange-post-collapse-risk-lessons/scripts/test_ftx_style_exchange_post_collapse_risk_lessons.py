import unittest
from ftx_style_exchange_post_collapse_risk_lessons import (
    ExchangePostCollapseRiskEngine, ExchangeSolvencyMetrics, ExchangePostCollapseRiskReport
)

class TestExchangePostCollapseRiskEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangePostCollapseRiskEngine(
            min_por_coverage_ratio=1.00,
            max_native_token_ratio=0.05,
            max_single_venue_nav_pct=0.20
        )

    def test_acceptable_venue_risk_profile(self):
        metrics = ExchangeSolvencyMetrics(
            venue_id="BINANCE", exchange_name="Binance International",
            proof_of_reserves_ratio=1.05, native_token_collateral_pct=0.02,
            uses_off_exchange_settlement=True, nav_exposure_pct=0.15,
            has_independent_audit_report=True
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertEqual(report.status, "VENUE_RISK_ACCEPTABLE")
        self.assertFalse(report.is_derisking_triggered)
        self.assertTrue(report.is_por_valid)
        self.assertTrue(report.is_native_token_safe)
        self.assertEqual(report.risk_score_0_to_100, 0.0)

    def test_ftx_style_insolvency_triggers_derisking(self):
        # Deficit PoR (85%), High Native Token (35% FTT), No OES, NAV = 40% -> DERISK TRIGGERED!
        metrics = ExchangeSolvencyMetrics(
            venue_id="FTX_RISK_EXCHANGE", exchange_name="Opaque Crypto Exchange",
            proof_of_reserves_ratio=0.85, native_token_collateral_pct=0.35,
            uses_off_exchange_settlement=False, nav_exposure_pct=0.40,
            has_independent_audit_report=False
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertEqual(report.status, "EXCHANGE_DERISK_TRIGGERED")
        self.assertTrue(report.is_derisking_triggered)
        self.assertFalse(report.is_por_valid)
        self.assertFalse(report.is_native_token_safe)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 100.0)

if __name__ == '__main__':
    unittest.main()
