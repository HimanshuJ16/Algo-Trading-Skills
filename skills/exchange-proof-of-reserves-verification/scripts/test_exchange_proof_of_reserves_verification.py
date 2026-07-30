import unittest
from exchange_proof_of_reserves_verification import (
    ExchangeProofOfReservesEngine, UserAccountBalance, MerkleAuditPathNode, ProofOfReservesAuditReport
)

class TestExchangeProofOfReservesEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeProofOfReservesEngine(min_reserve_ratio_pct=100.0)

    def test_valid_merkle_inclusion_and_solvent_reserves(self):
        # User Leaf: Alice, 2.5 BTC
        alice = UserAccountBalance("USR_ALICE_01", "BTC", 2.5)
        leaf_hash = self.engine.compute_leaf_hash("USR_ALICE_01", "BTC", 2.5)
        
        # Sibling: Bob, 7.5 BTC (Right Sibling)
        bob_hash = self.engine.compute_leaf_hash("USR_BOB_02", "BTC", 7.5)
        root_hash, total_bal = self.engine.compute_parent_hash(leaf_hash, 2.5, bob_hash, 7.5)

        path = [MerkleAuditPathNode(sibling_hash=bob_hash, sibling_balance=7.5, is_sibling_right=True)]

        # On-chain reserves = 10.5 BTC (105.0% ratio >= 100.0% -> SOLVENT!)
        report = self.engine.verify_proof_of_reserves(
            exchange_name="BINANCE_POR",
            user_leaf=alice,
            audit_path=path,
            declared_merkle_root=root_hash,
            total_declared_liabilities=10.0,
            total_verified_onchain_reserves=10.5
        )

        self.assertTrue(report.is_user_inclusion_verified)
        self.assertEqual(report.reserve_ratio_percentage, 105.0)
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")

    def test_insolvent_reserve_deficit(self):
        alice = UserAccountBalance("USR_ALICE_01", "BTC", 2.5)
        leaf_hash = self.engine.compute_leaf_hash("USR_ALICE_01", "BTC", 2.5)
        bob_hash = self.engine.compute_leaf_hash("USR_BOB_02", "BTC", 7.5)
        root_hash, _ = self.engine.compute_parent_hash(leaf_hash, 2.5, bob_hash, 7.5)

        path = [MerkleAuditPathNode(sibling_hash=bob_hash, sibling_balance=7.5, is_sibling_right=True)]

        # On-chain reserves = 9.2 BTC (92.0% ratio < 100.0% -> INSOLVENT DEFICIT!)
        report = self.engine.verify_proof_of_reserves(
            exchange_name="FTX_LEGACY",
            user_leaf=alice,
            audit_path=path,
            declared_merkle_root=root_hash,
            total_declared_liabilities=10.0,
            total_verified_onchain_reserves=9.2
        )

        self.assertTrue(report.is_user_inclusion_verified)
        self.assertEqual(report.reserve_ratio_percentage, 92.0)
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")

if __name__ == '__main__':
    unittest.main()
