import unittest
from transfer_learning_bootstrap import Config, TransferLearningBootstrap

class TestTransferLearningBootstrap(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = TransferLearningBootstrap(cfg)
        self.assertEqual(obj.config.name, "transfer-learning-across-correlated-instruments")
        
    def test_process(self):
        cfg = Config()
        obj = TransferLearningBootstrap(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
