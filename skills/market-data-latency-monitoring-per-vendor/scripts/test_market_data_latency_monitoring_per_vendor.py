
import unittest
from market_data_latency_monitoring_per_vendor import MarketDataLatencyMonitoringPerVendorEngine, MarketDataLatencyMonitoringPerVendorConfig

class TestMarketDataLatencyMonitoringPerVendor(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataLatencyMonitoringPerVendorEngine(MarketDataLatencyMonitoringPerVendorConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = MarketDataLatencyMonitoringPerVendorEngine(MarketDataLatencyMonitoringPerVendorConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
