import unittest
from smart_contract_approval_scope_minimization import (
    SmartContractApprovalScopeMinimizationConfig,
    SmartContractApprovalScopeMinimizationEngine,
    ApprovalType, TokenAllowance, ApprovalTransactionPlan, MAX_UINT256
)

class TestSmartContractApprovalScopeLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = SmartContractApprovalScopeMinimizationEngine(SmartContractApprovalScopeMinimizationConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SmartContractApprovalScopeMinimizationEngine(SmartContractApprovalScopeMinimizationConfig(enabled=False))
        self.assertFalse(engine.execute())

class TestSmartContractApprovalScopeEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine()

    def test_plan_exact_amount_approval(self):
        plan = self.engine.plan_approval(
            token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
            spender_address="0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",# Uniswap Router
            required_amount=100000000,                                 # $100 USDC
            supports_eip2612_permit=False
        )
        self.assertEqual(plan.recommended_approval_amount, 100000000)
        self.assertEqual(plan.approval_type, ApprovalType.EXACT_AMOUNT)
        self.assertFalse(plan.requires_reset_to_zero_first)

    def test_plan_eip_2612_permit(self):
        plan = self.engine.plan_approval(
            token_address="0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
            spender_address="0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            required_amount=500000000000000000000,                      # 500 DAI
            supports_eip2612_permit=True,
            permit_validity_seconds=600.0
        )
        self.assertEqual(plan.approval_type, ApprovalType.EIP_2612_PERMIT)
        self.assertIsNotNone(plan.permit_deadline_unix)

    def test_revoke_unlimited_allowances(self):
        allowances = [
            TokenAllowance("0xA0b8...", "0xSpender1", current_allowance=1000),      # Normal
            TokenAllowance("0x6B17...", "0xSpender2", current_allowance=MAX_UINT256, is_unlimited=True) # DANGEROUS!
        ]
        revocations = self.engine.audit_and_revoke_unlimited_allowances(allowances)

        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations[0].spender_address, "0xSpender2")
        self.assertEqual(revocations[0].recommended_approval_amount, 0)

if __name__ == '__main__':
    unittest.main()
