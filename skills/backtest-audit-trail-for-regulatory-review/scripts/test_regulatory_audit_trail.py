"""Unit tests for backtest-audit-trail-for-regulatory-review."""
import unittest
from regulatory_audit_trail import RegulatoryAuditTrailEngine


class TestRegulatoryAuditTrailEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RegulatoryAuditTrailEngine(organization="AlphaHedge LLC")

    def test_build_and_verify_audit_manifest(self):
        manifest = self.engine.build_manifest(
            strategy_id="stat_arb_v3",
            git_commit_sha="41c1d05a8b792e01",
            data_checksum="a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
            parameters={"lookback": 20, "z_entry": 2.0},
            metrics_summary={"sharpe_ratio": 2.15, "max_drawdown_pct": 8.4},
        )

        self.assertEqual(len(manifest.manifest_signature_sha256), 64)
        self.assertTrue(self.engine.verify_manifest(manifest))

    def test_tampered_manifest_fails_verification(self):
        manifest = self.engine.build_manifest(
            strategy_id="stat_arb_v3",
            git_commit_sha="41c1d05a8b792e01",
            data_checksum="a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
            parameters={"lookback": 20, "z_entry": 2.0},
            metrics_summary={"sharpe_ratio": 2.15, "max_drawdown_pct": 8.4},
        )

        # Tamper with Sharpe ratio
        manifest.metrics_summary["sharpe_ratio"] = 4.5

        self.assertFalse(self.engine.verify_manifest(manifest))


if __name__ == "__main__":
    unittest.main()
