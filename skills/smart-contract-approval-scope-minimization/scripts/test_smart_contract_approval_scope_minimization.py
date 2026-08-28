import unittest

from smart_contract_approval_scope_minimization import (
    ApprovalType,
    MAX_UINT160,
    MAX_UINT256,
    SmartContractApprovalScopeMinimizationConfig,
    SmartContractApprovalScopeMinimizationEngine,
    TokenAllowance,
    UnlimitedApprovalBlocked,
)

# Real mainnet addresses, used only as well-formed fixtures.
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
UNISWAP_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

FROZEN_NOW = 1_700_000_000.0


def frozen_clock():
    return FROZEN_NOW


class TestSmartContractApprovalScopeLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = SmartContractApprovalScopeMinimizationEngine(
            SmartContractApprovalScopeMinimizationConfig(enabled=True)
        )
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SmartContractApprovalScopeMinimizationEngine(
            SmartContractApprovalScopeMinimizationConfig(enabled=False)
        )
        self.assertFalse(engine.execute())

    def test_disabled_engine_refuses_to_plan(self):
        engine = SmartContractApprovalScopeMinimizationEngine(
            SmartContractApprovalScopeMinimizationConfig(enabled=False)
        )
        with self.assertRaises(RuntimeError):
            engine.plan_approval(USDC, UNISWAP_ROUTER, 100_000000)


class TestExactAmountPlanning(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine(clock=frozen_clock)

    def test_plan_exact_amount_approval(self):
        plan = self.engine.plan_approval(
            token_address=USDC,
            spender_address=UNISWAP_ROUTER,
            required_amount=100_000000,  # $100 USDC, 6 decimals
            supports_eip2612_permit=False,
        )
        self.assertEqual(plan.recommended_approval_amount, 100_000000)
        self.assertEqual(plan.approval_type, ApprovalType.EXACT_AMOUNT)
        self.assertFalse(plan.requires_reset_to_zero_first)
        self.assertTrue(plan.approval_transaction_needed)
        self.assertIsNone(plan.permit_deadline_unix)

    def test_zero_reset_required_when_changing_a_non_zero_allowance(self):
        """EIP-20: a non-zero -> non-zero change is front-runnable; reset to 0 first."""
        self.engine.record_allowance(
            TokenAllowance(USDT, UNISWAP_ROUTER, current_allowance=50_000000)
        )
        plan = self.engine.plan_approval(USDT, UNISWAP_ROUTER, 100_000000)
        self.assertTrue(plan.requires_reset_to_zero_first)
        self.assertTrue(plan.approval_transaction_needed)

    def test_explicit_current_allowance_overrides_recorded_value(self):
        self.engine.record_allowance(
            TokenAllowance(USDT, UNISWAP_ROUTER, current_allowance=50_000000)
        )
        plan = self.engine.plan_approval(
            USDT, UNISWAP_ROUTER, 100_000000, current_allowance=0
        )
        self.assertFalse(plan.requires_reset_to_zero_first)

    def test_no_zero_reset_when_current_allowance_is_zero(self):
        plan = self.engine.plan_approval(
            USDC, UNISWAP_ROUTER, 100_000000, current_allowance=0
        )
        self.assertFalse(plan.requires_reset_to_zero_first)

    def test_no_zero_reset_when_revoking_to_zero(self):
        """approve(0) from a non-zero allowance needs no preparatory reset."""
        plan = self.engine.plan_approval(
            USDC, UNISWAP_ROUTER, 0, current_allowance=100_000000
        )
        self.assertEqual(plan.recommended_approval_amount, 0)
        self.assertFalse(plan.requires_reset_to_zero_first)
        self.assertTrue(plan.approval_transaction_needed)

    def test_no_transaction_needed_when_allowance_already_exact(self):
        plan = self.engine.plan_approval(
            USDC, UNISWAP_ROUTER, 100_000000, current_allowance=100_000000
        )
        self.assertFalse(plan.approval_transaction_needed)
        self.assertFalse(plan.requires_reset_to_zero_first)


class TestUnlimitedApprovalBlock(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine(clock=frozen_clock)

    def test_uint256_max_is_blocked(self):
        with self.assertRaises(UnlimitedApprovalBlocked):
            self.engine.plan_approval(USDC, UNISWAP_ROUTER, MAX_UINT256)

    def test_permit2_uint160_max_sentinel_is_blocked(self):
        """Permit2 amounts are uint160; type(uint160).max is its unlimited sentinel."""
        with self.assertRaises(UnlimitedApprovalBlocked):
            self.engine.plan_approval(USDC, PERMIT2, MAX_UINT160)

    def test_unlimited_block_applies_to_the_permit_path_too(self):
        with self.assertRaises(UnlimitedApprovalBlocked):
            self.engine.plan_approval(
                DAI, UNISWAP_ROUTER, MAX_UINT256, supports_eip2612_permit=True
            )

    def test_amount_just_below_threshold_is_allowed(self):
        plan = self.engine.plan_approval(USDC, UNISWAP_ROUTER, (1 << 200) - 1)
        self.assertEqual(plan.approval_type, ApprovalType.EXACT_AMOUNT)

    def test_classification_is_available_without_raising(self):
        self.assertEqual(
            self.engine.classify_requested_amount(MAX_UINT256),
            ApprovalType.UNLIMITED_BLOCKED,
        )
        self.assertEqual(
            self.engine.classify_requested_amount(MAX_UINT160),
            ApprovalType.UNLIMITED_BLOCKED,
        )
        self.assertEqual(
            self.engine.classify_requested_amount(100_000000),
            ApprovalType.EXACT_AMOUNT,
        )


class TestPermitPlanning(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine(clock=frozen_clock)

    def test_plan_eip_2612_permit(self):
        plan = self.engine.plan_approval(
            token_address=DAI,
            spender_address=UNISWAP_ROUTER,
            required_amount=500 * 10**18,  # 500 DAI, 18 decimals
            supports_eip2612_permit=True,
            permit_validity_seconds=600.0,
        )
        self.assertEqual(plan.approval_type, ApprovalType.EIP_2612_PERMIT)
        # ERC-2612 deadline is a uint256 second count compared to block.timestamp.
        self.assertIsInstance(plan.permit_deadline_unix, int)
        self.assertEqual(plan.permit_deadline_unix, int(FROZEN_NOW) + 600)
        self.assertFalse(plan.requires_reset_to_zero_first)

    def test_permit_path_ignores_existing_allowance_for_zero_reset(self):
        self.engine.record_allowance(
            TokenAllowance(DAI, UNISWAP_ROUTER, current_allowance=10**18)
        )
        plan = self.engine.plan_approval(
            DAI, UNISWAP_ROUTER, 2 * 10**18, supports_eip2612_permit=True
        )
        self.assertFalse(plan.requires_reset_to_zero_first)

    def test_permit_validity_above_policy_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.plan_approval(
                DAI, UNISWAP_ROUTER, 10**18,
                supports_eip2612_permit=True,
                permit_validity_seconds=3600.0,
            )

    def test_non_positive_permit_validity_is_rejected(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                self.engine.plan_approval(
                    DAI, UNISWAP_ROUTER, 10**18,
                    supports_eip2612_permit=True,
                    permit_validity_seconds=bad,
                )

    def test_sub_second_validity_rounds_the_deadline_up(self):
        """int() truncation would yield an already-expired deadline."""
        plan = self.engine.plan_approval(
            DAI, UNISWAP_ROUTER, 10**18,
            supports_eip2612_permit=True,
            permit_validity_seconds=0.5,
        )
        self.assertEqual(plan.permit_deadline_unix, int(FROZEN_NOW) + 1)

    def test_policy_cap_is_configurable(self):
        engine = SmartContractApprovalScopeMinimizationEngine(
            SmartContractApprovalScopeMinimizationConfig(max_permit_validity_seconds=60.0),
            clock=frozen_clock,
        )
        with self.assertRaises(ValueError):
            engine.plan_approval(
                DAI, UNISWAP_ROUTER, 10**18,
                supports_eip2612_permit=True,
                permit_validity_seconds=300.0,
            )


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine(clock=frozen_clock)

    def test_malformed_addresses_are_rejected(self):
        for bad in ("0xSpender1", "", "0x", USDC[:-1], "not-an-address", None):
            with self.assertRaises(ValueError):
                self.engine.plan_approval(USDC, bad, 1000)

    def test_zero_address_spender_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.plan_approval(USDC, "0x" + "0" * 40, 1000)

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.plan_approval(USDC, UNISWAP_ROUTER, -1)

    def test_amount_above_uint256_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.plan_approval(USDC, UNISWAP_ROUTER, MAX_UINT256 + 1)

    def test_non_integer_amount_is_rejected(self):
        for bad in (100.5, "1000", True):
            with self.assertRaises(TypeError):
                self.engine.plan_approval(USDC, UNISWAP_ROUTER, bad)

    def test_degenerate_config_is_rejected(self):
        """threshold=0 would classify every amount, including 0, as unlimited."""
        with self.assertRaises(ValueError):
            SmartContractApprovalScopeMinimizationConfig(unlimited_allowance_threshold=0)
        with self.assertRaises(ValueError):
            SmartContractApprovalScopeMinimizationConfig(max_permit_validity_seconds=0.0)

    def test_negative_recorded_allowance_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.record_allowance(
                TokenAllowance(USDC, UNISWAP_ROUTER, current_allowance=-5)
            )


class TestAllowanceAudit(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractApprovalScopeMinimizationEngine(clock=frozen_clock)

    def test_revoke_unlimited_allowances(self):
        allowances = [
            TokenAllowance(USDC, UNISWAP_ROUTER, current_allowance=1000),
            TokenAllowance(DAI, PERMIT2, current_allowance=MAX_UINT256, is_unlimited=True),
        ]
        revocations = self.engine.audit_and_revoke_unlimited_allowances(allowances)

        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations[0].spender_address, PERMIT2)
        self.assertEqual(revocations[0].recommended_approval_amount, 0)
        self.assertFalse(revocations[0].requires_reset_to_zero_first)

    def test_permit2_unlimited_sentinel_is_detected(self):
        """Regression: uint160.max is ~1.46e48, far below the old 2**200 threshold."""
        allowances = [TokenAllowance(USDC, PERMIT2, current_allowance=MAX_UINT160)]
        revocations = self.engine.audit_and_revoke_unlimited_allowances(allowances)
        self.assertEqual(len(revocations), 1)

    def test_zero_allowances_are_never_planned(self):
        allowances = [
            TokenAllowance(USDC, UNISWAP_ROUTER, current_allowance=0, is_unlimited=True),
        ]
        self.assertEqual(self.engine.audit_allowances(allowances), [])

    def test_stale_allowance_is_revoked_when_a_window_is_given(self):
        allowances = [
            TokenAllowance(
                USDC, UNISWAP_ROUTER, current_allowance=1000,
                last_used_unix=FROZEN_NOW - 90 * 86400,
            ),
            TokenAllowance(
                DAI, UNISWAP_ROUTER, current_allowance=1000,
                last_used_unix=FROZEN_NOW - 3600,
            ),
        ]
        revocations = self.engine.audit_allowances(allowances, stale_after_seconds=30 * 86400)
        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations[0].token_address, USDC)
        self.assertIn("stale allowance", revocations[0].audit_notes)

    def test_staleness_is_ignored_when_no_window_is_given(self):
        allowances = [
            TokenAllowance(
                USDC, UNISWAP_ROUTER, current_allowance=1000,
                last_used_unix=FROZEN_NOW - 900 * 86400,
            ),
        ]
        self.assertEqual(self.engine.audit_allowances(allowances), [])

    def test_unknown_last_used_is_not_treated_as_stale(self):
        allowances = [TokenAllowance(USDC, UNISWAP_ROUTER, current_allowance=1000)]
        revocations = self.engine.audit_allowances(allowances, stale_after_seconds=86400)
        self.assertEqual(revocations, [])

    def test_unlimited_allowance_is_revoked_even_without_a_stale_window(self):
        allowances = [
            TokenAllowance(
                USDC, UNISWAP_ROUTER, current_allowance=MAX_UINT256,
                last_used_unix=FROZEN_NOW,
            ),
        ]
        revocations = self.engine.audit_allowances(allowances)
        self.assertEqual(len(revocations), 1)
        self.assertIn("unlimited allowance", revocations[0].audit_notes)

    def test_non_positive_stale_window_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_allowances([], stale_after_seconds=0)

    def test_malformed_address_in_inventory_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_allowances(
                [TokenAllowance("0xA0b8...", "0xSpender1", current_allowance=MAX_UINT256)]
            )


if __name__ == "__main__":
    unittest.main()
