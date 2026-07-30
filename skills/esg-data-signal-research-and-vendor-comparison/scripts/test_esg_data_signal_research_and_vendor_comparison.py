import unittest
from esg_data_signal_research_and_vendor_comparison import (
    EsgDataSignalEngine, RawVendorEsgData, EsgSignalAuditReport
)

class TestEsgDataSignalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EsgDataSignalEngine(disagreement_threshold=0.25)

    def test_bullish_esg_leader_consensus(self):
        # MSCI = 'AAA' (1.0), Sustainalytics Risk = 15.0 (0.85), Refinitiv = 85.0 (0.85) -> Consensus = 0.90, Dispersion ~ 0.07 -> BULLISH!
        data = RawVendorEsgData(
            ticker="MSFT",
            msci_rating="AAA",
            sustainalytics_risk_score=15.0,
            refinitiv_esg_score=85.0
        )
        report = self.engine.analyze_esg_signal(data)
        
        self.assertEqual(report.signal, "BULLISH_ESG_LEADER")
        self.assertEqual(report.consensus_esg_score, 0.90)
        self.assertFalse(report.has_high_vendor_disagreement)
        self.assertEqual(report.normalized_msci_score, 1.0)

    def test_high_vendor_disagreement_detection(self):
        # MSCI = 'AAA' (1.0), Sustainalytics Risk = 60.0 (0.40) -> High Dispersion > 0.25 -> HIGH DISAGREEMENT!
        data = RawVendorEsgData(
            ticker="TSLA",
            msci_rating="AAA",
            sustainalytics_risk_score=60.0
        )
        report = self.engine.analyze_esg_signal(data)
        
        self.assertTrue(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, "NEUTRAL_HIGH_DISAGREEMENT")
        self.assertGreater(report.vendor_disagreement_dispersion, 0.25)

if __name__ == '__main__':
    unittest.main()
