import unittest
from data_pipeline_schema_contract_testing import (
    DataSchemaContractVerifier, SchemaContract, FieldSpec, SchemaContractValidationReport
)

class TestDataSchemaContractVerifier(unittest.TestCase):

    def setUp(self):
        # Contract for TickRecord
        self.contract = SchemaContract(
            contract_name="TickRecordContract",
            contract_version="v1.0",
            field_specs=[
                FieldSpec("symbol", str, is_nullable=False),
                FieldSpec("price", float, is_nullable=False, min_value=0.01),
                FieldSpec("volume", int, is_nullable=False, min_value=0)
            ]
        )
        self.verifier = DataSchemaContractVerifier(self.contract)

    def test_schema_contract_validates_and_quarantines_corrupt_records(self):
        batch = [
            {"symbol": "AAPL", "price": 180.50, "volume": 100},              # Valid
            {"symbol": "MSFT", "price": 400.25, "volume": 200},              # Valid
            {"symbol": "GOOGL", "price": 175.00},                            # Corrupt: Missing 'volume'
            {"symbol": "AMZN", "price": "invalid_string_price", "volume": 50},# Corrupt: Type mismatch
            {"symbol": "TSLA", "price": -10.0, "volume": 50}                  # Corrupt: Price < 0.01
        ]
        
        report = self.verifier.validate_batch(batch)
        
        self.assertEqual(report.total_records_processed, 5)
        self.assertEqual(report.valid_records_count, 2)
        self.assertEqual(report.quarantined_records_count, 3)
        self.assertFalse(report.is_batch_valid)
        self.assertEqual(report.compliance_rate_pct, 40.0)

if __name__ == '__main__':
    unittest.main()
