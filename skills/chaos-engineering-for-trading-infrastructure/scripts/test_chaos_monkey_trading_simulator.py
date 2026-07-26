import unittest
import time
from chaos_monkey_trading_simulator import ChaosInjector, ChaosConfig, MockFixClient

class TestChaosInjector(unittest.TestCase):

    def setUp(self):
        self.client = MockFixClient()

    def test_latency_injection(self):
        config = ChaosConfig(latency_ms=50, jitter_ms=0)
        injector = ChaosInjector(config)
        
        start_time = time.time()
        result = injector.execute(self.client.send_order, "ORD-1")
        end_time = time.time()
        
        duration_ms = (end_time - start_time) * 1000
        self.assertEqual(result, "ACK-ORD-1")
        self.assertGreaterEqual(duration_ms, 49.0)

    def test_drop_probability(self):
        config = ChaosConfig(drop_probability=1.0)
        injector = ChaosInjector(config)
        
        with self.assertRaises(ConnectionAbortedError):
            injector.execute(self.client.send_order, "ORD-2")

    def test_crash_probability(self):
        config = ChaosConfig(crash_probability=1.0)
        injector = ChaosInjector(config)
        
        with self.assertRaises(SystemExit):
            injector.execute(self.client.send_order, "ORD-3")

    def test_determinism_with_seed(self):
        config1 = ChaosConfig(drop_probability=0.5, seed=42)
        injector1 = ChaosInjector(config1)
        
        result = injector1.execute(self.client.send_order, "ORD-4")
        self.assertEqual(result, "ACK-ORD-4")
        
        config2 = ChaosConfig(drop_probability=0.5, seed=42)
        injector2 = ChaosInjector(config2)
        result2 = injector2.execute(self.client.send_order, "ORD-4")
        self.assertEqual(result2, "ACK-ORD-4")

if __name__ == '__main__':
    unittest.main()
