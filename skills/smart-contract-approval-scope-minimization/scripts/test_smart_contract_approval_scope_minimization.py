import unittest
from smart_contract_approval_scope_minimization import SmartContractApprovalScopeMinimizationConfig, SmartContractApprovalScopeMinimizationEngine

class TestSmartContractApprovalScopeMinimization(unittest.TestCase):
    def test_execute_true(self):
        engine = SmartContractApprovalScopeMinimizationEngine(SmartContractApprovalScopeMinimizationConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SmartContractApprovalScopeMinimizationEngine(SmartContractApprovalScopeMinimizationConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
