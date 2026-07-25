
import unittest
from data_pipeline_schema_contract_testing import DataPipelineSchemaContractTestingEngine, DataPipelineSchemaContractTestingConfig

class TestDataPipelineSchemaContractTesting(unittest.TestCase):
    def setUp(self):
        self.engine = DataPipelineSchemaContractTestingEngine(DataPipelineSchemaContractTestingConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = DataPipelineSchemaContractTestingEngine(DataPipelineSchemaContractTestingConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
