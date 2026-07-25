import unittest
from reproducible_build_pinner import ReproducibleBuildPinner, Config

class TestReproducibleBuildPinner(unittest.TestCase):
    def test_init(self):
        engine = ReproducibleBuildPinner(Config("test"))
        self.assertEqual(engine.config.name, "test")

    def test_execute(self):
        engine = ReproducibleBuildPinner(Config("test"))
        self.assertTrue(engine.execute())

if __name__ == '__main__':
    unittest.main()
