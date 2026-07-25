import unittest
from network_segmentation_auditor import NetworkSegmentationAuditor, Config

class TestNetworkSegmentationAuditor(unittest.TestCase):
    def test_init(self):
        engine = NetworkSegmentationAuditor(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = NetworkSegmentationAuditor(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
