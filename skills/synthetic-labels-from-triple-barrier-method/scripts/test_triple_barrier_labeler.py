import unittest
from triple_barrier_labeler import Config, TripleBarrierLabeler

class TestTripleBarrierLabeler(unittest.TestCase):
    def test_init(self):
        obj = TripleBarrierLabeler(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = TripleBarrierLabeler(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
