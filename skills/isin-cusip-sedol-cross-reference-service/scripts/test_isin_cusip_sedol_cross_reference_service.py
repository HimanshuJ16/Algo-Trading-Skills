import unittest
from isin_cusip_sedol_cross_reference_service import (
    IsinCusipSedolCrossReferenceEngine, SecurityMasterRecord, IdentifierCrossReferenceReport
)

class TestIsinCusipSedolCrossReferenceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_checksum_validations(self):
        # Valid Apple Inc Identifiers
        self.assertTrue(self.engine.validate_isin_checksum("US0378331005"))
        self.assertTrue(self.engine.validate_cusip_checksum("037833100"))
        self.assertTrue(self.engine.validate_sedol_checksum("2046251"))

        # Invalid Checksums
        self.assertFalse(self.engine.validate_isin_checksum("US0378331009"))
        self.assertFalse(self.engine.validate_cusip_checksum("037833109"))
        self.assertFalse(self.engine.validate_sedol_checksum("2046259"))

    def test_cross_reference_lookup_success(self):
        # Lookup by ISIN -> Resolves AAPL record
        report = self.engine.lookup_identifier("US0378331005")

        self.assertEqual(report.status, "MATCH_FOUND")
        self.assertEqual(report.query_type, "ISIN")
        self.assertTrue(report.is_checksum_valid)
        self.assertIsNotNone(report.matched_record)
        self.assertEqual(report.matched_record.ticker_symbol, "AAPL")
        self.assertEqual(report.matched_record.figi, "BBG000B9XRY4")

    def test_invalid_checksum_rejection(self):
        # Corrupted ISIN -> INVALID_CHECKSUM!
        report = self.engine.lookup_identifier("US0378331009")

        self.assertEqual(report.status, "INVALID_CHECKSUM")
        self.assertFalse(report.is_checksum_valid)
        self.assertIsNone(report.matched_record)

if __name__ == '__main__':
    unittest.main()
