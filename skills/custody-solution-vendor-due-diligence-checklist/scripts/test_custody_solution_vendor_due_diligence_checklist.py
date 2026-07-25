import unittest
from custody_solution_vendor_due_diligence_checklist import CustodySolutionVendorDueDiligenceChecklistConfig, CustodySolutionVendorDueDiligenceChecklistEngine

class TestCustodySolutionVendorDueDiligenceChecklist(unittest.TestCase):
    def test_execute_true(self):
        engine = CustodySolutionVendorDueDiligenceChecklistEngine(CustodySolutionVendorDueDiligenceChecklistConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = CustodySolutionVendorDueDiligenceChecklistEngine(CustodySolutionVendorDueDiligenceChecklistConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
