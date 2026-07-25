import unittest
from blameless_postmortem_generator import BlamelessPostmortemGenerator, Config

class TestBlamelessPostmortemGenerator(unittest.TestCase):
    def test_init(self):
        obj = BlamelessPostmortemGenerator(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = BlamelessPostmortemGenerator()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
