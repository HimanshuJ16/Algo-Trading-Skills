import math
import unittest

from margin_call_engine import (
    AccountMarginSnapshot,
    BrokerMarginCallEngine,
    MarginCallError,
    MarginDataError,
    MarginState,
    PositionMarginInfo,
)


def _snapshot(nlv, maintenance, initial=None, excess=None, available=0.0):
    """Snapshot helper; excess liquidity defaults to a healthy positive cushion."""
    return AccountMarginSnapshot(
        net_liquidation_value=nlv,
        initial_margin=maintenance if initial is None else initial,
        maintenance_margin=maintenance,
        excess_liquidity=(nlv - maintenance) if excess is None else excess,
        available_funds=available,
        buying_power=0.0,
    )


class TestBrokerMarginCallEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerMarginCallEngine(
            warning_threshold=0.85, 
            critical_threshold=0.95, 
            breach_threshold=1.00,
            target_post_deleverage_ratio=0.75,
            liquidation_buffer_multiplier=1.0  # Keep at 1.0 for simpler math in tests
        )

    def test_normal_margin_health(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=60000.0,
            maintenance_margin=50000.0, 
            excess_liquidity=50000.0,
            available_funds=40000.0,
            buying_power=160000.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.NORMAL)
        # 50k / 100k = 0.50 margin impact is 0.0 -> OK
        self.assertTrue(self.engine.guard_new_order(snap, margin_impact=0.0, is_deleveraging=False))

    def test_predictive_order_veto(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=80000.0,
            maintenance_margin=70000.0, 
            excess_liquidity=30000.0,
            available_funds=20000.0,
            buying_power=80000.0
        )
        # Current ratio 70%, normal. But order impact +20,000 -> 90%
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.NORMAL)
        
        with self.assertRaises(MarginCallError) as context:
            self.engine.guard_new_order(snap, margin_impact=20000.0, is_deleveraging=False)
        self.assertIn("Projected margin ratio 90.0% exceeds", str(context.exception))

    def test_critical_tier_evaluation(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=98000.0,
            maintenance_margin=96000.0, 
            excess_liquidity=4000.0,
            available_funds=2000.0,
            buying_power=8000.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.CRITICAL)
        self.assertEqual(res.action_required, "CANCEL_ALL_PENDING_ORDERS")

    def test_deleveraging_plan_generation_with_priority(self):
        # 105% margin ratio -> BREACH
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=110000.0,
            maintenance_margin=105000.0, 
            excess_liquidity=-5000.0,
            available_funds=-10000.0,
            buying_power=0.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)

        positions = [
            PositionMarginInfo(
                symbol="AAPL", asset_class="STK", quantity=100, current_price=150.0, 
                maintenance_margin_requirement=3000.0, average_daily_volume=50_000_000
            ),
            PositionMarginInfo(
                symbol="SPX_PUT_SHORT", asset_class="OPT", quantity=-5, current_price=20.0, 
                maintenance_margin_requirement=50000.0, is_short_option=True, average_daily_volume=1_000_000
            ),
            PositionMarginInfo(
                symbol="TSLA", asset_class="STK", quantity=200, current_price=200.0, 
                maintenance_margin_requirement=20000.0, average_daily_volume=10_000_000
            ),
        ]

        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertTrue(len(plan) > 0)
        
        # Should prioritize short options (SPX_PUT_SHORT first) due to highest tail risk
        self.assertEqual(plan[0][0].symbol, "SPX_PUT_SHORT")
        
        # Target reduction = 105000 - (100000 * 0.75) = 105000 - 75000 = 30000
        # SPX_PUT_SHORT has 50000 margin for 5 units = 10000 per unit.
        # It needs to liquidate 3 units to hit 30000.
        self.assertAlmostEqual(plan[0][1], 3.0)

    def test_liquidity_cap_during_deleveraging(self):
        # 100k NLV, 150k Maint Margin = 150% ratio. Target 75% -> need 75k reduction.
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=150000.0,
            maintenance_margin=150000.0, 
            excess_liquidity=-50000.0,
            available_funds=-50000.0,
            buying_power=0.0
        )
        # Position is illiquid. ADV = 100. Max participation = 10% = 10 units.
        positions = [
             PositionMarginInfo(
                symbol="ILLIQUID_CORP", asset_class="STK", quantity=500, current_price=10.0, 
                maintenance_margin_requirement=150000.0, average_daily_volume=100
            )
        ]
        
        # 150k margin / 500 units = 300 margin/unit. 
        # Need 75k reduction -> would need 250 units. 
        # But max participation caps at 10% of ADV(100) = 10 units.
        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertEqual(plan[0][1], 10.0)

class TestUnusableInputFailsClosed(unittest.TestCase):
    """
    Every threshold comparison against NaN is False, so a NaN ratio falls through
    the whole if/elif chain into the healthy branch. A margin engine must not
    report NORMAL because its feed broke.
    """

    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_nan_nlv_raises_instead_of_reporting_healthy(self):
        with self.assertRaises(MarginDataError):
            self.engine.evaluate_margin_health(_snapshot(float("nan"), 100_000.0))

    def test_nan_maintenance_margin_raises_instead_of_reporting_healthy(self):
        with self.assertRaises(MarginDataError):
            self.engine.evaluate_margin_health(_snapshot(100_000.0, float("nan")))

    def test_infinite_values_raise(self):
        with self.assertRaises(MarginDataError):
            self.engine.evaluate_margin_health(_snapshot(float("inf"), 100_000.0))

    def test_negative_maintenance_margin_raises(self):
        with self.assertRaises(MarginDataError):
            self.engine.evaluate_margin_health(_snapshot(100_000.0, -1.0))

    def test_nan_margin_impact_does_not_approve_the_order(self):
        """Regression: a NaN projected ratio passed every veto test and returned True."""
        snap = _snapshot(100_000.0, 50_000.0)
        with self.assertRaises(MarginDataError):
            self.engine.guard_new_order(snap, margin_impact=float("nan"))


class TestNonPositiveEquity(unittest.TestCase):
    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_negative_nlv_reports_the_true_deficit(self):
        """
        Regression: NLV was floored at 0.01 and the deficit computed from the
        floored value, understating it by exactly the negative equity. With
        NLV = -50,000 and maintenance margin 100,000 the true deficit is 150,000;
        the old code reported 99,999.99.
        """
        res = self.engine.evaluate_margin_health(
            _snapshot(-50_000.0, 100_000.0, excess=-150_000.0)
        )
        self.assertEqual(res.maintenance_deficit, 150_000.0)
        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)
        self.assertEqual(res.action_required, "HALT_AND_ESCALATE")
        self.assertEqual(res.maintenance_margin_ratio, math.inf)

    def test_zero_nlv_is_a_breach_not_normal(self):
        """Regression: NLV=0 with zero margin produced ratio 0.0 and state NORMAL."""
        res = self.engine.evaluate_margin_health(_snapshot(0.0, 0.0, excess=0.0))
        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)
        self.assertEqual(res.action_required, "HALT_AND_ESCALATE")

    def test_deleveraging_with_non_positive_nlv_targets_full_unwind(self):
        snap = _snapshot(-10_000.0, 80_000.0, excess=-90_000.0)
        positions = [
            PositionMarginInfo(
                symbol="ABC", asset_class="STK", quantity=1000, current_price=10.0,
                maintenance_margin_requirement=80_000.0, average_daily_volume=1_000_000,
            )
        ]
        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0][1], 1000.0)  # entire position


class TestBrokerCushionOverridesHouseRatio(unittest.TestCase):
    """
    Excess Liquidity is Equity with Loan Value minus maintenance margin, and ELV
    is not NLV. An account holding non-marginable assets can look healthy on
    maintenance/NLV while the broker's own cushion is already negative — which is
    the condition under which positions actually get liquidated.
    """

    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_negative_excess_liquidity_is_a_breach_despite_a_healthy_ratio(self):
        # maintenance/NLV = 70k/100k = 0.70, comfortably NORMAL on the house ratio.
        # But ELV is only 60k, so excess liquidity is -10k and the broker is in
        # deficiency. Regression: this reported NORMAL.
        snap = _snapshot(100_000.0, 70_000.0, excess=-10_000.0)

        res = self.engine.evaluate_margin_health(snap)

        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)
        self.assertTrue(res.broker_deficiency)
        self.assertAlmostEqual(res.maintenance_margin_ratio, 0.70)

    def test_positive_excess_liquidity_leaves_the_house_ratio_in_charge(self):
        res = self.engine.evaluate_margin_health(_snapshot(100_000.0, 50_000.0))
        self.assertEqual(res.state, MarginState.NORMAL)
        self.assertFalse(res.broker_deficiency)

    def test_order_is_vetoed_when_the_broker_cushion_is_negative(self):
        snap = _snapshot(100_000.0, 70_000.0, excess=-10_000.0)
        with self.assertRaises(MarginCallError):
            self.engine.guard_new_order(snap, margin_impact=0.0)


class TestThresholdConfiguration(unittest.TestCase):
    def test_unordered_thresholds_rejected(self):
        """Regression: warning above critical silently made the WARNING tier unreachable."""
        with self.assertRaises(MarginDataError):
            BrokerMarginCallEngine(warning_threshold=0.95, critical_threshold=0.85)

    def test_deleverage_target_at_or_above_breach_rejected(self):
        """Regression: a target of 1.5 aimed the recovery at a state that is itself a breach."""
        for bad in (1.0, 1.5):
            with self.assertRaises(MarginDataError):
                BrokerMarginCallEngine(target_post_deleverage_ratio=bad)

    def test_invalid_participation_rate_rejected(self):
        for bad in (0.0, -0.1, 1.5):
            with self.assertRaises(MarginDataError):
                BrokerMarginCallEngine(max_participation_rate=bad)

    def test_buffer_multiplier_below_one_rejected(self):
        with self.assertRaises(MarginDataError):
            BrokerMarginCallEngine(liquidation_buffer_multiplier=0.9)


class TestTierBoundaries(unittest.TestCase):
    """Thresholds are inclusive lower bounds; verify each exact boundary."""

    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_exact_boundaries(self):
        cases = [
            (84_999.0, MarginState.NORMAL),
            (85_000.0, MarginState.WARNING),
            (94_999.0, MarginState.WARNING),
            (95_000.0, MarginState.CRITICAL),
            (99_999.0, MarginState.CRITICAL),
            (100_000.0, MarginState.MARGIN_CALL_BREACH),
        ]
        for maintenance, expected in cases:
            with self.subTest(maintenance=maintenance):
                # Excess liquidity held positive so the house ratio is the only driver.
                res = self.engine.evaluate_margin_health(
                    _snapshot(100_000.0, maintenance, excess=1.0)
                )
                self.assertEqual(res.state, expected)


class TestDeleveragingOrderGuard(unittest.TestCase):
    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_deleveraging_flag_cannot_smuggle_a_margin_increasing_order(self):
        """
        Regression: is_deleveraging=True returned True unconditionally, so an
        order with a +999,999 margin impact bypassed every gate.
        """
        snap = _snapshot(100_000.0, 50_000.0)
        with self.assertRaises(MarginDataError):
            self.engine.guard_new_order(snap, margin_impact=999_999.0, is_deleveraging=True)

    def test_genuine_deleveraging_order_is_allowed_even_in_breach(self):
        snap = _snapshot(100_000.0, 120_000.0, excess=-20_000.0)
        self.assertTrue(
            self.engine.guard_new_order(snap, margin_impact=-30_000.0, is_deleveraging=True)
        )


class TestInitialMarginGate(unittest.TestCase):
    """
    New positions are opened against initial margin (Reg T: 50% on a long margin
    equity purchase) not maintenance margin (FINRA 4210: 25% minimum), so a
    maintenance-only projection is the weaker constraint.
    """

    def setUp(self):
        self.engine = BrokerMarginCallEngine()

    def test_order_exceeding_available_funds_is_vetoed(self):
        # Maintenance projection alone would pass: 50k + 10k = 60k on 100k NLV = 60%.
        snap = _snapshot(100_000.0, 50_000.0, available=8_000.0)
        with self.assertRaises(MarginCallError) as ctx:
            self.engine.guard_new_order(
                snap, margin_impact=10_000.0, initial_margin_impact=20_000.0
            )
        self.assertIn("Initial margin impact", str(ctx.exception))

    def test_order_within_available_funds_passes(self):
        snap = _snapshot(100_000.0, 50_000.0, available=40_000.0)
        self.assertTrue(
            self.engine.guard_new_order(
                snap, margin_impact=10_000.0, initial_margin_impact=20_000.0
            )
        )


class TestDeleveragingArithmetic(unittest.TestCase):
    def test_required_reduction_includes_the_slippage_buffer(self):
        """Independently computed: buffer multiplier must scale the reduction."""
        engine = BrokerMarginCallEngine(liquidation_buffer_multiplier=1.05)
        # ratio = 220k/200k = 1.10 -> BREACH. target = 200k * 0.75 = 150k.
        # base reduction = 220k - 150k = 70k; with buffer = 70k * 1.05 = 73.5k.
        # margin per unit = 220k / 1000 = 220. units = 73500 / 220 = 334.0909...
        snap = _snapshot(200_000.0, 220_000.0, excess=-20_000.0)
        positions = [
            PositionMarginInfo(
                symbol="XYZ", asset_class="STK", quantity=1000, current_price=100.0,
                maintenance_margin_requirement=220_000.0, average_daily_volume=1_000_000,
            )
        ]
        plan = engine.plan_deleveraging(snap, positions)
        self.assertAlmostEqual(plan[0][1], 73_500.0 / 220.0, places=6)

    def test_no_plan_when_not_in_breach(self):
        engine = BrokerMarginCallEngine()
        snap = _snapshot(100_000.0, 50_000.0)
        self.assertEqual(engine.plan_deleveraging(snap, []), [])

    def test_position_with_unusable_values_raises(self):
        engine = BrokerMarginCallEngine()
        snap = _snapshot(100_000.0, 120_000.0, excess=-20_000.0)
        bad = [
            PositionMarginInfo(
                symbol="BAD", asset_class="STK", quantity=float("nan"), current_price=10.0,
                maintenance_margin_requirement=120_000.0, average_daily_volume=1_000.0,
            )
        ]
        with self.assertRaises(MarginDataError):
            engine.plan_deleveraging(snap, bad)

    def test_negative_adv_raises_rather_than_sizing_against_it(self):
        engine = BrokerMarginCallEngine()
        snap = _snapshot(100_000.0, 120_000.0, excess=-20_000.0)
        bad = [
            PositionMarginInfo(
                symbol="BAD", asset_class="STK", quantity=100, current_price=10.0,
                maintenance_margin_requirement=120_000.0, average_daily_volume=-5.0,
            )
        ]
        with self.assertRaises(MarginDataError):
            engine.plan_deleveraging(snap, bad)


if __name__ == "__main__":
    unittest.main()
