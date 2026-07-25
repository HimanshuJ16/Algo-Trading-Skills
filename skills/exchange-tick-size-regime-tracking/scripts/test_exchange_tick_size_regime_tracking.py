
import unittest
from exchange_tick_size_regime_tracking import ExchangeTickSizeRegimeTrackingEngine, ExchangeTickSizeRegimeTrackingConfig

class TestExchangeTickSizeRegimeTracking(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeTrackingEngine(ExchangeTickSizeRegimeTrackingConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = ExchangeTickSizeRegimeTrackingEngine(ExchangeTickSizeRegimeTrackingConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
