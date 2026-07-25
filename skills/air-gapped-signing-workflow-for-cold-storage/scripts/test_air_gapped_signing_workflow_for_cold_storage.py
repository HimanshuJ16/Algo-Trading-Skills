import unittest
from air_gapped_signing_workflow_for_cold_storage import AirGappedSigningWorkflowForColdStorageConfig, AirGappedSigningWorkflowForColdStorageEngine

class TestAirGappedSigningWorkflowForColdStorage(unittest.TestCase):
    def test_execute_true(self):
        engine = AirGappedSigningWorkflowForColdStorageEngine(AirGappedSigningWorkflowForColdStorageConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = AirGappedSigningWorkflowForColdStorageEngine(AirGappedSigningWorkflowForColdStorageConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
