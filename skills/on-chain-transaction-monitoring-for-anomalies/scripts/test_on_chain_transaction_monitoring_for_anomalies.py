import unittest
from on_chain_transaction_monitoring_for_anomalies import OnChainTransactionMonitoringForAnomaliesConfig, OnChainTransactionMonitoringForAnomaliesEngine

class TestOnChainTransactionMonitoringForAnomalies(unittest.TestCase):
    def test_execute_true(self):
        engine = OnChainTransactionMonitoringForAnomaliesEngine(OnChainTransactionMonitoringForAnomaliesConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = OnChainTransactionMonitoringForAnomaliesEngine(OnChainTransactionMonitoringForAnomaliesConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
