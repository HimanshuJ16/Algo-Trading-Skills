import unittest
from decimal import Decimal

from exchange_proof_of_reserves_verification import (
    ExchangeProofOfReservesEngine,
    MerkleAuditPathNode,
    ProofOfReservesError,
    UserAccountBalance,
)


class TestExchangeProofOfReservesEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeProofOfReservesEngine(min_reserve_ratio_pct=100.0)
        # Two-leaf tree: Alice 2.5 BTC + Bob 7.5 BTC, root sum 10 BTC.
        self.alice = UserAccountBalance("USR_ALICE_01", "BTC", "2.5")
        self.alice_hash = self.engine.compute_leaf_hash("USR_ALICE_01", "BTC", "2.5")
        self.bob_hash = self.engine.compute_leaf_hash("USR_BOB_02", "BTC", "7.5")
        self.root_hash, self.root_sum = self.engine.compute_parent_hash(
            self.alice_hash, "2.5", self.bob_hash, "7.5"
        )
        self.path = [
            MerkleAuditPathNode(
                sibling_hash=self.bob_hash, sibling_balance="7.5", is_sibling_right=True
            )
        ]

    def _audit(self, **overrides):
        kwargs = dict(
            exchange_name="EXCHANGE_UNDER_AUDIT",
            user_leaf=self.alice,
            audit_path=self.path,
            declared_merkle_root=self.root_hash,
            total_declared_liabilities="10",
            total_verified_onchain_reserves="10.5",
        )
        kwargs.update(overrides)
        return self.engine.verify_proof_of_reserves(**kwargs)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------
    def test_valid_merkle_inclusion_and_solvent_reserves(self):
        report = self._audit()
        self.assertTrue(report.is_user_inclusion_verified)
        self.assertTrue(report.is_declared_liability_consistent)
        # 10.5 / 10 * 100 = 105 exactly.
        self.assertEqual(report.reserve_ratio_percentage, Decimal("105"))
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")
        self.assertEqual(report.findings, [])

    def test_insolvent_reserve_deficit(self):
        report = self._audit(total_verified_onchain_reserves="9.2")
        self.assertTrue(report.is_user_inclusion_verified)
        # 9.2 / 10 * 100 = 92 exactly.
        self.assertEqual(report.reserve_ratio_percentage, Decimal("92"))
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")
        self.assertTrue(any(f.startswith("RESERVE_DEFICIT") for f in report.findings))

    def test_exact_full_reserve_boundary_is_solvent(self):
        report = self._audit(total_verified_onchain_reserves="10")
        self.assertEqual(report.reserve_ratio_percentage, Decimal("100"))
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")

    # ------------------------------------------------------------------
    # Root sum vs declared liabilities (the point of a Merkle *sum* tree)
    # ------------------------------------------------------------------
    def test_root_sum_must_equal_declared_liabilities(self):
        # The tree commits to 10 BTC of liabilities but the exchange declares 8,
        # which would flatter a 9 BTC reserve into a 112.5% ratio.
        report = self._audit(
            total_declared_liabilities="8", total_verified_onchain_reserves="9"
        )
        self.assertTrue(report.is_user_inclusion_verified)
        self.assertFalse(report.is_declared_liability_consistent)
        self.assertEqual(report.solvency_status, "INCONSISTENT_LIABILITY_TOTAL")
        self.assertEqual(report.computed_merkle_root_balance, Decimal("10"))
        self.assertTrue(
            any(f.startswith("LIABILITY_UNDERSTATEMENT") for f in report.findings)
        )

    def test_root_sum_check_skipped_when_root_hash_does_not_match(self):
        report = self._audit(declared_merkle_root="ab" * 32)
        self.assertFalse(report.is_user_inclusion_verified)
        self.assertFalse(report.is_declared_liability_consistent)
        self.assertEqual(report.solvency_status, "INVALID_MERKLE_PROOF")
        self.assertTrue(any(f.startswith("ROOT_MISMATCH") for f in report.findings))
        self.assertTrue(
            any(f.startswith("ROOT_SUM_UNVERIFIABLE") for f in report.findings)
        )

    def test_plain_merkle_mode_records_that_liabilities_are_unverified(self):
        engine = ExchangeProofOfReservesEngine(enforce_root_sum_match=False)
        report = engine.verify_proof_of_reserves(
            exchange_name="PLAIN_MERKLE_VENUE",
            user_leaf=self.alice,
            audit_path=self.path,
            declared_merkle_root=self.root_hash,
            total_declared_liabilities="8",
            total_verified_onchain_reserves="9",
        )
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")
        self.assertTrue(
            any(f.startswith("ROOT_SUM_UNENFORCED") for f in report.findings)
        )

    # ------------------------------------------------------------------
    # Threshold and precision
    # ------------------------------------------------------------------
    def test_sub_basis_point_deficit_is_not_rounded_into_solvency(self):
        # 9.9999 / 10 = 99.999%. Rounding the ratio to 2dp before comparing it
        # to the 100% threshold reports this as fully reserved.
        report = self._audit(total_verified_onchain_reserves="9.9999")
        self.assertEqual(report.reserve_ratio_percentage, Decimal("99.999"))
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")

    def test_deficit_note_never_displays_the_ratio_at_the_threshold(self):
        # A note reading "cover only 100.00%" beside an INSOLVENT verdict is the
        # same rounding defect as deciding the verdict on a rounded ratio.
        report = self._audit(total_verified_onchain_reserves="9.9999")
        self.assertIn("99.999%", report.audit_notes)
        self.assertNotIn("100.00%", report.audit_notes)

    def test_one_satoshi_shortfall_on_a_large_book_is_detected(self):
        # 25,000,000,000.00000001 has more significant digits than a binary
        # float can hold, so a float engine sees reserves == liabilities here.
        big = ExchangeProofOfReservesEngine(min_reserve_ratio_pct=100.0)
        a_hash = big.compute_leaf_hash("USR_A", "USDT", "25000000000")
        b_hash = big.compute_leaf_hash("USR_B", "USDT", "0.00000001")
        root, root_sum = big.compute_parent_hash(
            a_hash, "25000000000", b_hash, "0.00000001"
        )
        self.assertEqual(root_sum, Decimal("25000000000.00000001"))
        report = big.verify_proof_of_reserves(
            exchange_name="LARGE_BOOK",
            user_leaf=UserAccountBalance("USR_A", "USDT", "25000000000"),
            audit_path=[MerkleAuditPathNode(b_hash, "0.00000001", True)],
            declared_merkle_root=root,
            total_declared_liabilities="25000000000.00000001",
            total_verified_onchain_reserves="25000000000",
        )
        self.assertTrue(report.is_declared_liability_consistent)
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")

    def test_reported_ratio_is_truncated_never_rounded_up(self):
        # 1 / 3 * 100 = 33.333... -> 33.33333333 at 8dp, truncated downward so
        # the published figure can never overstate coverage.
        a_hash = self.engine.compute_leaf_hash("USR_A", "BTC", "1")
        b_hash = self.engine.compute_leaf_hash("USR_B", "BTC", "2")
        root, _ = self.engine.compute_parent_hash(a_hash, "1", b_hash, "2")
        report = self.engine.verify_proof_of_reserves(
            exchange_name="THIRDS",
            user_leaf=UserAccountBalance("USR_A", "BTC", "1"),
            audit_path=[MerkleAuditPathNode(b_hash, "2", True)],
            declared_merkle_root=root,
            total_declared_liabilities="3",
            total_verified_onchain_reserves="1",
        )
        self.assertEqual(report.reserve_ratio_percentage, Decimal("33.33333333"))
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")

    # ------------------------------------------------------------------
    # Hash encoding
    # ------------------------------------------------------------------
    def test_leaf_and_interior_preimages_are_domain_separated(self):
        # Under a colon-delimited encoding without domain separation, this
        # account_id/asset_symbol pair makes the leaf preimage byte-identical to
        # the interior node above it, letting a subtree be presented as one
        # small user leaf.
        parent_hash, _ = self.engine.compute_parent_hash(
            self.alice_hash, "2.5", self.bob_hash, "7.5"
        )
        forged_leaf = self.engine.compute_leaf_hash(
            self.alice_hash, f"2.50000000:{self.bob_hash}", "7.5"
        )
        self.assertNotEqual(forged_leaf, parent_hash)

    def test_declared_root_accepted_in_any_case_and_with_0x_prefix(self):
        for variant in (self.root_hash.upper(), "0x" + self.root_hash, f"  {self.root_hash}  "):
            with self.subTest(variant=variant[:12]):
                report = self._audit(declared_merkle_root=variant)
                self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")

    def test_malformed_declared_root_raises(self):
        for bad in ("not-a-hash", "abc", "zz" * 32, self.root_hash[:-1], 12345):
            with self.subTest(bad=bad):
                with self.assertRaises(ProofOfReservesError):
                    self._audit(declared_merkle_root=bad)

    # ------------------------------------------------------------------
    # Path traversal
    # ------------------------------------------------------------------
    def test_left_sibling_ordering_is_honoured(self):
        # Same two leaves, but the user is now the right child.
        root, _ = self.engine.compute_parent_hash(
            self.bob_hash, "7.5", self.alice_hash, "2.5"
        )
        left_path = [MerkleAuditPathNode(self.bob_hash, "7.5", is_sibling_right=False)]
        report = self._audit(audit_path=left_path, declared_merkle_root=root)
        self.assertTrue(report.is_user_inclusion_verified)

        # Mislabelling the side must break the proof, not silently succeed.
        wrong_side = [MerkleAuditPathNode(self.bob_hash, "7.5", is_sibling_right=True)]
        report = self._audit(audit_path=wrong_side, declared_merkle_root=root)
        self.assertEqual(report.solvency_status, "INVALID_MERKLE_PROOF")

    def test_multi_level_tree_verifies_and_sums(self):
        e = self.engine
        h_a = e.compute_leaf_hash("A", "BTC", "1")
        h_b = e.compute_leaf_hash("B", "BTC", "2")
        h_c = e.compute_leaf_hash("C", "BTC", "3")
        h_d = e.compute_leaf_hash("D", "BTC", "4")
        h_ab, b_ab = e.compute_parent_hash(h_a, "1", h_b, "2")
        h_cd, b_cd = e.compute_parent_hash(h_c, "3", h_d, "4")
        root, root_sum = e.compute_parent_hash(h_ab, b_ab, h_cd, b_cd)
        self.assertEqual(root_sum, Decimal("10"))

        # C's path: sibling D (right), then sibling AB (left).
        report = e.verify_proof_of_reserves(
            exchange_name="DEPTH_TWO",
            user_leaf=UserAccountBalance("C", "BTC", "3"),
            audit_path=[
                MerkleAuditPathNode(h_d, "4", is_sibling_right=True),
                MerkleAuditPathNode(h_ab, b_ab, is_sibling_right=False),
            ],
            declared_merkle_root=root,
            total_declared_liabilities="10",
            total_verified_onchain_reserves="10",
        )
        self.assertTrue(report.is_user_inclusion_verified)
        self.assertEqual(report.computed_merkle_root_balance, Decimal("10"))
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")

    def test_single_leaf_tree_with_empty_audit_path(self):
        solo_hash = self.engine.compute_leaf_hash("SOLO", "BTC", "4")
        report = self.engine.verify_proof_of_reserves(
            exchange_name="SOLO_TREE",
            user_leaf=UserAccountBalance("SOLO", "BTC", "4"),
            audit_path=[],
            declared_merkle_root=solo_hash,
            total_declared_liabilities="4",
            total_verified_onchain_reserves="4",
        )
        self.assertTrue(report.is_user_inclusion_verified)
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")

    # ------------------------------------------------------------------
    # Negative balance manipulation
    # ------------------------------------------------------------------
    def test_negative_user_leaf_balance_rejects_the_proof(self):
        negative = UserAccountBalance("USR_GHOST", "BTC", "-500")
        report = self._audit(user_leaf=negative)
        self.assertFalse(report.is_user_inclusion_verified)
        self.assertEqual(report.solvency_status, "INVALID_MERKLE_PROOF")
        self.assertTrue(
            any(f.startswith("NEGATIVE_LEAF_BALANCE") for f in report.findings)
        )

    def test_negative_sibling_balance_rejects_the_proof(self):
        ghost_hash = self.engine.compute_leaf_hash("USR_GHOST", "BTC", "-500")
        root, _ = self.engine.compute_parent_hash(
            self.alice_hash, "2.5", ghost_hash, "-500"
        )
        report = self._audit(
            audit_path=[MerkleAuditPathNode(ghost_hash, "-500", True)],
            declared_merkle_root=root,
        )
        # The hash chain is internally consistent -- only the balance audit
        # catches this.
        self.assertEqual(report.computed_merkle_root_hash, root)
        self.assertFalse(report.is_user_inclusion_verified)
        self.assertEqual(report.solvency_status, "INVALID_MERKLE_PROOF")
        self.assertTrue(
            any(f.startswith("NEGATIVE_SIBLING_BALANCE") for f in report.findings)
        )

    def test_negative_zero_balance_is_not_treated_as_negative(self):
        zero_leaf = UserAccountBalance("USR_ZERO", "BTC", "-0.0")
        zero_hash = self.engine.compute_leaf_hash("USR_ZERO", "BTC", "-0.0")
        root, root_sum = self.engine.compute_parent_hash(
            zero_hash, "-0.0", self.bob_hash, "7.5"
        )
        self.assertEqual(root_sum, Decimal("7.5"))
        report = self._audit(
            user_leaf=zero_leaf,
            declared_merkle_root=root,
            total_declared_liabilities="7.5",
            total_verified_onchain_reserves="7.5",
        )
        self.assertEqual(report.solvency_status, "SOLVENT_FULL_RESERVES")
        self.assertEqual(report.findings, [])

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    def test_non_finite_balances_raise_instead_of_poisoning_the_ratio(self):
        # Every comparison against NaN is False, so an unguarded NaN slips past
        # the `< 0` check and propagates into the ratio.
        for bad in ("nan", "inf", "-inf", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ProofOfReservesError):
                    self._audit(user_leaf=UserAccountBalance("U", "BTC", bad))
                with self.assertRaises(ProofOfReservesError):
                    self._audit(total_verified_onchain_reserves=bad)

    def test_non_positive_liabilities_raise(self):
        for bad in ("0", "-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ProofOfReservesError):
                    self._audit(total_declared_liabilities=bad)

    def test_negative_onchain_reserves_raise(self):
        with self.assertRaises(ProofOfReservesError):
            self._audit(total_verified_onchain_reserves="-1")

    def test_unparseable_balance_raises(self):
        with self.assertRaises(ProofOfReservesError):
            self._audit(total_verified_onchain_reserves="ten point five")
        with self.assertRaises(ProofOfReservesError):
            self._audit(total_verified_onchain_reserves=None)

    def test_malformed_audit_path_entries_raise(self):
        with self.assertRaises(ProofOfReservesError):
            self._audit(audit_path=[("not", "a", "node")])
        with self.assertRaises(ProofOfReservesError):
            self._audit(audit_path="not-a-sequence-of-nodes")

    def test_empty_identifiers_raise(self):
        with self.assertRaises(ProofOfReservesError):
            self.engine.compute_leaf_hash("", "BTC", "1")
        with self.assertRaises(ProofOfReservesError):
            self.engine.compute_leaf_hash("USR", "", "1")
        with self.assertRaises(ProofOfReservesError):
            self._audit(exchange_name="   ")

    def test_engine_configuration_is_validated(self):
        with self.assertRaises(ProofOfReservesError):
            ExchangeProofOfReservesEngine(min_reserve_ratio_pct=0.0)
        with self.assertRaises(ProofOfReservesError):
            ExchangeProofOfReservesEngine(min_reserve_ratio_pct=-5.0)
        with self.assertRaises(ProofOfReservesError):
            ExchangeProofOfReservesEngine(balance_decimals=19)
        with self.assertRaises(ProofOfReservesError):
            ExchangeProofOfReservesEngine(balance_decimals=-1)

    def test_balance_precision_must_match_the_exchange_tree(self):
        # A verifier configured for the wrong precision hashes a different
        # preimage and cannot reproduce the published root.
        coarse = ExchangeProofOfReservesEngine(balance_decimals=2)
        self.assertNotEqual(
            coarse.compute_leaf_hash("USR_ALICE_01", "BTC", "2.5"), self.alice_hash
        )

    def test_reserve_buffer_above_full_reserves_is_configurable(self):
        strict = ExchangeProofOfReservesEngine(min_reserve_ratio_pct=105.0)
        report = strict.verify_proof_of_reserves(
            exchange_name="BUFFERED",
            user_leaf=self.alice,
            audit_path=self.path,
            declared_merkle_root=self.root_hash,
            total_declared_liabilities="10",
            total_verified_onchain_reserves="10.4",
        )
        self.assertEqual(report.reserve_ratio_percentage, Decimal("104"))
        self.assertEqual(report.solvency_status, "INSOLVENT_RESERVE_DEFICIT")


if __name__ == "__main__":
    unittest.main()
