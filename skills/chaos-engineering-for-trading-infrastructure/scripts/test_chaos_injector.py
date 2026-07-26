import unittest
import time
from chaos_injector import ChaosInjector, ChaosConfig, MockFixClient

class TestChaosInjector(unittest.TestCase):

    def setUp(self):
        self.client = MockFixClient()

    def test_latency_injection(self):
        # Inject exactly 50ms latency
        config = ChaosConfig(latency_ms=50, jitter_ms=0)
        injector = ChaosInjector(config)
        
        start_time = time.time()
        result = injector.execute(self.client.send_order, "ORD-1")
        end_time = time.time()
        
        duration_ms = (end_time - start_time) * 1000
        
        self.assertEqual(result, "ACK-ORD-1")
        # Ensure it took AT LEAST 50ms (allow some small buffer for execution time)
        self.assertGreaterEqual(duration_ms, 49.0)

    def test_drop_probability(self):
        # 100% drop probability
        config = ChaosConfig(drop_probability=1.0)
        injector = ChaosInjector(config)
        
        with self.assertRaises(ConnectionAbortedError):
            injector.execute(self.client.send_order, "ORD-2")

    def test_crash_probability(self):
        # 100% crash probability
        config = ChaosConfig(crash_probability=1.0)
        injector = ChaosInjector(config)
        
        with self.assertRaises(SystemExit):
            injector.execute(self.client.send_order, "ORD-3")

    def test_determinism_with_seed(self):
        # With seed 42, random.random() sequence is predictable.
        # First call is ~0.639
        config1 = ChaosConfig(drop_probability=0.5, seed=42)
        injector1 = ChaosInjector(config1)
        
        # 0.639 > 0.5, so it should NOT drop
        result = injector1.execute(self.client.send_order, "ORD-4")
        self.assertEqual(result, "ACK-ORD-4")
        
        # Reset seed and test again to prove determinism
        config2 = ChaosConfig(drop_probability=0.5, seed=42)
        injector2 = ChaosInjector(config2)
        result2 = injector2.execute(self.client.send_order, "ORD-4")
        self.assertEqual(result2, "ACK-ORD-4")

if __name__ == '__main__':
    unittest.main()
