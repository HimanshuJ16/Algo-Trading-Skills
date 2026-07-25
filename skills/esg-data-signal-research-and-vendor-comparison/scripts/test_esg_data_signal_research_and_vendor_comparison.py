import unittest
from esg_data_signal_research_and_vendor_comparison import EsgDataSignalResearchAndVendorComparison, EsgDataSignalResearchAndVendorComparisonConfig

class TestEsgDataSignalResearchAndVendorComparison(unittest.TestCase):
    def test_init(self):
        config = EsgDataSignalResearchAndVendorComparisonConfig()
        obj = EsgDataSignalResearchAndVendorComparison(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = EsgDataSignalResearchAndVendorComparisonConfig()
        obj = EsgDataSignalResearchAndVendorComparison(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
