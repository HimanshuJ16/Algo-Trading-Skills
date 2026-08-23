import logging
import unittest

from data_pipeline_schema_contract_testing import (
    DataSchemaContractVerifier, SchemaContract, FieldSpec, SchemaContractValidationReport
)

# Violations are logged at WARNING by design; keep expected-failure tests quiet.
logging.getLogger("data_pipeline_schema_contract_testing").setLevel(logging.CRITICAL)


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

    # ------------------------------------------------------------------
    # Non-finite values (regression: NaN/Inf previously passed validation)
    # ------------------------------------------------------------------

    def test_nan_and_infinite_prices_are_quarantined(self):
        batch = [
            {"symbol": "A", "price": float("nan"), "volume": 1},
            {"symbol": "B", "price": float("inf"), "volume": 1},
            {"symbol": "C", "price": float("-inf"), "volume": 1},
        ]
        report = self.verifier.validate_batch(batch)

        self.assertEqual(report.valid_records_count, 0)
        self.assertEqual(report.quarantined_records_count, 3)
        for q in report.quarantined_records:
            self.assertIn("Non-finite value for field 'price'", q.violation_reason)

    def test_non_finite_allowed_when_field_opts_in(self):
        contract = SchemaContract(
            "SentinelContract", "v1.0",
            [FieldSpec("spread", float, allow_non_finite=True)],
        )
        report = DataSchemaContractVerifier(contract).validate_batch(
            [{"spread": float("inf")}]
        )
        self.assertEqual(report.valid_records_count, 1)
        self.assertTrue(report.is_batch_valid)

    # ------------------------------------------------------------------
    # bool is a subclass of int (regression: True/False passed numeric fields)
    # ------------------------------------------------------------------

    def test_boolean_values_rejected_for_numeric_fields(self):
        report = self.verifier.validate_batch([{"symbol": "A", "price": True, "volume": False}])

        self.assertEqual(report.valid_records_count, 0)
        self.assertEqual(report.quarantined_records_count, 1)
        reason = report.quarantined_records[0].violation_reason
        self.assertIn("expected float, got bool", reason)
        self.assertIn("expected int, got bool", reason)

    def test_boolean_accepted_when_contract_declares_bool(self):
        contract = SchemaContract("FlagContract", "v1.0", [FieldSpec("is_auction", bool)])
        report = DataSchemaContractVerifier(contract).validate_batch([{"is_auction": True}])
        self.assertEqual(report.valid_records_count, 1)

    # ------------------------------------------------------------------
    # Batch null ceiling (regression: max_allowed_null_pct was never enforced)
    # ------------------------------------------------------------------

    def test_null_ceiling_breach_invalidates_batch(self):
        contract = SchemaContract(
            "QuoteContract", "v1.0",
            [FieldSpec("bid", float, is_nullable=True, min_value=0.0)],
            max_allowed_null_pct=0.5,
        )
        # 3 nulls out of 100 records => 3.00% null rate, independently derived.
        batch = [{"bid": None}] * 3 + [{"bid": 10.0}] * 97
        report = DataSchemaContractVerifier(contract).validate_batch(batch)

        self.assertEqual(report.valid_records_count, 100)
        self.assertEqual(report.quarantined_records_count, 0)
        self.assertEqual(report.null_pct_by_field["bid"], 3.0)
        self.assertEqual(report.null_breach_fields, ["bid"])
        self.assertFalse(report.is_batch_valid)
        self.assertTrue(any("NULL CEILING BREACH" in a for a in report.alerts))

    def test_null_rate_exactly_at_ceiling_is_not_a_breach(self):
        contract = SchemaContract(
            "QuoteContract", "v1.0",
            [FieldSpec("bid", float, is_nullable=True)],
            max_allowed_null_pct=5.0,
        )
        # 5 nulls out of 100 => exactly 5.00%; the ceiling is inclusive.
        batch = [{"bid": None}] * 5 + [{"bid": 10.0}] * 95
        report = DataSchemaContractVerifier(contract).validate_batch(batch)

        self.assertEqual(report.null_pct_by_field["bid"], 5.0)
        self.assertEqual(report.null_breach_fields, [])
        self.assertTrue(report.is_batch_valid)

    def test_non_nullable_nulls_are_quarantined_not_counted_as_null_rate(self):
        report = self.verifier.validate_batch([{"symbol": "A", "price": None, "volume": 1}])
        self.assertEqual(report.quarantined_records_count, 1)
        self.assertIn("non-nullable field 'price'", report.quarantined_records[0].violation_reason)
        self.assertEqual(report.null_pct_by_field, {})

    # ------------------------------------------------------------------
    # Malformed payloads must not abort the batch
    # ------------------------------------------------------------------

    def test_non_mapping_payload_is_quarantined_without_aborting_batch(self):
        batch = [
            {"symbol": "AAPL", "price": 180.5, "volume": 10},
            None,
            ["symbol", "price"],
            "price",
            {"symbol": "MSFT", "price": 400.0, "volume": 20},
        ]
        report = self.verifier.validate_batch(batch)

        self.assertEqual(report.total_records_processed, 5)
        self.assertEqual(report.valid_records_count, 2)
        self.assertEqual(report.quarantined_records_count, 3)
        self.assertIn("not a mapping", report.quarantined_records[0].violation_reason)

    # ------------------------------------------------------------------
    # DLQ forensic integrity
    # ------------------------------------------------------------------

    def test_dlq_payload_snapshot_is_not_aliased_to_caller_record(self):
        record = {"symbol": "AAPL", "price": -5.0, "volume": 10}
        report = self.verifier.validate_batch([record])
        record["price"] = 999.0  # caller mutates the record after validation

        self.assertEqual(report.quarantined_records[0].raw_payload["price"], -5.0)

    # ------------------------------------------------------------------
    # Multi-violation reporting
    # ------------------------------------------------------------------

    def test_all_violations_in_a_record_are_reported(self):
        report = self.verifier.validate_batch([{"price": -1.0, "volume": "many"}])

        violations = report.quarantined_records[0].violations
        self.assertEqual(len(violations), 3)
        joined = report.quarantined_records[0].violation_reason
        self.assertIn("Missing required field 'symbol'", joined)
        self.assertIn("min_value 0.01", joined)
        self.assertIn("expected int, got str", joined)

    # ------------------------------------------------------------------
    # Schema drift on undeclared fields
    # ------------------------------------------------------------------

    def test_unknown_fields_reported_as_drift_without_failing_by_default(self):
        report = self.verifier.validate_batch(
            [{"symbol": "AAPL", "price": 180.5, "volume": 10, "ask_volume": 5}]
        )
        self.assertEqual(report.valid_records_count, 1)
        self.assertTrue(report.is_batch_valid)
        self.assertEqual(report.observed_unknown_fields, ["ask_volume"])
        self.assertTrue(any("SCHEMA DRIFT" in a for a in report.alerts))

    def test_unknown_fields_quarantined_when_strict_mode_enabled(self):
        contract = SchemaContract(
            "TickRecordContract", "v1.0",
            [FieldSpec("symbol", str), FieldSpec("price", float, min_value=0.01)],
            forbid_unknown_fields=True,
        )
        report = DataSchemaContractVerifier(contract).validate_batch(
            [{"symbol": "AAPL", "price": 180.5, "ask_volume": 5}]
        )
        self.assertEqual(report.valid_records_count, 0)
        self.assertIn("Undeclared field(s) present", report.quarantined_records[0].violation_reason)

    # ------------------------------------------------------------------
    # Contract configuration validation
    # ------------------------------------------------------------------

    def test_misconfigured_contracts_are_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            DataSchemaContractVerifier(SchemaContract("Empty", "v1", []))

        with self.assertRaises(ValueError):
            DataSchemaContractVerifier(SchemaContract(
                "Dup", "v1", [FieldSpec("price", float), FieldSpec("price", int)]))

        with self.assertRaises(ValueError):
            DataSchemaContractVerifier(SchemaContract(
                "Bounds", "v1", [FieldSpec("price", float, min_value=100.0, max_value=1.0)]))

        with self.assertRaises(ValueError):
            DataSchemaContractVerifier(SchemaContract(
                "NullPct", "v1", [FieldSpec("price", float)], max_allowed_null_pct=150.0))

    # ------------------------------------------------------------------
    # Preserved behaviour
    # ------------------------------------------------------------------

    def test_integer_price_is_widened_to_float(self):
        report = self.verifier.validate_batch([{"symbol": "AAPL", "price": 180, "volume": 10}])
        self.assertEqual(report.valid_records_count, 1)

    def test_boundary_values_are_inclusive(self):
        contract = SchemaContract(
            "Bounded", "v1.0",
            [FieldSpec("price", float, min_value=0.01, max_value=100.0)],
        )
        report = DataSchemaContractVerifier(contract).validate_batch(
            [{"price": 0.01}, {"price": 100.0}, {"price": 100.01}]
        )
        self.assertEqual(report.valid_records_count, 2)
        self.assertEqual(report.quarantined_records_count, 1)

    def test_empty_batch_is_valid(self):
        report = self.verifier.validate_batch([])
        self.assertEqual(report.total_records_processed, 0)
        self.assertEqual(report.compliance_rate_pct, 100.0)
        self.assertTrue(report.is_batch_valid)

    def test_nullable_field_accepts_none(self):
        contract = SchemaContract(
            "QuoteContract", "v1.0",
            [FieldSpec("bid", float, is_nullable=True)],
            max_allowed_null_pct=100.0,
        )
        report = DataSchemaContractVerifier(contract).validate_batch([{"bid": None}])
        self.assertEqual(report.valid_records_count, 1)
        self.assertTrue(report.is_batch_valid)


if __name__ == '__main__':
    unittest.main()
