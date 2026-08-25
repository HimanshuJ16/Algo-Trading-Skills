"""Unit tests for the venue fee-tier allocation optimizer.

Every monetary expectation below is derived by hand from the tier rates in the
fixture, not by re-running the engine's own arithmetic.
"""

import unittest

from execution_venue_fee_tier_optimization import (
    ExecutionVenueFeeTierOptimizerEngine,
    ExecutionVenueSpec,
    TierBenefitPeriod,
    TierQualificationBasis,
    UnfilledPassivePolicy,
    VenueFeeTier,
    VenueOptimizationError,
)

# NASDAQ-style maker-taker schedule: Tier 1 from 0 shares, Tier 2 VIP from 15M.
NASDAQ_TIERS = [
    VenueFeeTier("Tier 1", 0, taker_rate_per_share=0.0030, maker_rate_per_share=-0.0020),
    VenueFeeTier("Tier 2 VIP", 15_000_000, taker_rate_per_share=0.0025, maker_rate_per_share=-0.0028),
]
EDGX_TIERS = [
    VenueFeeTier("Tier 1", 0, taker_rate_per_share=0.0030, maker_rate_per_share=-0.0022),
    VenueFeeTier("Tier 2 VIP", 15_000_000, taker_rate_per_share=0.0025, maker_rate_per_share=-0.0029),
]


def rolling_engine(**kwargs):
    return ExecutionVenueFeeTierOptimizerEngine(TierQualificationBasis.ROLLING_CURRENT, **kwargs)


def prior_period_engine(**kwargs):
    return ExecutionVenueFeeTierOptimizerEngine(TierQualificationBasis.PRIOR_PERIOD, **kwargs)


class TestVenueCostArithmetic(unittest.TestCase):
    """Per-venue expected economics, checked against hand-computed values."""

    def test_all_aggressive_flow_pays_the_taker_rate_of_the_qualified_tier(self):
        # 30,000,000 shares removed, all executed -> Tier 2 VIP taker rate $0.0025.
        # 30,000,000 * 0.0025 = $75,000 charged.
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        bd = rolling_engine().calculate_venue_net_cost(venue, 30_000_000, maker_ratio=0.0)
        self.assertEqual(bd.active_tier_name, "Tier 2 VIP")
        self.assertEqual(bd.aggressive_shares, 30_000_000)
        self.assertEqual(bd.posted_passive_shares, 0)
        self.assertEqual(bd.taker_side_cost_usd, 75_000.00)
        self.assertEqual(bd.net_cost_usd, 75_000.00)

    def test_fully_filling_passive_flow_earns_the_full_vip_rebate(self):
        # 30,000,000 posted, fill probability 1.0 -> 30,000,000 * -0.0028 = -$84,000.
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        bd = rolling_engine().calculate_venue_net_cost(venue, 30_000_000, maker_ratio=1.0)
        self.assertEqual(bd.expected_passive_fills, 30_000_000.0)
        self.assertEqual(bd.maker_side_cost_usd, -84_000.00)
        self.assertEqual(bd.net_cost_usd, -84_000.00)
        self.assertEqual(bd.gross_maker_rebates_usd, 84_000.00)

    def test_partial_passive_fill_is_swept_at_the_taker_rate(self):
        # 30,000,000 posted at p = 0.5 -> 15,000,000 fill, 15,000,000 swept.
        # maker: 15,000,000 * -0.0028 = -$42,000
        # taker: 15,000,000 * +0.0025 = +$37,500
        # net  :                         -$4,500
        venue = ExecutionVenueSpec("NASDAQ", 0.5, NASDAQ_TIERS)
        bd = rolling_engine().calculate_venue_net_cost(venue, 30_000_000, maker_ratio=1.0)
        self.assertEqual(bd.expected_passive_fills, 15_000_000.0)
        self.assertEqual(bd.swept_shares, 15_000_000.0)
        self.assertEqual(bd.maker_side_cost_usd, -42_000.00)
        self.assertEqual(bd.taker_side_cost_usd, 37_500.00)
        self.assertEqual(bd.net_cost_usd, -4_500.00)

    def test_abandon_policy_charges_an_explicit_opportunity_cost(self):
        # 30,000,000 posted at p = 0.5, ABANDON, opportunity cost $0.01/share.
        # maker      : 15,000,000 * -0.0028 = -$42,000  (15M executed meets the 15M tier)
        # opportunity: 15,000,000 *  0.01   = +$150,000
        # net        :                         +$108,000
        venue = ExecutionVenueSpec("NASDAQ", 0.5, NASDAQ_TIERS)
        engine = rolling_engine(
            unfilled_passive_policy=UnfilledPassivePolicy.ABANDON,
            unfilled_passive_opportunity_cost_per_share=0.01,
        )
        bd = engine.calculate_venue_net_cost(venue, 30_000_000, maker_ratio=1.0)
        self.assertEqual(bd.swept_shares, 0.0)
        self.assertEqual(bd.executed_shares, 15_000_000.0)
        self.assertEqual(bd.opportunity_cost_usd, 150_000.00)
        self.assertEqual(bd.net_cost_usd, 108_000.00)

    def test_tier_threshold_is_inclusive(self):
        # Exactly 15,000,000 executed shares reaches the 15,000,000-share tier.
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        engine = rolling_engine()
        self.assertEqual(engine.calculate_venue_net_cost(venue, 15_000_000, 0.0).active_tier_name, "Tier 2 VIP")
        self.assertEqual(engine.calculate_venue_net_cost(venue, 14_999_999, 0.0).active_tier_name, "Tier 1")

    def test_zero_allocation_is_costless_and_uses_the_base_tier(self):
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        bd = rolling_engine().calculate_venue_net_cost(venue, 0, maker_ratio=0.70)
        self.assertEqual(bd.net_cost_usd, 0.0)
        self.assertEqual(bd.active_tier_name, "Tier 1")


class TestFillProbabilityRegression(unittest.TestCase):
    """Regression: fill probability must enter the economics, not merely gate them."""

    def test_high_rebate_low_fill_venue_loses_to_low_rebate_high_fill_venue(self):
        # TRAP  : p = 0.10, maker -$0.0090, taker +$0.0030
        #         10,000,000 posted -> 1,000,000 fill, 9,000,000 swept
        #         1,000,000 * -0.0090 = -$9,000 ; 9,000,000 * 0.0030 = +$27,000 -> +$18,000
        # SOLID : p = 1.00, maker -$0.0020, taker +$0.0030
        #         10,000,000 * -0.0020 = -$20,000
        # A model that ignores fill probability scores TRAP at -$90,000 and picks it.
        trap = ExecutionVenueSpec("TRAP", 0.10, [VenueFeeTier("Flat", 0, 0.0030, -0.0090)])
        solid = ExecutionVenueSpec("SOLID", 1.00, [VenueFeeTier("Flat", 0, 0.0030, -0.0020)])
        engine = rolling_engine(min_weighted_passive_fill_probability=0.0)

        self.assertEqual(engine.calculate_venue_net_cost(trap, 10_000_000, 1.0).net_cost_usd, 18_000.00)
        self.assertEqual(engine.calculate_venue_net_cost(solid, 10_000_000, 1.0).net_cost_usd, -20_000.00)

        report = engine.optimize_venue_fee_allocation([trap, solid], 10_000_000, maker_ratio=1.0)
        self.assertEqual(report.optimal_strategy.strategy_name, "CONCENTRATED_SOLID")
        self.assertEqual(report.optimal_strategy.total_net_cost_usd, -20_000.00)


class TestFillProbabilityConstraint(unittest.TestCase):
    """The fill-probability floor must be enforced, never silently disabled."""

    def test_infeasible_constraint_raises_instead_of_returning_an_unsafe_allocation(self):
        trap = ExecutionVenueSpec("TRAP", 0.10, [VenueFeeTier("Flat", 0, 0.0030, -0.0090)])
        engine = rolling_engine(min_weighted_passive_fill_probability=0.80)
        with self.assertRaises(VenueOptimizationError) as ctx:
            engine.optimize_venue_fee_allocation([trap], 10_000_000, maker_ratio=0.70)
        self.assertIn("No candidate allocation satisfies the constraints", str(ctx.exception))

    def test_rejected_candidates_are_reported_with_a_reason(self):
        trap = ExecutionVenueSpec("TRAP", 0.10, [VenueFeeTier("Flat", 0, 0.0030, -0.0090)])
        nasdaq = ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS)
        report = rolling_engine().optimize_venue_fee_allocation([nasdaq, trap], 30_000_000, 0.70)

        rejected_names = {r.strategy_name for r in report.rejected_strategies}
        self.assertIn("CONCENTRATED_TRAP", rejected_names)
        self.assertEqual(report.optimal_strategy.strategy_name, "CONCENTRATED_NASDAQ")
        for rejected in report.rejected_strategies:
            self.assertTrue(rejected.reason)
        self.assertTrue(
            any("rejected by hard constraints" in w for w in report.warnings),
            report.warnings,
        )

    def test_all_aggressive_flow_is_not_gated_by_passive_fill_probability(self):
        # With no passive volume there is nothing for the fill-probability floor to bind on.
        trap = ExecutionVenueSpec("TRAP", 0.10, [VenueFeeTier("Flat", 0, 0.0030, -0.0090)])
        report = rolling_engine().optimize_venue_fee_allocation([trap], 10_000_000, maker_ratio=0.0)
        self.assertEqual(report.optimal_strategy.weighted_passive_fill_probability, 1.0)
        self.assertEqual(report.optimal_strategy.total_net_cost_usd, 30_000.00)


class TestTierQualificationBasis(unittest.TestCase):
    """Reg NMS Rule 610(d): on US NMS stocks the tier comes from the prior period."""

    def test_prior_period_tier_comes_from_prior_volume_not_from_routed_volume(self):
        # Prior period 20,000,000 shares -> Tier 2 VIP applies even though only
        # 1,000,000 shares are routed now: 1,000,000 * -0.0028 = -$2,800.
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS, qualifying_volume_shares=20_000_000)
        bd = prior_period_engine().calculate_venue_net_cost(venue, 1_000_000, maker_ratio=1.0)
        self.assertEqual(bd.active_tier_name, "Tier 2 VIP")
        self.assertEqual(bd.net_cost_usd, -2_800.00)

    def test_prior_period_routing_cannot_buy_a_better_current_rate(self):
        # Prior period 5,000,000 shares -> Tier 1, however much is routed now.
        # 30,000,000 * -0.0020 = -$60,000, not the -$84,000 the VIP tier would pay.
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS, qualifying_volume_shares=5_000_000)
        bd = prior_period_engine().calculate_venue_net_cost(venue, 30_000_000, maker_ratio=1.0)
        self.assertEqual(bd.active_tier_name, "Tier 1")
        self.assertEqual(bd.net_cost_usd, -60_000.00)
        # ...but it does set up next period's tier.
        self.assertEqual(bd.projected_next_period_tier_name, "Tier 2 VIP")

    def test_rolling_current_tier_comes_from_executed_volume(self):
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        bd = rolling_engine().calculate_venue_net_cost(venue, 30_000_000, maker_ratio=1.0)
        self.assertEqual(bd.active_tier_name, "Tier 2 VIP")
        self.assertEqual(bd.qualifying_volume_shares, 30_000_000)

    def test_prior_period_without_qualifying_volume_raises(self):
        venue = ExecutionVenueSpec("NASDAQ", 1.0, NASDAQ_TIERS)
        with self.assertRaises(VenueOptimizationError) as ctx:
            prior_period_engine().calculate_venue_net_cost(venue, 1_000_000, maker_ratio=0.70)
        self.assertIn("qualifying_volume_shares", str(ctx.exception))

    def test_benefit_period_and_warning_track_the_basis(self):
        nasdaq = ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS, qualifying_volume_shares=20_000_000)
        prior = prior_period_engine().optimize_venue_fee_allocation([nasdaq], 30_000_000, 0.70)
        self.assertIs(prior.tier_benefit_period, TierBenefitPeriod.NEXT_PERIOD)
        self.assertTrue(any("NEXT_PERIOD" in w for w in prior.warnings), prior.warnings)

        rolling = rolling_engine().optimize_venue_fee_allocation(
            [ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS)], 30_000_000, 0.70
        )
        self.assertIs(rolling.tier_benefit_period, TierBenefitPeriod.CURRENT_PERIOD)

    def test_basis_is_required_and_typed(self):
        with self.assertRaises(VenueOptimizationError):
            ExecutionVenueFeeTierOptimizerEngine("ROLLING_CURRENT")  # string, not the enum
        with self.assertRaises(TypeError):
            ExecutionVenueFeeTierOptimizerEngine()  # no default basis


class TestSavingsReporting(unittest.TestCase):
    """Savings must be measured against the incumbent routing table, not a strawman."""

    def setUp(self):
        self.nasdaq = ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS)
        self.edgx = ExecutionVenueSpec("EDGX", 0.90, EDGX_TIERS)
        self.engine = rolling_engine()

    def test_savings_are_none_without_an_explicit_baseline(self):
        report = self.engine.optimize_venue_fee_allocation([self.nasdaq, self.edgx], 30_000_000, 0.70)
        self.assertIsNone(report.net_savings_vs_baseline_usd)
        self.assertIsNone(report.baseline_strategy)
        self.assertTrue(any("No baseline_allocation supplied" in w for w in report.warnings))

    def test_savings_equal_baseline_cost_minus_optimal_cost(self):
        baseline = {"NASDAQ": 15_000_000, "EDGX": 15_000_000}
        report = self.engine.optimize_venue_fee_allocation(
            [self.nasdaq, self.edgx], 30_000_000, 0.70, baseline_allocation=baseline
        )
        self.assertIsNotNone(report.baseline_strategy)
        self.assertAlmostEqual(
            report.net_savings_vs_baseline_usd,
            round(report.baseline_strategy.total_net_cost_usd - report.optimal_strategy.total_net_cost_usd, 2),
            places=2,
        )
        self.assertGreater(report.net_savings_vs_baseline_usd, 0.0)

    def test_baseline_already_optimal_reports_no_improvement_rather_than_a_positive_number(self):
        baseline = {"NASDAQ": 30_000_000}
        report = self.engine.optimize_venue_fee_allocation(
            [self.nasdaq, self.edgx], 30_000_000, 0.70, baseline_allocation=baseline
        )
        self.assertEqual(report.net_savings_vs_baseline_usd, 0.0)
        self.assertTrue(any("Do not re-route" in w for w in report.warnings), report.warnings)

    def test_baseline_must_sum_to_the_volume_budget(self):
        with self.assertRaises(VenueOptimizationError) as ctx:
            self.engine.optimize_venue_fee_allocation(
                [self.nasdaq, self.edgx], 30_000_000, 0.70,
                baseline_allocation={"NASDAQ": 10_000_000},
            )
        self.assertIn("not comparable", str(ctx.exception))

    def test_baseline_referencing_an_unknown_venue_raises(self):
        with self.assertRaises(VenueOptimizationError):
            self.engine.optimize_venue_fee_allocation(
                [self.nasdaq], 30_000_000, 0.70, baseline_allocation={"IEX": 30_000_000}
            )


class TestAllocationIntegrity(unittest.TestCase):
    """Every candidate must allocate the full budget, deterministically."""

    def setUp(self):
        self.venues = [
            ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS),
            ExecutionVenueSpec("EDGX", 0.90, EDGX_TIERS),
            ExecutionVenueSpec("BZX", 0.88, EDGX_TIERS),
        ]

    def test_every_candidate_allocates_exactly_the_budget(self):
        # 10,000,000 / 3 is not an integer: floor division would drop a share.
        report = rolling_engine().optimize_venue_fee_allocation(self.venues, 10_000_000, 0.70)
        for strategy in report.all_strategies_evaluated:
            self.assertEqual(sum(strategy.volume_allocations_shares.values()), 10_000_000, strategy.strategy_name)
        for rejected in report.rejected_strategies:
            self.assertEqual(sum(rejected.volume_allocations_shares.values()), 10_000_000, rejected.strategy_name)

    def test_result_does_not_depend_on_venue_input_order(self):
        forward = rolling_engine().optimize_venue_fee_allocation(self.venues, 30_000_000, 0.70)
        reverse = rolling_engine().optimize_venue_fee_allocation(list(reversed(self.venues)), 30_000_000, 0.70)
        self.assertEqual(
            forward.optimal_strategy.volume_allocations_shares,
            reverse.optimal_strategy.volume_allocations_shares,
        )
        self.assertEqual(forward.optimal_strategy.total_net_cost_usd, reverse.optimal_strategy.total_net_cost_usd)

    def test_tiers_are_sorted_regardless_of_input_order(self):
        venue = ExecutionVenueSpec("NASDAQ", 1.0, list(reversed(NASDAQ_TIERS)))
        self.assertEqual([t.min_volume_shares for t in venue.tiers], [0, 15_000_000])

    def test_capacity_cap_rejects_an_oversized_allocation(self):
        capped = ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS, max_allocatable_shares=20_000_000)
        edgx = ExecutionVenueSpec("EDGX", 0.90, EDGX_TIERS)
        report = rolling_engine().optimize_venue_fee_allocation([capped, edgx], 30_000_000, 0.70)
        self.assertIn(
            "CONCENTRATED_NASDAQ", {r.strategy_name for r in report.rejected_strategies}
        )
        self.assertLessEqual(
            report.optimal_strategy.volume_allocations_shares.get("NASDAQ", 0), 20_000_000
        )

    def test_liquidity_weighted_candidate_is_generated(self):
        report = rolling_engine().optimize_venue_fee_allocation(self.venues, 30_000_000, 0.70)
        names = {s.strategy_name for s in report.all_strategies_evaluated} | {
            r.strategy_name for r in report.rejected_strategies
        }
        self.assertIn("LIQUIDITY_WEIGHTED", names)
        self.assertIn("EQUAL_SPLIT_BALANCED", names)


class TestInputValidation(unittest.TestCase):
    """Invalid input must raise, never produce a plausible-looking number."""

    def setUp(self):
        self.nasdaq = ExecutionVenueSpec("NASDAQ", 0.95, NASDAQ_TIERS)
        self.engine = rolling_engine()

    def test_maker_ratio_outside_zero_to_one_raises(self):
        # A ratio above 1.0 previously produced negative aggressive volume and
        # fabricated rebates on shares that do not exist.
        for bad_ratio in (1.7, -0.1, float("nan"), float("inf")):
            with self.subTest(maker_ratio=bad_ratio):
                with self.assertRaises(VenueOptimizationError):
                    self.engine.optimize_venue_fee_allocation([self.nasdaq], 1_000_000, bad_ratio)

    def test_maker_ratio_boundaries_are_accepted(self):
        for ratio in (0.0, 1.0):
            with self.subTest(maker_ratio=ratio):
                self.engine.optimize_venue_fee_allocation([self.nasdaq], 1_000_000, ratio)

    def test_non_positive_or_non_integer_volume_raises(self):
        for bad_volume in (0, -1, 1_000_000.5):
            with self.subTest(total_volume_shares=bad_volume):
                with self.assertRaises(VenueOptimizationError):
                    self.engine.optimize_venue_fee_allocation([self.nasdaq], bad_volume, 0.70)

    def test_empty_or_duplicate_venue_list_raises(self):
        with self.assertRaises(VenueOptimizationError):
            self.engine.optimize_venue_fee_allocation([], 1_000_000, 0.70)
        with self.assertRaises(VenueOptimizationError) as ctx:
            self.engine.optimize_venue_fee_allocation([self.nasdaq, self.nasdaq], 1_000_000, 0.70)
        self.assertIn("duplicate venue_id", str(ctx.exception))

    def test_schedule_without_a_zero_threshold_tier_raises(self):
        with self.assertRaises(VenueOptimizationError) as ctx:
            ExecutionVenueSpec("X", 0.9, [VenueFeeTier("Tier 2", 15_000_000, 0.0025, -0.0028)])
        self.assertIn("threshold 0", str(ctx.exception))

    def test_empty_schedule_raises(self):
        with self.assertRaises(VenueOptimizationError):
            ExecutionVenueSpec("X", 0.9, [])

    def test_duplicate_tier_thresholds_raise(self):
        with self.assertRaises(VenueOptimizationError) as ctx:
            ExecutionVenueSpec(
                "X", 0.9,
                [VenueFeeTier("A", 0, 0.0030, -0.0020), VenueFeeTier("B", 0, 0.0025, -0.0028)],
            )
        self.assertIn("duplicate tier thresholds", str(ctx.exception))

    def test_non_finite_rates_raise(self):
        for bad_rate in (float("nan"), float("inf")):
            with self.subTest(rate=bad_rate):
                with self.assertRaises(VenueOptimizationError):
                    VenueFeeTier("A", 0, bad_rate, -0.0020)
                with self.assertRaises(VenueOptimizationError):
                    VenueFeeTier("A", 0, 0.0030, bad_rate)

    def test_negative_tier_threshold_raises(self):
        with self.assertRaises(VenueOptimizationError):
            VenueFeeTier("A", -1, 0.0030, -0.0020)

    def test_fill_probability_outside_zero_to_one_raises(self):
        for bad_p in (-0.01, 1.01, float("nan")):
            with self.subTest(p=bad_p):
                with self.assertRaises(VenueOptimizationError):
                    ExecutionVenueSpec("X", bad_p, NASDAQ_TIERS)

    def test_negative_opportunity_cost_raises(self):
        with self.assertRaises(VenueOptimizationError):
            rolling_engine(unfilled_passive_opportunity_cost_per_share=-0.01)

    def test_fill_probability_threshold_outside_zero_to_one_raises(self):
        with self.assertRaises(VenueOptimizationError):
            rolling_engine(min_weighted_passive_fill_probability=1.5)

    def test_zero_opportunity_cost_under_abandon_policy_warns(self):
        engine = rolling_engine(unfilled_passive_policy=UnfilledPassivePolicy.ABANDON)
        report = engine.optimize_venue_fee_allocation([self.nasdaq], 10_000_000, 0.70)
        self.assertTrue(
            any("zero opportunity cost" in w for w in report.warnings), report.warnings
        )


if __name__ == "__main__":
    unittest.main()
