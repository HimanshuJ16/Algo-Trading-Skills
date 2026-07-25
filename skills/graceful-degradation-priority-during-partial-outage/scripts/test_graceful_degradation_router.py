import unittest
from graceful_degradation_router import GracefulDegradationRouter, Config

class TestGracefulDegradationRouter(unittest.TestCase):
    def test_init(self):
        obj = GracefulDegradationRouter(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = GracefulDegradationRouter()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
