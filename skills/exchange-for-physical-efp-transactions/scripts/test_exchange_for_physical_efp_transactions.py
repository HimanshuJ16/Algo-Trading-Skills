"""Unit tests for exchange-for-physical-efp-transactions."""
import unittest

from exchange_for_physical_efp_transactions import (
    EfpFuturesLeg,
    EfpPhysicalLeg,
    EfrpBonaFideAttestation,
    ExchangeForPhysicalEngine,
    STATUSES,
)


def gold_futures_leg(**overrides) -> EfpFuturesLeg:
    """10 GC contracts, 100 troy oz each -> 1,000 oz, at $2,500.00/oz."""
    kwargs = dict(
        futures_symbol="GC_202612",
        contract_count=10,
        contract_multiplier=100.0,
        futures_price_usd=2500.00,
        side="SELL_FUTURES",
        quantity_unit="TROY_OZ",
    )
    kwargs.update(overrides)
    return EfpFuturesLeg(**kwargs)


def gold_physical_leg(**overrides) -> EfpPhysicalLeg:
    """1,000 oz of spot gold at $2,490.00/oz."""
    kwargs = dict(
        physical_symbol="SPOT_GOLD_USD",
        physical_quantity=1000.0,
        spot_price_usd=2490.00,
        side="BUY_PHYSICAL",
        quantity_unit="TROY_OZ",
    )
    kwargs.update(overrides)
    return EfpPhysicalLeg(**kwargs)


def full_attestation(**overrides) -> EfrpBonaFideAttestation:
    kwargs = dict(
        ownership_transfer_confirmed=True,
        non_transitory_confirmed=True,
        accounts_independently_controlled=True,
        attested_by="commodities-desk-compliance",
        supporting_document_ref="WAREHOUSE_RECEIPT_88231",
    )
    kwargs.update(overrides)
    return EfrpBonaFideAttestation(**kwargs)


class TestApprovalPath(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine(quantity_tolerance=1e-4)

    def test_valid_gold_efp_transaction_approved(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_GOLD_001",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            risk_free_rate=0.04,
            time_to_expiry_years=0.25,
            attestation=full_attestation(),
        )

        self.assertEqual(report.status, "EFP_APPROVED")
        self.assertTrue(report.is_quantity_equivalent)
        self.assertTrue(report.is_side_structure_valid)
        self.assertTrue(report.is_bona_fide_attested)
        self.assertEqual(report.attestation_failures, {})
        self.assertEqual(report.required_physical_quantity, 1000.0)
        self.assertEqual(report.quantity_deviation_ratio, 0.0)
        self.assertEqual(report.observed_efp_basis_usd, 10.00)
        self.assertIsNotNone(report.efrp_clearing_payload)

    def test_payload_does_not_claim_a_submission_that_never_happened(self):
        # The engine talks to no venue. Asserting SUBMITTED in an audit record that was
        # only ever computed locally is a recordkeeping falsehood.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_GOLD_002",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            attestation=full_attestation(),
        )
        payload = report.efrp_clearing_payload
        self.assertEqual(payload["reporting_status"], "PENDING_SUBMISSION")
        self.assertEqual(payload["attested_by"], "commodities-desk-compliance")
        self.assertEqual(payload["supporting_document_ref"], "WAREHOUSE_RECEIPT_88231")
        self.assertEqual(payload["futures_side"], "SELL_FUTURES")
        self.assertEqual(payload["physical_side"], "BUY_PHYSICAL")

    def test_declared_status_set_is_exactly_what_the_engine_can_return(self):
        self.assertEqual(
            sorted(STATUSES),
            sorted([
                "EFP_APPROVED",
                "SIDE_DIRECTION_VIOLATION",
                "UNIT_MISMATCH_REJECTION",
                "QUANTITY_MISMATCH_REJECTION",
                "BONA_FIDE_ATTESTATION_MISSING",
                "RULE_538_VIOLATION",
            ]),
        )


class TestBasisArithmetic(unittest.TestCase):
    """
    Expected values derived by hand from F = S * exp((r + u - y) * T), independently of the
    implementation's own expression.
    """

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine()

    def test_financing_only_fair_basis(self):
        # S = 2490, r = 4%, T = 0.25 -> exp(0.01) = 1.0100501670841680...
        # basis = 2490 * 0.0100501670841680 = 25.0249160395...  -> 25.0249 at 4 dp.
        # The prior SKILL.md documented 24.95 for this case, which was simply wrong.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_CARRY_001",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            risk_free_rate=0.04,
            time_to_expiry_years=0.25,
            attestation=full_attestation(),
        )
        self.assertAlmostEqual(report.theoretical_fair_basis_usd, 25.0249, places=4)
        # Observed +10.00 is well below the +25.02 carry basis: the future is cheap to carry.
        self.assertAlmostEqual(report.basis_mispricing_usd, 10.00 - 25.0249, places=4)

    def test_storage_cost_widens_the_fair_basis(self):
        # Same trade with u = 2% storage: net carry 6%, exp(0.015) = 1.01511306461...
        # basis = 2490 * 0.01511306461 = 37.6315...  -> 37.6315 at 4 dp.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_CARRY_002",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            risk_free_rate=0.04,
            time_to_expiry_years=0.25,
            attestation=full_attestation(),
            storage_cost_rate=0.02,
        )
        self.assertAlmostEqual(report.theoretical_fair_basis_usd, 37.6315, places=4)

    def test_convenience_yield_narrows_and_can_invert_the_fair_basis(self):
        # r = 4%, u = 1%, y = 9% -> net carry -4%, exp(-0.01) = 0.99004983374916...
        # basis = 2490 * (-0.00995016625) = -24.7759...  -> backwardation.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_CARRY_003",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            risk_free_rate=0.04,
            time_to_expiry_years=0.25,
            attestation=full_attestation(),
            storage_cost_rate=0.01,
            convenience_yield=0.09,
        )
        self.assertAlmostEqual(report.theoretical_fair_basis_usd, -24.7759, places=4)
        self.assertLess(report.theoretical_fair_basis_usd, 0.0)

    def test_zero_maturity_gives_zero_fair_basis(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_CARRY_004",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            time_to_expiry_years=0.0,
            attestation=full_attestation(),
        )
        self.assertEqual(report.theoretical_fair_basis_usd, 0.0)
        self.assertEqual(report.basis_mispricing_usd, 10.0)

    def test_basis_is_reported_on_rejection_paths_not_zero_filled(self):
        # A rejected EFP still has a real observed basis; zeroing it makes the audit record
        # indistinguishable from a genuine zero basis.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_CARRY_005",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(physical_quantity=900.0),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "QUANTITY_MISMATCH_REJECTION")
        self.assertEqual(report.observed_efp_basis_usd, 10.00)
        self.assertAlmostEqual(report.theoretical_fair_basis_usd, 25.0249, places=4)


class TestSideStructure(unittest.TestCase):
    """An EFRP requires the two legs to be on opposite sides (ICE Rule 4.06(b)(i))."""

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine()

    def test_same_direction_legs_are_rejected(self):
        # Regression: the previous engine ignored `side` entirely and approved this,
        # doubling the long exposure while labelling it an EFP.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_SIDE_001",
            futures_leg=gold_futures_leg(side="BUY_FUTURES"),
            physical_leg=gold_physical_leg(side="BUY_PHYSICAL"),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "SIDE_DIRECTION_VIOLATION")
        self.assertFalse(report.is_side_structure_valid)
        self.assertIsNone(report.efrp_clearing_payload)

    def test_both_valid_pairings_are_accepted(self):
        for f_side, p_side in (("BUY_FUTURES", "SELL_PHYSICAL"), ("SELL_FUTURES", "BUY_PHYSICAL")):
            with self.subTest(futures_side=f_side):
                report = self.engine.evaluate_efp_transaction(
                    efp_id=f"EFP_SIDE_{f_side}",
                    futures_leg=gold_futures_leg(side=f_side),
                    physical_leg=gold_physical_leg(side=p_side),
                    attestation=full_attestation(),
                )
                self.assertEqual(report.status, "EFP_APPROVED")

    def test_unknown_side_labels_raise(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_efp_transaction(
                efp_id="EFP_SIDE_002",
                futures_leg=gold_futures_leg(side="LONG"),
                physical_leg=gold_physical_leg(),
                attestation=full_attestation(),
            )
        with self.assertRaises(ValueError):
            self.engine.evaluate_efp_transaction(
                efp_id="EFP_SIDE_003",
                futures_leg=gold_futures_leg(),
                physical_leg=gold_physical_leg(side="SELL_FUTURES"),
                attestation=full_attestation(),
            )


class TestQuantityEquivalence(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine(quantity_tolerance=1e-4)

    def test_quantity_mismatch_rejection(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_QTY_001",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(physical_quantity=900.0),
            attestation=full_attestation(),
        )
        self.assertFalse(report.is_quantity_equivalent)
        self.assertEqual(report.status, "QUANTITY_MISMATCH_REJECTION")
        self.assertIsNone(report.efrp_clearing_payload)
        self.assertEqual(report.required_physical_quantity, 1000.0)
        self.assertAlmostEqual(report.quantity_deviation_ratio, 0.10, places=10)

    def test_deviation_exactly_at_the_absolute_tolerance_is_accepted(self):
        # Boundary: the check rejects only when the deviation *exceeds* the allowance.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_QTY_002",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(physical_quantity=1000.0 + 1e-4),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "EFP_APPROVED")

    def test_relative_tolerance_admits_an_approximately_equivalent_quantity(self):
        # Rules require "approximately equivalent", not identical, and venue tolerances
        # differ (Eurex permits up to 20% deviation on the FX leg of an FX-futures EFP).
        strict = ExchangeForPhysicalEngine()
        tolerant = ExchangeForPhysicalEngine(quantity_tolerance_ratio=0.20)
        legs = dict(
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(physical_quantity=950.0),
            attestation=full_attestation(),
        )
        self.assertEqual(
            strict.evaluate_efp_transaction(efp_id="EFP_QTY_003", **legs).status,
            "QUANTITY_MISMATCH_REJECTION",
        )
        self.assertEqual(
            tolerant.evaluate_efp_transaction(efp_id="EFP_QTY_004", **legs).status,
            "EFP_APPROVED",
        )

    def test_relative_tolerance_still_rejects_beyond_its_band(self):
        tolerant = ExchangeForPhysicalEngine(quantity_tolerance_ratio=0.20)
        report = tolerant.evaluate_efp_transaction(
            efp_id="EFP_QTY_005",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(physical_quantity=700.0),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "QUANTITY_MISMATCH_REJECTION")

    def test_mismatched_quantity_units_are_rejected_before_the_number_check(self):
        # 1,000 bbl of crude is 42,000 gal: the bare numbers can agree while the trade does not.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_QTY_006",
            futures_leg=gold_futures_leg(quantity_unit="BBL"),
            physical_leg=gold_physical_leg(quantity_unit="GAL"),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "UNIT_MISMATCH_REJECTION")
        self.assertIsNone(report.efrp_clearing_payload)

    def test_unit_comparison_is_case_insensitive(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_QTY_007",
            futures_leg=gold_futures_leg(quantity_unit="troy_oz"),
            physical_leg=gold_physical_leg(quantity_unit="TROY_OZ"),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "EFP_APPROVED")

    def test_units_omitted_on_one_leg_skips_the_unit_check(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_QTY_008",
            futures_leg=gold_futures_leg(quantity_unit=""),
            physical_leg=gold_physical_leg(quantity_unit="TROY_OZ"),
            attestation=full_attestation(),
        )
        self.assertEqual(report.status, "EFP_APPROVED")


class TestBonaFideAttestation(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine()

    def test_missing_attestation_is_not_approved(self):
        # Regression: the previous engine approved and stamped SUBMITTED_TO_CLEARINGHOUSE
        # without any bona fide determination having been made or recorded.
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_BONA_001",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
        )
        self.assertEqual(report.status, "BONA_FIDE_ATTESTATION_MISSING")
        self.assertFalse(report.is_bona_fide_attested)
        self.assertIsNone(report.efrp_clearing_payload)

    def test_transitory_efrp_is_rejected_with_its_rule_reference(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_BONA_002",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            attestation=full_attestation(non_transitory_confirmed=False),
        )
        self.assertEqual(report.status, "RULE_538_VIOLATION")
        self.assertIn("non_transitory_confirmed", report.attestation_failures)
        self.assertIn("538.K", report.attestation_failures["non_transitory_confirmed"])
        self.assertIsNone(report.efrp_clearing_payload)

    def test_each_unattested_requirement_is_reported_individually(self):
        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_BONA_003",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            attestation=full_attestation(
                ownership_transfer_confirmed=False,
                accounts_independently_controlled=False,
            ),
        )
        self.assertEqual(report.status, "RULE_538_VIOLATION")
        self.assertEqual(
            sorted(report.attestation_failures),
            ["accounts_independently_controlled", "ownership_transfer_confirmed"],
        )

    def test_anonymous_attestation_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_efp_transaction(
                efp_id="EFP_BONA_004",
                futures_leg=gold_futures_leg(),
                physical_leg=gold_physical_leg(),
                attestation=full_attestation(attested_by="   "),
            )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine()

    def _evaluate(self, **overrides):
        kwargs = dict(
            efp_id="EFP_VAL_001",
            futures_leg=gold_futures_leg(),
            physical_leg=gold_physical_leg(),
            attestation=full_attestation(),
        )
        kwargs.update(overrides)
        return self.engine.evaluate_efp_transaction(**kwargs)

    def test_non_finite_price_raises_instead_of_approving_a_nan_report(self):
        with self.assertRaises(ValueError):
            self._evaluate(physical_leg=gold_physical_leg(spot_price_usd=float("nan")))
        with self.assertRaises(ValueError):
            self._evaluate(futures_leg=gold_futures_leg(futures_price_usd=float("inf")))

    def test_non_finite_rate_or_maturity_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(risk_free_rate=float("nan"))
        with self.assertRaises(ValueError):
            self._evaluate(time_to_expiry_years=float("inf"))
        with self.assertRaises(ValueError):
            self._evaluate(convenience_yield=float("nan"))

    def test_negative_maturity_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(time_to_expiry_years=-0.25)

    def test_non_positive_quantities_raise(self):
        with self.assertRaises(ValueError):
            self._evaluate(futures_leg=gold_futures_leg(contract_count=0))
        with self.assertRaises(ValueError):
            self._evaluate(futures_leg=gold_futures_leg(contract_count=-10))
        with self.assertRaises(ValueError):
            self._evaluate(futures_leg=gold_futures_leg(contract_multiplier=0.0))
        with self.assertRaises(ValueError):
            self._evaluate(physical_leg=gold_physical_leg(physical_quantity=-1000.0))

    def test_non_integer_contract_count_raises(self):
        with self.assertRaises(TypeError):
            self._evaluate(futures_leg=gold_futures_leg(contract_count=10.0))

    def test_blank_efp_id_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(efp_id="   ")

    def test_negative_spot_price_is_priced_but_flagged(self):
        # WTI settled at -$37.63 on 2020-04-20, so a negative price is not malformed input.
        # The carry relation is not meaningful there, so the engine warns rather than raises.
        with self.assertLogs(
            "exchange_for_physical_efp_transactions", level="WARNING"
        ) as captured:
            report = self._evaluate(
                futures_leg=gold_futures_leg(futures_price_usd=-30.0),
                physical_leg=gold_physical_leg(spot_price_usd=-37.63),
            )
        self.assertEqual(report.status, "EFP_APPROVED")
        self.assertAlmostEqual(report.observed_efp_basis_usd, 7.63, places=4)
        self.assertTrue(any("not positive" in line for line in captured.output))

    def test_engine_rejects_an_out_of_range_tolerance_ratio(self):
        with self.assertRaises(ValueError):
            ExchangeForPhysicalEngine(quantity_tolerance_ratio=1.0)
        with self.assertRaises(ValueError):
            ExchangeForPhysicalEngine(quantity_tolerance_ratio=-0.1)
        with self.assertRaises(ValueError):
            ExchangeForPhysicalEngine(quantity_tolerance=-1.0)


if __name__ == "__main__":
    unittest.main()
