import unittest
from prime_brokerage_multi_venue_consolidation import (
    Config, IntegrationEngine, PrimeBrokerageMultiVenueConsolidationEngine,
    VenueExecution, PrimeBrokerSpec, PBConsolidationReport
)

class TestPrimeBrokerageMultiVenueConsolidation(unittest.TestCase):

    def setUp(self):
        self.config = Config(api_key="test_key")
        self.legacy_engine = IntegrationEngine(self.config)
        self.pb_spec = PrimeBrokerSpec("GOLDMAN_SACHS", "PB_ACCT_99182", clearing_fee_per_share=0.0005)
        self.consolidation_engine = PrimeBrokerageMultiVenueConsolidationEngine(self.config, self.pb_spec)

    def test_legacy_connect_and_process(self):
        self.assertTrue(self.legacy_engine.connect())
        self.assertEqual(self.legacy_engine.process(), "success")

    def test_multi_venue_consolidation_and_netting(self):
        # Trade 1: BUY 1,000 AAPL @ $150 via Broker A on NASDAQ
        # Trade 2: SELL 400 AAPL @ $151 via Broker B on BATS
        # Net AAPL = +600 shares. Total Gross = 1,400 shares ($210,400 notional)
        # Margin savings = (1 - 600/1400) * 100% = 57.14%
        executions = [
            VenueExecution("EXEC_01", "BROKER_A", "NASDAQ", "AAPL", "BUY", 1000.0, 150.0, "2026-07-31"),
            VenueExecution("EXEC_02", "BROKER_B", "BATS", "AAPL", "SELL", 400.0, 151.0, "2026-07-31"),
        ]

        report = self.consolidation_engine.consolidate_venue_executions(executions)
        self.assertEqual(report.status, "CONSOLIDATION_SUCCESSFUL")
        self.assertEqual(report.total_executions_consolidated, 2)
        self.assertEqual(report.net_positions_by_symbol["AAPL"], 600.0)
        self.assertEqual(report.total_gross_notional_usd, 210400.0)
        self.assertAlmostEqual(report.consolidated_margin_savings_pct, 57.14, places=2)
        self.assertEqual(len(report.giveup_payload), 2)

if __name__ == '__main__':
    unittest.main()
