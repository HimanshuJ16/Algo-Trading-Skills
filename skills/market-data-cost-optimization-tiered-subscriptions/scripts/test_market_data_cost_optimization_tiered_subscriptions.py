import logging
import unittest

from market_data_cost_optimization_tiered_subscriptions import (
    ACTION_DEMOTE,
    ACTION_HOLD_MIN_DWELL,
    ACTION_MAINTAIN,
    ACTION_PROMOTE,
    STATUS_COST_OPTIMIZATION_SUCCESS,
    STATUS_NET_COST_INCREASE,
    STATUS_NO_SAVINGS_FOUND,
    TIER1_DIRECT_L3,
    TIER2_SIP_L1,
    TIER3_DELAYED_EOD,
    MarketDataCostOptimizerEngine,
    SymbolSubscriptionSpec,
)

# Explicit schedule so the tests never depend on the illustrative placeholder
# defaults, and so expected values below are derived by hand rather than by
# re-running the engine's own arithmetic.
SCHEDULE = {
    TIER1_DIRECT_L3: 1000.0,
    TIER2_SIP_L1: 150.0,
    TIER3_DELAYED_EOD: 5.0,
}


def spec(symbol, tier, position=False, signal=False, days=None, dwell=0):
    return SymbolSubscriptionSpec(
        symbol=symbol,
        current_tier=tier,
        has_active_position=position,
        has_active_signal=signal,
        days_since_last_trade=days,
        days_in_current_tier=dwell,
    )


class TestTierSelection(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataCostOptimizerEngine(
            demotion_inactivity_days_threshold=30, tier_monthly_costs_usd=SCHEDULE
        )

    def test_position_and_signal_requires_full_depth(self):
        self.assertEqual(
            self.engine.determine_optimal_tier(
                spec("AAPL", TIER2_SIP_L1, position=True, signal=True, days=0)
            ),
            TIER1_DIRECT_L3,
        )

    def test_live_signal_without_position_never_lands_on_delayed_data(self):
        """Regression: a live signal on a long-dormant symbol used to be demoted to
        TIER3, which would have had the strategy price orders off delayed data."""
        self.assertEqual(
            self.engine.determine_optimal_tier(
                spec("NVDA", TIER1_DIRECT_L3, position=False, signal=True, days=365)
            ),
            TIER2_SIP_L1,
        )

    def test_position_without_signal_keeps_realtime(self):
        self.assertEqual(
            self.engine.determine_optimal_tier(
                spec("MSFT", TIER3_DELAYED_EOD, position=True, signal=False, days=400)
            ),
            TIER2_SIP_L1,
        )

    def test_recent_trade_keeps_realtime_at_threshold_boundary(self):
        # 30 days is inside the window; 31 is outside.
        self.assertEqual(
            self.engine.determine_optimal_tier(spec("KO", TIER2_SIP_L1, days=30)),
            TIER2_SIP_L1,
        )
        self.assertEqual(
            self.engine.determine_optimal_tier(spec("KO", TIER2_SIP_L1, days=31)),
            TIER3_DELAYED_EOD,
        )

    def test_never_traded_symbol_is_stale(self):
        self.assertEqual(
            self.engine.determine_optimal_tier(spec("XYZ", TIER1_DIRECT_L3, days=None)),
            TIER3_DELAYED_EOD,
        )

    def test_zero_threshold_only_keeps_same_day_trades(self):
        engine = MarketDataCostOptimizerEngine(
            demotion_inactivity_days_threshold=0, tier_monthly_costs_usd=SCHEDULE
        )
        self.assertEqual(engine.determine_optimal_tier(spec("F", TIER2_SIP_L1, days=0)), TIER2_SIP_L1)
        self.assertEqual(
            engine.determine_optimal_tier(spec("F", TIER2_SIP_L1, days=1)), TIER3_DELAYED_EOD
        )


class TestCostAudit(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataCostOptimizerEngine(
            demotion_inactivity_days_threshold=30, tier_monthly_costs_usd=SCHEDULE
        )

    def test_demotion_savings_across_a_universe(self):
        # 10 actively worked names stay on TIER1; 90 dormant names drop to TIER3.
        # Hand-derived: baseline 100 * 1000 = 100,000.
        #               optimized 10 * 1000 + 90 * 5 = 10,450.
        #               savings 89,550 -> 89.55% of the symbol-metered base.
        subs = [
            spec(f"ACT_{i}", TIER1_DIRECT_L3, position=True, signal=True, days=1)
            for i in range(10)
        ] + [
            spec(f"INACT_{i}", TIER1_DIRECT_L3, days=60) for i in range(90)
        ]

        report = self.engine.optimize_market_data_costs(subs)

        self.assertEqual(report.status, STATUS_COST_OPTIMIZATION_SUCCESS)
        self.assertEqual(report.total_symbols_audited, 100)
        self.assertEqual(report.demotions_count, 90)
        self.assertEqual(report.promotions_count, 0)
        self.assertEqual(report.dwell_holds_count, 0)
        self.assertEqual(report.baseline_monthly_spend_usd, 100000.0)
        self.assertEqual(report.optimized_monthly_spend_usd, 10450.0)
        self.assertEqual(report.total_monthly_savings_usd, 89550.0)
        self.assertAlmostEqual(report.savings_percentage, 89.55, places=2)
        # No fixed cost declared, so the two percentages coincide.
        self.assertAlmostEqual(report.total_savings_percentage_including_fixed, 89.55, places=2)

    def test_fixed_platform_cost_deflates_the_headline_savings_percentage(self):
        """Per-firm / per-subscriber / non-display fees do not shrink with the symbol
        count, so the reduction in *total* data spend is far smaller than the
        reduction in the symbol-metered slice."""
        engine = MarketDataCostOptimizerEngine(
            demotion_inactivity_days_threshold=30,
            tier_monthly_costs_usd=SCHEDULE,
            fixed_monthly_platform_cost_usd=50000.0,
        )
        subs = [spec(f"INACT_{i}", TIER1_DIRECT_L3, days=60) for i in range(10)]

        report = engine.optimize_market_data_costs(subs)

        # Hand-derived: metered 10,000 -> 50; saved 9,950.
        # Of metered base: 9,950 / 10,000 = 99.50%.
        # Of total base:   9,950 / 60,000 = 16.583... -> 16.58%.
        self.assertEqual(report.total_monthly_savings_usd, 9950.0)
        self.assertAlmostEqual(report.savings_percentage, 99.5, places=2)
        self.assertEqual(report.baseline_total_monthly_spend_usd, 60000.0)
        self.assertEqual(report.optimized_total_monthly_spend_usd, 50050.0)
        self.assertAlmostEqual(report.total_savings_percentage_including_fixed, 16.58, places=2)
        self.assertEqual(report.fixed_monthly_platform_cost_usd, 50000.0)

    def test_promotion_from_delayed_tier(self):
        report = self.engine.optimize_market_data_costs(
            [spec("TSLA", TIER3_DELAYED_EOD, position=True, signal=True, days=0)]
        )
        self.assertEqual(report.promotions_count, 1)
        self.assertEqual(report.decisions[0].action, ACTION_PROMOTE)
        self.assertEqual(report.decisions[0].recommended_tier, TIER1_DIRECT_L3)
        self.assertEqual(report.decisions[0].monthly_savings_usd, -995.0)

    def test_net_cost_increase_is_reported_as_such_not_as_already_optimal(self):
        """Regression: a net increase used to be labelled NO_SAVINGS_FOUND with the
        note 'current subscriptions already optimal', which was false."""
        report = self.engine.optimize_market_data_costs(
            [spec("TSLA", TIER3_DELAYED_EOD, position=True, signal=True, days=0)]
        )
        self.assertEqual(report.status, STATUS_NET_COST_INCREASE)
        self.assertLess(report.total_monthly_savings_usd, 0.0)
        self.assertIn("RISES", report.audit_notes)

    def test_no_change_required(self):
        report = self.engine.optimize_market_data_costs(
            [
                spec("AAPL", TIER1_DIRECT_L3, position=True, signal=True, days=0),
                spec("KO", TIER3_DELAYED_EOD, days=90),
            ]
        )
        self.assertEqual(report.status, STATUS_NO_SAVINGS_FOUND)
        self.assertEqual(report.total_monthly_savings_usd, 0.0)
        self.assertTrue(all(d.action == ACTION_MAINTAIN for d in report.decisions))

    def test_live_signal_symbol_demotes_only_as_far_as_realtime(self):
        """A dormant symbol carrying a live signal may drop off full depth, but the
        demotion must stop at TIER2 and the recorded rationale must not claim the
        symbol has no signal."""
        report = self.engine.optimize_market_data_costs(
            [spec("NVDA", TIER1_DIRECT_L3, signal=True, days=365)]
        )
        decision = report.decisions[0]
        self.assertEqual(decision.action, ACTION_DEMOTE)
        self.assertEqual(decision.recommended_tier, TIER2_SIP_L1)
        self.assertEqual(decision.monthly_savings_usd, 850.0)
        self.assertIn("signal=True", decision.rationale)

    def test_tier_string_is_normalized(self):
        report = self.engine.optimize_market_data_costs(
            [spec("AAPL", "  tier1_direct_l3  ", position=True, signal=True, days=0)]
        )
        self.assertEqual(report.decisions[0].previous_tier, TIER1_DIRECT_L3)
        self.assertEqual(report.decisions[0].action, ACTION_MAINTAIN)

    def test_every_decision_carries_a_rationale(self):
        report = self.engine.optimize_market_data_costs(
            [spec("INACT", TIER1_DIRECT_L3, days=60)]
        )
        self.assertTrue(report.decisions[0].rationale.strip())


class TestDemotionDwellGuard(unittest.TestCase):
    """Exchange and SIP fees are not prorated (UTP Data Policies), so a demotion
    applied inside a billing period saves nothing and costs a full period again on
    re-promotion."""

    def setUp(self):
        self.engine = MarketDataCostOptimizerEngine(
            demotion_inactivity_days_threshold=30,
            tier_monthly_costs_usd=SCHEDULE,
            min_days_before_demotion=31,
        )

    def test_demotion_is_withheld_inside_the_dwell_window(self):
        report = self.engine.optimize_market_data_costs(
            [spec("INACT", TIER1_DIRECT_L3, days=60, dwell=5)]
        )
        self.assertEqual(report.decisions[0].action, ACTION_HOLD_MIN_DWELL)
        self.assertEqual(report.decisions[0].recommended_tier, TIER1_DIRECT_L3)
        self.assertEqual(report.dwell_holds_count, 1)
        self.assertEqual(report.demotions_count, 0)
        self.assertEqual(report.total_monthly_savings_usd, 0.0)

    def test_demotion_proceeds_once_the_dwell_window_is_met(self):
        report = self.engine.optimize_market_data_costs(
            [spec("INACT", TIER1_DIRECT_L3, days=60, dwell=31)]
        )
        self.assertEqual(report.decisions[0].action, ACTION_DEMOTE)
        self.assertEqual(report.decisions[0].recommended_tier, TIER3_DELAYED_EOD)
        self.assertEqual(report.dwell_holds_count, 0)

    def test_dwell_guard_never_withholds_a_promotion(self):
        report = self.engine.optimize_market_data_costs(
            [spec("TSLA", TIER3_DELAYED_EOD, position=True, signal=True, days=0, dwell=0)]
        )
        self.assertEqual(report.decisions[0].action, ACTION_PROMOTE)
        self.assertEqual(report.decisions[0].recommended_tier, TIER1_DIRECT_L3)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = MarketDataCostOptimizerEngine(tier_monthly_costs_usd=SCHEDULE)

    def test_empty_subscription_list_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.optimize_market_data_costs([])

    def test_unknown_tier_is_rejected_not_silently_priced(self):
        """Regression: an unrecognised tier used to be priced at the TIER1 rate and
        ranked as TIER1, inventing baseline spend and therefore phantom savings."""
        with self.assertRaises(ValueError) as ctx:
            self.engine.optimize_market_data_costs([spec("AAPL", "TIER1_DIRECT_L2", days=60)])
        self.assertIn("unknown current_tier", str(ctx.exception))

    def test_duplicate_symbol_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.optimize_market_data_costs(
                [spec("AAPL", TIER1_DIRECT_L3, days=60), spec("AAPL", TIER2_SIP_L1, days=60)]
            )
        self.assertIn("Duplicate symbol", str(ctx.exception))

    def test_blank_symbol_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.optimize_market_data_costs([spec("   ", TIER1_DIRECT_L3, days=1)])

    def test_negative_days_since_last_trade_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.optimize_market_data_costs([spec("AAPL", TIER1_DIRECT_L3, days=-1)])

    def test_negative_dwell_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.optimize_market_data_costs(
                [spec("AAPL", TIER1_DIRECT_L3, days=1, dwell=-3)]
            )

    def test_non_bool_activity_flag_is_rejected(self):
        bad = SymbolSubscriptionSpec("AAPL", TIER1_DIRECT_L3, "yes", False, 1)
        with self.assertRaises(ValueError):
            self.engine.optimize_market_data_costs([bad])

    def test_negative_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            MarketDataCostOptimizerEngine(demotion_inactivity_days_threshold=-1)

    def test_negative_min_dwell_is_rejected(self):
        with self.assertRaises(ValueError):
            MarketDataCostOptimizerEngine(min_days_before_demotion=-1)

    def test_negative_fixed_cost_is_rejected(self):
        with self.assertRaises(ValueError):
            MarketDataCostOptimizerEngine(fixed_monthly_platform_cost_usd=-1.0)

    def test_incomplete_cost_schedule_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            MarketDataCostOptimizerEngine(
                tier_monthly_costs_usd={TIER1_DIRECT_L3: 1.0, TIER2_SIP_L1: 1.0}
            )
        self.assertIn(TIER3_DELAYED_EOD, str(ctx.exception))

    def test_non_finite_cost_is_rejected(self):
        bad = dict(SCHEDULE)
        bad[TIER2_SIP_L1] = float("nan")
        with self.assertRaises(ValueError):
            MarketDataCostOptimizerEngine(tier_monthly_costs_usd=bad)

    def test_negative_cost_is_rejected(self):
        bad = dict(SCHEDULE)
        bad[TIER3_DELAYED_EOD] = -5.0
        with self.assertRaises(ValueError):
            MarketDataCostOptimizerEngine(tier_monthly_costs_usd=bad)


class TestPlaceholderScheduleWarning(unittest.TestCase):
    def test_default_schedule_emits_a_warning(self):
        """The default tier costs have no market basis; using them silently is how a
        fabricated savings figure reaches a budget owner."""
        with self.assertLogs(
            "market_data_cost_optimization_tiered_subscriptions", level=logging.WARNING
        ) as captured:
            MarketDataCostOptimizerEngine()
        self.assertTrue(any("illustrative" in line for line in captured.output))

    def test_explicit_schedule_emits_no_warning(self):
        logger_name = "market_data_cost_optimization_tiered_subscriptions"
        with self.assertLogs(logger_name, level=logging.DEBUG) as captured:
            logging.getLogger(logger_name).debug("probe")
            MarketDataCostOptimizerEngine(tier_monthly_costs_usd=SCHEDULE)
        self.assertEqual(
            [line for line in captured.output if line.startswith("WARNING")], []
        )


if __name__ == "__main__":
    unittest.main()
