import unittest
from reference_data_symbol_mapping_across_vendors import (
    Config, Engine, SymbolMappingEngine, SymbolMappingConfig,
    VendorSymbolEntry, SymbolMappingCoverageReport
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

class TestSymbolMappingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SymbolMappingEngine()
        self.engine.register_mapping(VendorSymbolEntry(
            canonical_symbol="AAPL", vendor_name="Bloomberg",
            vendor_symbol="AAPL US Equity", identifier_type="BBG"
        ))
        self.engine.register_mapping(VendorSymbolEntry(
            canonical_symbol="AAPL", vendor_name="Reuters",
            vendor_symbol="AAPL.O", identifier_type="RIC"
        ))
        self.engine.register_mapping(VendorSymbolEntry(
            canonical_symbol="AAPL", vendor_name="ISIN_DB",
            vendor_symbol="US0378331005", identifier_type="ISIN"
        ))

    def test_forward_lookup_reuters(self):
        result = self.engine.forward_lookup("Reuters", "AAPL.O")
        self.assertEqual(result, "AAPL")

    def test_forward_lookup_bloomberg(self):
        result = self.engine.forward_lookup("Bloomberg", "AAPL US Equity")
        self.assertEqual(result, "AAPL")

    def test_reverse_lookup_bloomberg(self):
        result = self.engine.reverse_lookup("AAPL", "Bloomberg")
        self.assertEqual(result, "AAPL US EQUITY")

    def test_forward_lookup_miss_returns_none(self):
        result = self.engine.forward_lookup("Reuters", "UNKNOWN.X")
        self.assertIsNone(result)

    def test_case_insensitive_lookup(self):
        result = self.engine.forward_lookup("reuters", "aapl.o")
        self.assertEqual(result, "AAPL")

    def test_coverage_report_full(self):
        report = self.engine.get_coverage_report()
        self.assertEqual(report.status, "FULL_COVERAGE")
        self.assertEqual(report.total_canonical_symbols, 1)
        self.assertEqual(report.total_mappings, 3)

if __name__ == '__main__':
    unittest.main()
