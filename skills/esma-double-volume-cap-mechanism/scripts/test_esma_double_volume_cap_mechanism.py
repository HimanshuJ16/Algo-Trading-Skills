import unittest
from esma_double_volume_cap_mechanism import (
    EsmaDoubleVolumeCapEngine, DarkTradingVolumeMetrics, SorOrderRouteRequest, EsmaDvcAuditReport
)

class TestEsmaDoubleVolumeCapEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EsmaDoubleVolumeCapEngine(single_venue_cap_pct=4.0, eu_wide_cap_pct=8.0)

    def test_dvc_8pct_eu_breach_reroutes_to_lit_venue(self):
        # Total EU Vol = €1,000,000,000. EU Dark Vol = €85,000,000 (8.5% > 8.0% EU Cap -> SUSPENDED!)
        metrics = DarkTradingVolumeMetrics(
            isin="DE0007100000", symbol="MBG",
            rolling_12m_total_eu_market_volume_eur=1_000_000_000.0,
            rolling_12m_venue_dark_volume_eur=30_000_000.0,    # 3.0%
            rolling_12m_eu_wide_dark_volume_eur=85_000_000.0   # 8.5%
        )
        req = SorOrderRouteRequest(
            order_id="ORD_SOR_01", isin="DE0007100000", symbol="MBG",
            order_val_eur=50_000.0, intended_waiver_type="RPW"  # €50k < €100k LIS threshold
        )
        report = self.engine.audit_dvc_and_route_order(req, metrics)
        
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.dvc_suspension_status, "SUSPENDED_8PCT_EU_WIDE")
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")

    def test_lis_waiver_exempt_from_dvc_suspension(self):
        # Same suspended stock, but order = €200,000 (>= €100k LIS threshold) -> DARK LIS EXEMPT APPROVED!
        metrics = DarkTradingVolumeMetrics(
            isin="DE0007100000", symbol="MBG",
            rolling_12m_total_eu_market_volume_eur=1_000_000_000.0,
            rolling_12m_venue_dark_volume_eur=30_000_000.0,
            rolling_12m_eu_wide_dark_volume_eur=85_000_000.0
        )
        req = SorOrderRouteRequest(
            order_id="ORD_SOR_02", isin="DE0007100000", symbol="MBG",
            order_val_eur=200_000.0, intended_waiver_type="RPW"
        )
        report = self.engine.audit_dvc_and_route_order(req, metrics)
        
        self.assertTrue(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "DARK_LIS_EXEMPT")

if __name__ == '__main__':
    unittest.main()
