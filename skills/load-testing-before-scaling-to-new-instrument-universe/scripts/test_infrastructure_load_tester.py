import unittest
from infrastructure_load_tester import InfrastructureLoadTester, Config

class TestInfrastructureLoadTester(unittest.TestCase):
    def test_init(self):
        obj = InfrastructureLoadTester(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = InfrastructureLoadTester()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
