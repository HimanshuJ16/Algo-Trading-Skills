import unittest
from decimal import Decimal

from transfer_pricing_considerations_for_multi_entity_trading_operations import (
    SPLIT_BASIS_CONTRIBUTION,
    SPLIT_BASIS_RESIDUAL,
    DEMPEContribution,
    EntityType,
    IntercompanyTransaction,
    LegalEntity,
    TPMethodology,
    TransferPricingEngine,
    TransferPricingError,
)


class TransferPricingTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = TransferPricingEngine()

        self.us_ip_entity = LegalEntity(
            entity_id="ENT-US",
            name="Alpha Trading Systems LLC",
            jurisdiction="United States",
            entity_type=EntityType.IP_OWNER,
            tax_rate_pct=21.0,
        )
        self.uk_manager = LegalEntity(
            entity_id="ENT-UK",
            name="Alpha Asset Management UK Ltd",
            jurisdiction="United Kingdom",
            entity_type=EntityType.INVESTMENT_MANAGER,
            tax_rate_pct=25.0,
        )
        self.sg_execution = LegalEntity(
            entity_id="ENT-SG",
            name="Alpha Execution Hub SG Pte Ltd",
            jurisdiction="Singapore",
            entity_type=EntityType.EXECUTION_HUB,
            tax_rate_pct=17.0,
        )

        for entity in (self.us_ip_entity, self.uk_manager, self.sg_execution):
            self.engine.register_entity(entity)


class TestEntityRegistration(TransferPricingTestBase):
    def test_entity_registration(self):
        self.assertEqual(len(self.engine.entities), 3)
        self.assertIn("ENT-US", self.engine.entities)

    def test_register_rejects_non_entity(self):
        with self.assertRaises(TransferPricingError):
            self.engine.register_entity({"entity_id": "ENT-XX"})

    def test_register_rejects_blank_entity_id(self):
        with self.assertRaises(TransferPricingError):
            self.engine.register_entity(
                LegalEntity("   ", "Blank", "US", EntityType.IP_OWNER, 21.0)
            )


class TestCostPlusFee(TransferPricingTestBase):
    def test_cost_plus_fee_calculation(self):
        # 1,000,000 * 1.12 = 1,120,000; markup 1,000,000 * 0.12 = 120,000
        fee, markup = TransferPricingEngine.calculate_cost_plus_fee(1_000_000.0, 12.0)
        self.assertEqual(fee, 1_120_000.0)
        self.assertEqual(markup, 120_000.0)

    def test_zero_markup_is_permitted(self):
        """US Services Cost Method / IRAS strict pass-through charge at cost."""
        fee, markup = TransferPricingEngine.calculate_cost_plus_fee(250_000.0, 0.0)
        self.assertEqual(fee, 250_000.0)
        self.assertEqual(markup, 0.0)

    def test_oecd_low_value_adding_five_percent_markup(self):
        # TPG 2022 para. 7.61 simplified approach, derived independently:
        # markup 812,345.67 * 0.05 = 40,617.2835 -> 40,617.28 half-up,
        # fee 812,345.67 + 40,617.28 = 852,962.95
        fee, markup = TransferPricingEngine.calculate_cost_plus_fee(812_345.67, 5.0)
        self.assertEqual(markup, 40_617.28)
        self.assertEqual(fee, 852_962.95)

    def test_markup_below_minus_100_pct_is_rejected(self):
        """A markup of -150% would invert the fee; a negative service fee is not a fee."""
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_cost_plus_fee(100_000.0, -150.0)

    def test_negative_base_cost_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_cost_plus_fee(-1.0, 10.0)

    def test_non_finite_markup_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_cost_plus_fee(100_000.0, float("nan"))

    def test_bool_is_not_accepted_as_a_number(self):
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_cost_plus_fee(True, 10.0)

    def test_markup_rounds_half_up_not_half_even(self):
        """
        1.25 * 10% = 0.125 exactly. Banker's rounding gives 0.12; an invoice
        prepared under conventional half-up rounding shows 0.13.
        """
        _, markup = TransferPricingEngine.calculate_cost_plus_fee(1.25, 10.0)
        self.assertEqual(markup, 0.13)


class TestCupFee(TransferPricingTestBase):
    def test_cup_fee_calculation(self):
        # 50,000 executions @ $0.50 = $25,000
        self.assertEqual(TransferPricingEngine.calculate_cup_fee(50_000.0, 0.50), 25_000.0)

    def test_cup_fee_uses_decimal_multiplication(self):
        """
        1.50 * 0.15 is 0.22499999999999998 in binary float and rounds to 0.22
        even under half-up; the exact decimal product 0.2250 rounds to 0.23.
        """
        self.assertEqual(TransferPricingEngine.calculate_cup_fee(1.50, 0.15), 0.23)

    def test_negative_volume_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_cup_fee(-1.0, 0.50)


class TestBerryRatio(TransferPricingTestBase):
    def test_berry_ratio_calculation(self):
        # Gross profit 1,150,000 / opex 1,000,000 = 1.15
        self.assertAlmostEqual(
            TransferPricingEngine.calculate_berry_ratio(1_150_000.0, 1_000_000.0), 1.15
        )

    def test_berry_ratio_is_gross_profit_over_opex_not_revenue_over_cost(self):
        """
        TPG 2022 para. 2.106: gross profit is net sales less COGS. A provider
        billing 1,200,000 with 700,000 of COGS and 400,000 of opex has a Berry
        ratio of 500,000/400,000 = 1.25, not 1,200,000/1,100,000 = 1.0909...
        """
        self.assertAlmostEqual(
            TransferPricingEngine.calculate_berry_ratio(1_200_000.0 - 700_000.0, 400_000.0),
            1.25,
        )

    def test_zero_opex_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            TransferPricingEngine.calculate_berry_ratio(100.0, 0.0)


class TestProcessIntercompanyTransaction(TransferPricingTestBase):
    def test_cost_plus_settlement(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-101",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-US",
            service_description="Quant research & alpha strategy generation",
            base_cost_usd=500_000.0,
            tp_method=TPMethodology.COST_PLUS,
            markup_pct=10.0,
            cogs_usd=0.0,
            operating_expenses_usd=500_000.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 550_000.0)
        self.assertEqual(settlement.intercompany_markup_usd, 50_000.0)
        self.assertEqual(settlement.profit_level_indicator, "GROSS_COST_PLUS_MARKUP")
        # Provider has no COGS, so gross profit is the full 550,000 fee against
        # 500,000 of operating expenses.
        self.assertAlmostEqual(settlement.berry_ratio, 1.10)

    def test_berry_ratio_is_none_when_opex_not_supplied(self):
        """
        The old behaviour inferred opex from the cost base and always reported a
        ratio. An inferred Berry ratio is just the markup factor relabelled.
        """
        tx = IntercompanyTransaction(
            transaction_id="TX-102",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-US",
            service_description="Quant research",
            base_cost_usd=500_000.0,
            tp_method=TPMethodology.COST_PLUS,
            markup_pct=10.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertIsNone(settlement.berry_ratio)
        self.assertTrue(
            any("Berry ratio not computed" in w for w in settlement.warnings)
        )

    def test_berry_ratio_reflects_supplied_cogs(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-103",
            provider_entity_id="ENT-SG",
            recipient_entity_id="ENT-US",
            service_description="Execution routing with purchased exchange capacity",
            base_cost_usd=1_100_000.0,
            tp_method=TPMethodology.COST_PLUS,
            markup_pct=9.0909090909,
            cogs_usd=700_000.0,
            operating_expenses_usd=400_000.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        # Fee 1,200,000 - COGS 700,000 = 500,000 gross profit over 400,000 opex.
        self.assertEqual(settlement.arm_length_fee_usd, 1_200_000.0)
        self.assertAlmostEqual(settlement.berry_ratio, 1.25)

    def test_cup_settlement(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-104",
            provider_entity_id="ENT-SG",
            recipient_entity_id="ENT-US",
            service_description="Order execution routing",
            base_cost_usd=100_000.0,
            tp_method=TPMethodology.CUP,
            benchmark_cup_rate_usd=2.50,
            volume_units=50_000.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 125_000.0)
        self.assertEqual(settlement.intercompany_markup_usd, 25_000.0)
        self.assertEqual(settlement.profit_level_indicator, "COMPARABLE_UNCONTROLLED_PRICE")

    def test_cup_with_zero_cost_base_does_not_raise(self):
        """
        Regression: the Berry ratio was previously computed unconditionally on
        base_cost_usd, so a CUP transaction priced purely off a third-party rate
        card with no cost base raised instead of settling.
        """
        tx = IntercompanyTransaction(
            transaction_id="TX-105",
            provider_entity_id="ENT-SG",
            recipient_entity_id="ENT-UK",
            service_description="Exchange rate-card pass-through",
            base_cost_usd=0.0,
            tp_method=TPMethodology.CUP,
            benchmark_cup_rate_usd=0.35,
            volume_units=200_000.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 70_000.0)
        self.assertIsNone(settlement.berry_ratio)

    def test_cup_below_cost_is_flagged_not_silently_settled(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-106",
            provider_entity_id="ENT-SG",
            recipient_entity_id="ENT-US",
            service_description="Execution routing priced below cost",
            base_cost_usd=200_000.0,
            tp_method=TPMethodology.CUP,
            benchmark_cup_rate_usd=1.00,
            volume_units=50_000.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.intercompany_markup_usd, -150_000.0)
        self.assertTrue(any("below the provider" in w for w in settlement.warnings))

    def test_tnmm_is_labelled_as_a_net_cost_plus_pli(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-107",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-US",
            service_description="Middle-office support",
            base_cost_usd=400_000.0,
            tp_method=TPMethodology.TNMM,
            markup_pct=8.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 432_000.0)
        self.assertEqual(settlement.profit_level_indicator, "NET_COST_PLUS_MARKUP")
        self.assertTrue(any("net cost-plus PLI" in w for w in settlement.warnings))

    def test_profit_split_method_is_rejected_as_an_invoice(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-108",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-US",
            service_description="Global trading profit",
            base_cost_usd=1.0,
            tp_method=TPMethodology.PROFIT_SPLIT,
        )
        with self.assertRaises(TransferPricingError):
            self.engine.process_intercompany_transaction(tx)

    def test_unregistered_entity_raises_error(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-BAD",
            provider_entity_id="ENT-UNKNOWN",
            recipient_entity_id="ENT-US",
            service_description="Test",
            base_cost_usd=10_000.0,
            tp_method=TPMethodology.COST_PLUS,
        )
        with self.assertRaises(TransferPricingError):
            self.engine.process_intercompany_transaction(tx)

    def test_self_dealing_is_rejected(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-SELF",
            provider_entity_id="ENT-US",
            recipient_entity_id="ENT-US",
            service_description="Self-billing",
            base_cost_usd=10_000.0,
            tp_method=TPMethodology.COST_PLUS,
        )
        with self.assertRaises(TransferPricingError):
            self.engine.process_intercompany_transaction(tx)

    def test_same_jurisdiction_pair_is_flagged(self):
        self.engine.register_entity(
            LegalEntity(
                entity_id="ENT-UK2",
                name="Alpha Research UK Ltd",
                jurisdiction="United Kingdom",
                entity_type=EntityType.IP_OWNER,
                tax_rate_pct=25.0,
            )
        )
        tx = IntercompanyTransaction(
            transaction_id="TX-DOM",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-UK2",
            service_description="Domestic support services",
            base_cost_usd=100_000.0,
            tp_method=TPMethodology.COST_PLUS,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertTrue(any("domestic controlled" in w for w in settlement.warnings))


class TestDEMPEContribution(TransferPricingTestBase):
    def test_weight_above_one_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            DEMPEContribution("ENT-US", 50.0, 0.5, 0.5, 0.5, 0.5)

    def test_negative_weight_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            DEMPEContribution("ENT-US", -0.2, 0.5, 0.5, 0.5, 0.5)

    def test_equal_weighted_score(self):
        # (1.0 + 0.9 + 0.8 + 1.0 + 0.7) / 5 = 4.4 / 5 = 0.88
        contribution = DEMPEContribution("ENT-US", 1.0, 0.9, 0.8, 1.0, 0.7)
        self.assertAlmostEqual(contribution.total_dempe_score, 0.88)


def exact_total(allocations):
    """
    Sums cent-denominated allocations in exact decimal arithmetic.

    Adding the floats back up reintroduces binary representation error, so a
    plain float sum tests float addition rather than the allocation invariant.
    """
    return sum((Decimal(str(v)) for v in allocations), Decimal("0"))


class TestProfitSplit(TransferPricingTestBase):
    def setUp(self):
        super().setUp()
        # Equal-weighted scores: US = 4.4/5 = 0.88, UK = 1.5/5 = 0.30.
        self.dempe_us = DEMPEContribution("ENT-US", 1.0, 0.9, 0.8, 1.0, 0.7)
        self.dempe_uk = DEMPEContribution("ENT-UK", 0.2, 0.3, 0.4, 0.1, 0.5)

    def test_contribution_analysis_exact_allocation(self):
        """
        Independently derived: total key 0.88 + 0.30 = 1.18.
        US share 0.88/1.18 = 0.745762711864...; 10,000,000 * that
        = 7,457,627.1186... -> 7,457,627.12 half-up.
        UK share 0.30/1.18 = 0.254237288135...; -> 2,542,372.88.
        """
        split = self.engine.calculate_profit_split(
            10_000_000.0, [self.dempe_us, self.dempe_uk]
        )
        self.assertEqual(split.split_basis, SPLIT_BASIS_CONTRIBUTION)
        self.assertEqual(split.entity_allocations_usd["ENT-US"], 7_457_627.12)
        self.assertEqual(split.entity_allocations_usd["ENT-UK"], 2_542_372.88)
        self.assertAlmostEqual(split.dempe_percentages["ENT-US"], 74.576271186, places=6)
        self.assertEqual(
            exact_total(split.entity_allocations_usd.values()), Decimal("10000000.00")
        )

    def test_residual_analysis_allocates_routine_returns_first(self):
        """
        Routine returns 500,000 (US) + 1,500,000 (UK) leave an 8,000,000
        residual. US residual share 0.88/1.18 * 8,000,000 = 5,966,101.6949...
        -> 5,966,101.69; plus its 500,000 routine return = 6,466,101.69.
        UK: 0.30/1.18 * 8,000,000 = 2,033,898.3050... -> 2,033,898.31;
        plus 1,500,000 = 3,533,898.31.
        """
        split = self.engine.calculate_profit_split(
            10_000_000.0,
            [self.dempe_us, self.dempe_uk],
            routine_returns_usd={"ENT-US": 500_000.0, "ENT-UK": 1_500_000.0},
        )
        self.assertEqual(split.split_basis, SPLIT_BASIS_RESIDUAL)
        self.assertEqual(split.amount_split_by_key_usd, 8_000_000.0)
        self.assertEqual(split.entity_allocations_usd["ENT-US"], 6_466_101.69)
        self.assertEqual(split.entity_allocations_usd["ENT-UK"], 3_533_898.31)

    def test_residual_and_contribution_analyses_differ(self):
        """A residual split is not the same number as a contribution split."""
        contribution = self.engine.calculate_profit_split(
            10_000_000.0, [self.dempe_us, self.dempe_uk]
        )
        residual = self.engine.calculate_profit_split(
            10_000_000.0,
            [self.dempe_us, self.dempe_uk],
            routine_returns_usd={"ENT-US": 500_000.0, "ENT-UK": 1_500_000.0},
        )
        self.assertNotEqual(
            contribution.entity_allocations_usd["ENT-UK"],
            residual.entity_allocations_usd["ENT-UK"],
        )

    def test_dimension_weights_override_changes_the_key(self):
        """
        Weighting development at 100%: US key 1.0, UK key 0.2, total 1.2.
        US = 1.0/1.2 * 12,000,000 = 10,000,000; UK = 0.2/1.2 * 12,000,000
        = 2,000,000.
        """
        split = self.engine.calculate_profit_split(
            12_000_000.0,
            [self.dempe_us, self.dempe_uk],
            dimension_weights={
                "development": 1.0,
                "enhancement": 0.0,
                "maintenance": 0.0,
                "protection": 0.0,
                "exploitation": 0.0,
            },
        )
        self.assertEqual(split.entity_allocations_usd["ENT-US"], 10_000_000.0)
        self.assertEqual(split.entity_allocations_usd["ENT-UK"], 2_000_000.0)

    def test_incomplete_dimension_weights_are_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(
                1_000_000.0,
                [self.dempe_us, self.dempe_uk],
                dimension_weights={"development": 1.0},
            )

    def test_unknown_dimension_name_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(
                1_000_000.0,
                [self.dempe_us, self.dempe_uk],
                dimension_weights={
                    "development": 1.0,
                    "enhancement": 0.0,
                    "maintenance": 0.0,
                    "protection": 0.0,
                    "exploitation": 0.0,
                    "extraction": 1.0,
                },
            )

    def test_routine_return_for_unknown_entity_is_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(
                1_000_000.0,
                [self.dempe_us, self.dempe_uk],
                routine_returns_usd={"ENT-SG": 100_000.0},
            )

    def test_duplicate_entity_contributions_are_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(
                1_000_000.0, [self.dempe_us, self.dempe_us]
            )

    def test_empty_contributions_are_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(1_000_000.0, [])

    def test_all_zero_weights_are_rejected(self):
        with self.assertRaises(TransferPricingError):
            self.engine.calculate_profit_split(
                1_000_000.0, [DEMPEContribution("ENT-US", 0.0, 0.0, 0.0, 0.0, 0.0)]
            )

    def test_negative_pnl_is_allocated_but_flagged(self):
        split = self.engine.calculate_profit_split(
            -1_180_000.0, [self.dempe_us, self.dempe_uk]
        )
        # 0.88/1.18 * -1,180,000 = -880,000 exactly; 0.30/1.18 -> -300,000.
        self.assertEqual(split.entity_allocations_usd["ENT-US"], -880_000.0)
        self.assertEqual(split.entity_allocations_usd["ENT-UK"], -300_000.0)
        self.assertTrue(any("negative" in w for w in split.warnings))

    def test_three_way_split_reconciles_to_the_total_exactly(self):
        """
        Regression: three independently half-up-rounded shares left a one-cent
        residue, so the allocation legs did not tie back to the profit being
        split and would not post as a balanced intercompany journal.
        """
        sg = DEMPEContribution("ENT-SG", 0.0, 0.1, 0.2, 0.0, 0.4)
        split = self.engine.calculate_profit_split(
            48_750_000.0,
            [self.dempe_us, self.dempe_uk, sg],
            routine_returns_usd={"ENT-UK": 5_390_000.0, "ENT-SG": 1_320_000.0},
            dimension_weights={
                "development": 0.35, "enhancement": 0.25, "maintenance": 0.10,
                "protection": 0.10, "exploitation": 0.20,
            },
        )
        self.assertEqual(
            exact_total(split.entity_allocations_usd.values()), Decimal("48750000.00")
        )

    def test_split_reconciles_across_many_awkward_shares(self):
        """Three equal thirds of a penny-odd amount must still tie to the total."""
        contributions = [
            DEMPEContribution(f"ENT-{i}", 0.5, 0.5, 0.5, 0.5, 0.5) for i in range(3)
        ]
        split = self.engine.calculate_profit_split(1_000_000.01, contributions)
        self.assertEqual(
            exact_total(split.entity_allocations_usd.values()), Decimal("1000000.01")
        )

    def test_routine_returns_exceeding_profit_are_flagged(self):
        split = self.engine.calculate_profit_split(
            1_000_000.0,
            [self.dempe_us, self.dempe_uk],
            routine_returns_usd={"ENT-US": 900_000.0, "ENT-UK": 900_000.0},
        )
        self.assertEqual(split.amount_split_by_key_usd, -800_000.0)
        self.assertTrue(any("exceed combined profits" in w for w in split.warnings))


if __name__ == "__main__":
    unittest.main()
