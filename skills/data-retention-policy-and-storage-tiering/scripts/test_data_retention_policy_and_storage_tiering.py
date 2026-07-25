
import unittest
from data_retention_policy_and_storage_tiering import DataRetentionPolicyAndStorageTieringEngine, DataRetentionPolicyAndStorageTieringConfig

class TestDataRetentionPolicyAndStorageTiering(unittest.TestCase):
    def setUp(self):
        self.engine = DataRetentionPolicyAndStorageTieringEngine(DataRetentionPolicyAndStorageTieringConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = DataRetentionPolicyAndStorageTieringEngine(DataRetentionPolicyAndStorageTieringConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
