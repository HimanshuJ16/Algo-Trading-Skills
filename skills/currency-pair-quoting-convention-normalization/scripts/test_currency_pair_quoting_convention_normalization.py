
import unittest
from currency_pair_quoting_convention_normalization import CurrencyPairQuotingConventionNormalizationEngine, CurrencyPairQuotingConventionNormalizationConfig

class TestCurrencyPairQuotingConventionNormalization(unittest.TestCase):
    def setUp(self):
        self.engine = CurrencyPairQuotingConventionNormalizationEngine(CurrencyPairQuotingConventionNormalizationConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = CurrencyPairQuotingConventionNormalizationEngine(CurrencyPairQuotingConventionNormalizationConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
