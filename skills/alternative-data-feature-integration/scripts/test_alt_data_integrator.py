import unittest
from alt_data_integrator import Config, AltDataIntegrator

class TestAltDataIntegrator(unittest.TestCase):
    def test_init(self):
        obj = AltDataIntegrator(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = AltDataIntegrator(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
