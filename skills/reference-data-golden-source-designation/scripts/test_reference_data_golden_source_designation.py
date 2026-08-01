import unittest
from reference_data_golden_source_designation import (
    Config, Engine, GoldenSourceDesignationEngine, GoldenSourceConfig,
    VendorFieldData, GoldenRecordReport
)

class TestEngine(unittest.TestCase):
    def test_legacy_init(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.config.name, "test")

    def test_legacy_process(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.process(1), 1)

class TestGoldenSourceDesignationEngine(unittest.TestCase):

    def setUp(self):
        self.config = GoldenSourceConfig(priority_rules={
            "isin": ["Bloomberg", "Refinitiv"],
            "tick_size": ["Exchange", "Bloomberg", "Refinitiv"],
            "currency": ["Bloomberg", "Refinitiv"],
        })
        self.engine = GoldenSourceDesignationEngine(self.config)

    def test_bloomberg_golden_for_isin(self):
        vendor_data = [
            VendorFieldData("Bloomberg", {"isin": "US0378331005", "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005_OLD", "currency": "USD"}),
        ]
        report = self.engine.resolve_golden_record("AAPL", vendor_data)
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        isin_res = [r for r in report.resolutions if r.field_name == "isin"][0]
        self.assertEqual(isin_res.golden_vendor, "Bloomberg")
        self.assertTrue(isin_res.has_conflict)

    def test_fallback_to_secondary_when_primary_null(self):
        vendor_data = [
            VendorFieldData("Bloomberg", {"isin": None, "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005", "currency": "USD"}),
        ]
        report = self.engine.resolve_golden_record("AAPL", vendor_data)
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        isin_res = [r for r in report.resolutions if r.field_name == "isin"][0]
        self.assertEqual(isin_res.golden_vendor, "Refinitiv")

    def test_no_conflicts_resolved_status(self):
        vendor_data = [
            VendorFieldData("Bloomberg", {"isin": "US0378331005", "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005", "currency": "USD"}),
        ]
        report = self.engine.resolve_golden_record("AAPL", vendor_data)
        self.assertEqual(report.status, "RESOLVED")
        self.assertEqual(report.conflicts_detected, 0)

    def test_conflicts_found_status(self):
        vendor_data = [
            VendorFieldData("Bloomberg", {"isin": "US0378331005"}),
            VendorFieldData("Refinitiv", {"isin": "DIFFERENT_ISIN"}),
        ]
        report = self.engine.resolve_golden_record("AAPL", vendor_data)
        self.assertEqual(report.status, "CONFLICTS_FOUND")
        self.assertEqual(report.conflicts_detected, 1)

if __name__ == '__main__':
    unittest.main()
