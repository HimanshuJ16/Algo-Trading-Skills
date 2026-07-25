import unittest
from ml_tca_backtester import MlTcaBacktester, MlTcaBacktesterConfig

class TestMlTcaBacktester(unittest.TestCase):
    def test_initialization(self):
        config = MlTcaBacktesterConfig()
        obj = MlTcaBacktester(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = MlTcaBacktesterConfig(parameter_1=2.0)
        obj = MlTcaBacktester(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
