import unittest
from on_chain_transaction_monitoring_for_anomalies import (
    OnChainAnomalyMonitorEngine, OnChainTxPayload, OnChainRiskPolicy, OnChainMonitoringReport
)

class TestOnChainAnomalyMonitorEngine(unittest.TestCase):

    def setUp(self):
        policy = OnChainRiskPolicy(
            max_transfer_usd=50000.0,
            max_gas_gwei=200.0,
            sanctioned_addresses={"0x1111111111111111111111111111111111111111"},
            whitelisted_methods={"transfer(address,uint256)"}
        )
        self.engine = OnChainAnomalyMonitorEngine(policy)

    def test_safe_transaction_monitoring(self):
        tx = OnChainTxPayload(
            tx_hash="0xabc123",
            from_address="0xAAAA",
            to_address="0xBBBB",
            value_usd=1000.0,
            gas_price_gwei=30.0,
            method_signature="transfer(address,uint256)",
            block_number=18000000,
            timestamp_utc=1700000000.0
        )
        report = self.engine.audit_transaction(tx)
        self.assertEqual(report.status, "TRANSACTION_SAFE")
        self.assertEqual(report.risk_score, 0)
        self.assertFalse(report.is_blocked)

    def test_high_risk_sanctioned_and_gas_spike_block(self):
        # Transaction to sanctioned address + gas spike + unapproved method -> HIGH_RISK_BLOCK
        tx = OnChainTxPayload(
            tx_hash="0xbad999",
            from_address="0xAAAA",
            to_address="0x1111111111111111111111111111111111111111", # Sanctioned!
            value_usd=100000.0,                                   # High value spike!
            gas_price_gwei=500.0,                                  # Gas spike!
            method_signature="setApprovalForAll(address,bool)",    # Unapproved method!
            block_number=18000005,
            timestamp_utc=1700000100.0
        )
        report = self.engine.audit_transaction(tx)
        self.assertEqual(report.status, "HIGH_RISK_BLOCK")
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.risk_score, 100)
        self.assertEqual(len(report.risk_flags), 4)

if __name__ == '__main__':
    unittest.main()
