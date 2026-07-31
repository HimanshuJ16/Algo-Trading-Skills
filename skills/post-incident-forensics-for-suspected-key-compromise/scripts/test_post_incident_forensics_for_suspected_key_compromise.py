import unittest
from post_incident_forensics_for_suspected_key_compromise import (
    Analyzer, Result, KeyForensicsAnalyzer,
    KeyCompromiseIncident, KeyAccessLog, OnChainTx, KeyForensicsReport
)

class TestPostIncidentForensicsForSuspectedKeyCompromise(unittest.TestCase):

    def test_legacy_success(self):
        analyzer = Analyzer({"key": "value"})
        res = analyzer.execute()
        self.assertTrue(res.success)

    def test_legacy_failure(self):
        analyzer = Analyzer({})
        res = analyzer.execute()
        self.assertFalse(res.success)

    def test_key_forensics_unauthorized_access_detected(self):
        incident = KeyCompromiseIncident(
            key_id="KEY-HOT-01",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
            suspected_leak_time="2026-07-31T12:00:00Z",
            affected_systems=["HotWalletBot", "KMS_Prod"]
        )

        auth_ips = ["192.168.1.10", "10.0.0.5"]
        logs = [
            KeyAccessLog("2026-07-31T12:01:00Z", "192.168.1.10", "SIGN_TRANSACTION", 200, auth_ips),
            KeyAccessLog("2026-07-31T12:05:00Z", "198.51.100.44", "EXPORT_KEY", 200, auth_ips), # Unauthorized IP!
        ]

        txs = [
            OnChainTx("0xabc123", "0x1234567890abcdef1234567890abcdef12345678", "0x99999", 50.0, "2026-07-31T12:06:00Z")
        ]

        analyzer = KeyForensicsAnalyzer()
        report = analyzer.run_forensic_analysis(incident, logs, txs)

        self.assertEqual(report.status, "UNAUTHORIZED_ACCESS_CONFIRMED")
        self.assertEqual(report.unauthorized_access_count, 1)
        self.assertIn("198.51.100.44", report.unauthorized_ips)
        self.assertEqual(report.total_exfiltrated_crypto, 50.0)
        self.assertTrue(len(report.evidence_sha256_hash) == 64)

if __name__ == '__main__':
    unittest.main()
