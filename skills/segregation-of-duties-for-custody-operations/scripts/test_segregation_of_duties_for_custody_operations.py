import unittest
from segregation_of_duties_for_custody_operations import (
    SegregationOfDutiesForCustodyOperationsConfig,
    SegregationOfDutiesForCustodyOperationsEngine,
    UserIdentity, CustodyRole, SoDConflictError,
    CustodyTransferProposal
)

class TestSegregationOfDutiesLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(SegregationOfDutiesForCustodyOperationsConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(SegregationOfDutiesForCustodyOperationsConfig(enabled=False))
        self.assertFalse(engine.execute())

class TestSegregationOfDutiesEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SegregationOfDutiesForCustodyOperationsEngine(large_transfer_threshold_usd=50000.0)
        # Register users
        self.maker = UserIdentity("USR_MAKER_1", "alice", "Trading", {CustodyRole.INITIATOR})
        self.checker1 = UserIdentity("USR_CHECKER_1", "bob", "Risk", {CustodyRole.APPROVER})
        self.checker2 = UserIdentity("USR_CHECKER_2", "charlie", "Compliance", {CustodyRole.APPROVER})
        self.admin = UserIdentity("USR_ADMIN_1", "dave", "Security", {CustodyRole.SECURITY_ADMIN})

        self.engine.register_user(self.maker)
        self.engine.register_user(self.checker1)
        self.engine.register_user(self.checker2)
        self.engine.register_user(self.admin)

    def test_successful_dual_control_approval(self):
        # Proposal for $100,000 (Requires 2 approvals)
        prop = self.engine.propose_transfer("PROP_001", "USR_MAKER_1", "0x123...", "BTC", 100000.0)
        self.assertEqual(prop.required_approvals, 2)
        self.assertEqual(prop.status, "PENDING")

        # 1st approval by Checker 1
        prop = self.engine.approve_transfer("PROP_001", "USR_CHECKER_1")
        self.assertEqual(len(prop.approvals), 1)
        self.assertEqual(prop.status, "PENDING")

        # 2nd approval by Checker 2
        prop = self.engine.approve_transfer("PROP_001", "USR_CHECKER_2")
        self.assertEqual(len(prop.approvals), 2)
        self.assertEqual(prop.status, "APPROVED")

    def test_maker_checker_self_approval_blocked(self):
        # Maker attempts to approve their own transfer proposal -> SoDConflictError
        prop = self.engine.propose_transfer("PROP_002", "USR_MAKER_1", "0x123...", "ETH", 10000.0)

        # Give maker both roles for testing self approval attempt
        self.maker.roles.add(CustodyRole.APPROVER)

        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.approve_transfer("PROP_002", "USR_MAKER_1")
        self.assertIn("cannot approve their own transfer", str(ctx.exception))

    def test_invalid_role_combination_on_registration(self):
        # Admin trying to also be Maker (Initiator) -> SoDConflictError
        bad_user = UserIdentity("USR_BAD", "eve", "Security", {CustodyRole.SECURITY_ADMIN, CustodyRole.INITIATOR})
        with self.assertRaises(SoDConflictError):
            self.engine.register_user(bad_user)

if __name__ == '__main__':
    unittest.main()
