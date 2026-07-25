import unittest
from alternative_data_vendor_due_diligence_checklist import AlternativeDataVendorDueDiligenceChecklist, AlternativeDataVendorDueDiligenceChecklistConfig

class TestAlternativeDataVendorDueDiligenceChecklist(unittest.TestCase):
    def test_init(self):
        config = AlternativeDataVendorDueDiligenceChecklistConfig()
        obj = AlternativeDataVendorDueDiligenceChecklist(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = AlternativeDataVendorDueDiligenceChecklistConfig()
        obj = AlternativeDataVendorDueDiligenceChecklist(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
