import unittest
from signal_adversarial_tester import SignalAdversarialTester, SignalAdversarialTesterConfig

class TestSignalAdversarialTester(unittest.TestCase):
    def test_initialization(self):
        config = SignalAdversarialTesterConfig()
        obj = SignalAdversarialTester(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = SignalAdversarialTesterConfig(parameter_1=2.0)
        obj = SignalAdversarialTester(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
