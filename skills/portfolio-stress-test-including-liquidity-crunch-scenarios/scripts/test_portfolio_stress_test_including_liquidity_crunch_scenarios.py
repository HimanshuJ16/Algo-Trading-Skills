"""
Behaviour tests for the portfolio liquidity-crunch stress engine.

Every expected figure below is derived by hand from the model's stated formulas, not
by re-running the implementation's own arithmetic:

    price_shock_loss = -(Q * P * shock)
    spread_cost      = 0.5 * (spread_bps * expansion / 10_000) * |Q| * P
    market_impact    = Y * sigma * sqrt(|Q| / stressed_adv) * |Q| * P
    DTL              = |Q| / (participation * adv * (1 - liquidity_drop))
"""
import math
import unittest

from portfolio_stress_test_including_liquidity_crunch_scenarios import (
    Config,
    PortfolioStressTestEngine,
    PortfolioPosition,
    PositionStressResult,
    StressScenario,
    StressTestReport,
    STATUS_PASSED,
    STATUS_ILLIQUID_WARNING,
    IMPACT_CALIBRATION_MAX_PHI,
)


class TestConfigValidation(unittest.TestCase):

    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.max_allowed_dtl_days, 5.0)
        self.assertEqual(cfg.daily_participation_rate, 0.10)
        self.assertEqual(cfg.impact_coefficient_y, 1.0)

    def test_rejects_bad_policy(self):
        for kwargs in (
            {"max_allowed_dtl_days": 0.0},
            {"max_allowed_dtl_days": -1.0},
            {"max_allowed_dtl_days": float("nan")},
            {"daily_participation_rate": 0.0},
            {"daily_participation_rate": 1.5},
            {"daily_participation_rate": -0.1},
            {"impact_coefficient_y": -0.5},
            {"impact_coefficient_y": float("inf")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    Config(**kwargs)


class TestPositionValidation(unittest.TestCase):

    def test_accepts_valid(self):
        p = PortfolioPosition("AAPL", 1000.0, 150.0, adv_shares=1_000_000.0)
        self.assertEqual(p.spread_bps, 5.0)
        self.assertIsNone(p.daily_volatility)

    def test_rejects_bad_inputs(self):
        base = dict(symbol="X", quantity=100.0, current_price=10.0, adv_shares=1000.0)
        for override in (
            {"symbol": ""},
            {"symbol": "   "},
            {"symbol": None},
            {"quantity": float("nan")},
            {"quantity": "100"},
            {"quantity": True},
            {"current_price": 0.0},
            {"current_price": -10.0},
            {"current_price": float("inf")},
            {"adv_shares": 0.0},
            {"adv_shares": -5.0},
            {"adv_shares": float("nan")},
            {"spread_bps": -1.0},
            {"daily_volatility": -0.01},
            {"daily_volatility": float("nan")},
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    PortfolioPosition(**{**base, **override})

    def test_zero_adv_is_rejected_not_floored(self):
        # older floored ADV to 1.0 share/day, inventing a finite DTL for an
        # instrument with no volume at all.
        with self.assertRaises(ValueError):
            PortfolioPosition("DEAD", 1000.0, 10.0, adv_shares=0.0)


class TestScenarioValidation(unittest.TestCase):

    def test_rejects_bad_scenarios(self):
        for kwargs in (
            {"scenario_name": "", "price_shock_pct": {"A": -0.2}},
            {"scenario_name": "S", "price_shock_pct": {}},
            {"scenario_name": "S", "price_shock_pct": {"A": float("nan")}},
            {"scenario_name": "S", "price_shock_pct": {"A": -1.5}},
            {"scenario_name": "S", "price_shock_pct": {"A": -0.2}, "liquidity_drop_pct": 1.0},
            {"scenario_name": "S", "price_shock_pct": {"A": -0.2}, "liquidity_drop_pct": -0.1},
            {"scenario_name": "S", "price_shock_pct": {"A": -0.2},
             "spread_expansion_factor": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    StressScenario(**kwargs)

    def test_total_capacity_loss_is_rejected(self):
        # A 100% capacity loss is a halt: DTL would be infinite, not a large number.
        with self.assertRaises(ValueError):
            StressScenario("HALT", {"A": -0.2}, liquidity_drop_pct=1.0)

    def test_missing_shock_raises_instead_of_defaulting(self):
        scenario = StressScenario("S", {"AAPL": -0.20})
        self.assertEqual(scenario.shock_for("AAPL"), -0.20)
        with self.assertRaises(ValueError):
            scenario.shock_for("MSFT")

    def test_validation_does_not_mutate_the_callers_dict(self):
        # A scenario is routinely built once and reused across books; coercing in place
        # would edit the caller's dictionary under it.
        shocks = {"AAPL": -1, "MSFT": -0.2}
        StressScenario("S", shocks)
        self.assertEqual(shocks, {"AAPL": -1, "MSFT": -0.2})
        self.assertIsInstance(shocks["AAPL"], int)

    def test_scenario_is_reusable_across_runs(self):
        engine = PortfolioStressTestEngine(Config())
        scenario = StressScenario("REUSED", {"DEFAULT": -0.20})
        positions = [PortfolioPosition("SYM", 1000.0, 100.0,
                                       adv_shares=10_000_000.0, spread_bps=0.0)]
        first = engine.run_stress_test(positions, scenario)
        second = engine.run_stress_test(positions, scenario)
        self.assertEqual(first.total_stressed_loss_usd, second.total_stressed_loss_usd)

    def test_default_key_supplies_unlisted_symbols(self):
        scenario = StressScenario("S", {"AAPL": -0.20, "DEFAULT": -0.35})
        self.assertEqual(scenario.shock_for("AAPL"), -0.20)
        self.assertEqual(scenario.shock_for("ANYTHING_ELSE"), -0.35)


class TestPriceShockSign(unittest.TestCase):
    """
    Regression cover for an earlier sign bug: every long booked a loss equal to
    |shock| regardless of the shock's direction, and shorts booked no gain at all, so
    no book could ever net.
    """

    def setUp(self):
        self.engine = PortfolioStressTestEngine(Config(name="sign"))

    def _loss(self, quantity: float, shock: float) -> float:
        # spread_bps=0 isolates the mark-to-market term from the liquidity haircut.
        positions = [PortfolioPosition("SYM", quantity, 100.0,
                                       adv_shares=10_000_000.0, spread_bps=0.0)]
        scenario = StressScenario("S", {"SYM": shock})
        return self.engine.run_stress_test(positions, scenario).price_shock_loss_usd

    def test_long_loses_on_a_crash(self):
        # -(1000 * 100 * -0.10) = +10,000 loss
        self.assertEqual(self._loss(1000.0, -0.10), 10_000.0)

    def test_long_gains_on_a_rally(self):
        # -(1000 * 100 * +0.10) = -10,000, i.e. a 10,000 gain. older: +10,000 loss.
        self.assertEqual(self._loss(1000.0, 0.10), -10_000.0)

    def test_short_loses_on_a_rally(self):
        # -(-1000 * 100 * +0.10) = +10,000 loss
        self.assertEqual(self._loss(-1000.0, 0.10), 10_000.0)

    def test_short_gains_on_a_crash(self):
        # -(-1000 * 100 * -0.10) = -10,000, i.e. a 10,000 gain. older: 0.0.
        self.assertEqual(self._loss(-1000.0, -0.10), -10_000.0)

    def test_hedged_book_nets_to_zero(self):
        positions = [
            PortfolioPosition("LEG_LONG", 1000.0, 100.0,
                              adv_shares=10_000_000.0, spread_bps=0.0),
            PortfolioPosition("LEG_SHORT", -1000.0, 100.0,
                              adv_shares=10_000_000.0, spread_bps=0.0),
        ]
        scenario = StressScenario("PARALLEL_SHOCK", {"DEFAULT": -0.20})
        report = self.engine.run_stress_test(positions, scenario)

        self.assertEqual(report.price_shock_loss_usd, 0.0)   # older: 20,000.0
        self.assertEqual(report.net_exposure_usd, 0.0)
        self.assertEqual(report.total_portfolio_value_usd, 200_000.0)  # gross, not net


class TestDaysToLiquidate(unittest.TestCase):

    def test_documented_worked_example(self):
        # 100,000 shares, ADV 50,000, 50% capacity drop -> stressed ADV 25,000,
        # 10% participation -> 2,500 shares/day -> DTL = 100,000 / 2,500 = 40 days.
        engine = PortfolioStressTestEngine(Config())
        positions = [PortfolioPosition("SMALL_CAP", 100_000.0, 10.0,
                                       adv_shares=50_000.0, spread_bps=20.0)]
        scenario = StressScenario("LIQUIDITY_CRUNCH_2022", {"SMALL_CAP": -0.30},
                                  liquidity_drop_pct=0.50)
        report = engine.run_stress_test(positions, scenario)

        self.assertEqual(report.max_dtl_days, 40.0)
        self.assertEqual(report.status, STATUS_ILLIQUID_WARNING)
        self.assertEqual(report.illiquid_symbols, ["SMALL_CAP"])
        self.assertEqual(report.price_shock_loss_usd, 300_000.0)
        # Half of a 5x-expanded 20bps spread on $1,000,000, charged once:
        # 0.5 * 0.0100 * 1,000,000 = 5,000. older charged 100,000 (20x).
        self.assertEqual(report.spread_cost_usd, 5_000.0)
        self.assertEqual(report.total_stressed_loss_usd, 305_000.0)

    def test_threshold_is_strict(self):
        # capacity = 0.10 * 4000 * 0.5 = 200 shares/day; 1000 shares -> DTL exactly 5.0.
        engine = PortfolioStressTestEngine(Config(max_allowed_dtl_days=5.0))
        scenario = StressScenario("S", {"SYM": -0.10})

        at_limit = engine.run_stress_test(
            [PortfolioPosition("SYM", 1000.0, 10.0, adv_shares=4000.0)], scenario)
        self.assertEqual(at_limit.max_dtl_days, 5.0)
        self.assertEqual(at_limit.status, STATUS_PASSED)
        self.assertEqual(at_limit.illiquid_symbols, [])

        over_limit = engine.run_stress_test(
            [PortfolioPosition("SYM", 1001.0, 10.0, adv_shares=4000.0)], scenario)
        self.assertGreater(over_limit.max_dtl_days, 5.0)
        self.assertEqual(over_limit.status, STATUS_ILLIQUID_WARNING)

    def test_participation_rate_scales_dtl_inversely(self):
        # Doubling the participation rate halves DTL: 40 days -> 20 days.
        engine = PortfolioStressTestEngine(Config(daily_participation_rate=0.20))
        positions = [PortfolioPosition("SMALL_CAP", 100_000.0, 10.0, adv_shares=50_000.0)]
        report = engine.run_stress_test(
            positions, StressScenario("S", {"SMALL_CAP": -0.30}, liquidity_drop_pct=0.50))
        self.assertEqual(report.max_dtl_days, 20.0)

    def test_short_position_dtl_uses_magnitude(self):
        engine = PortfolioStressTestEngine(Config())
        scenario = StressScenario("S", {"SYM": -0.10})
        long_dtl = engine.run_stress_test(
            [PortfolioPosition("SYM", 100_000.0, 10.0, adv_shares=50_000.0)], scenario
        ).max_dtl_days
        short_dtl = engine.run_stress_test(
            [PortfolioPosition("SYM", -100_000.0, 10.0, adv_shares=50_000.0)], scenario
        ).max_dtl_days
        self.assertEqual(long_dtl, short_dtl)
        self.assertEqual(short_dtl, 40.0)


class TestSpreadCost(unittest.TestCase):

    def test_half_spread_charged_once_not_per_day(self):
        # 1,000 shares @ $100 = $100,000 gross; 10bps spread, no expansion.
        # Canonical (Bangia et al. 1999) cost = 0.5 * 0.0010 * 100,000 = $50.
        # ADV 1,000 with a 50% drop -> stressed ADV 500 -> 50 shares/day -> DTL 20.
        # older charged the full spread per day capped at 10 days: $1,000, a 20x
        # overstatement that grew with the liquidation horizon.
        engine = PortfolioStressTestEngine(Config())
        positions = [PortfolioPosition("SYM", 1000.0, 100.0,
                                       adv_shares=1000.0, spread_bps=10.0)]
        scenario = StressScenario("S", {"SYM": 0.0}, liquidity_drop_pct=0.50,
                                  spread_expansion_factor=1.0)
        report = engine.run_stress_test(positions, scenario)

        self.assertEqual(report.max_dtl_days, 20.0)
        self.assertEqual(report.spread_cost_usd, 50.0)
        self.assertNotEqual(report.spread_cost_usd, 1_000.0)

    def test_spread_cost_is_independent_of_liquidation_horizon(self):
        # Same position and spread, very different DTL: the crossing cost per share is
        # paid once either way. Only the impact term may depend on the horizon.
        engine = PortfolioStressTestEngine(Config())
        scenario = StressScenario("S", {"SYM": 0.0}, spread_expansion_factor=1.0)
        fast = engine.run_stress_test(
            [PortfolioPosition("SYM", 1000.0, 100.0, adv_shares=10_000_000.0,
                               spread_bps=10.0)], scenario)
        slow = engine.run_stress_test(
            [PortfolioPosition("SYM", 1000.0, 100.0, adv_shares=1000.0,
                               spread_bps=10.0)], scenario)
        self.assertLess(fast.max_dtl_days, 1.0)
        self.assertGreater(slow.max_dtl_days, 10.0)
        self.assertEqual(fast.spread_cost_usd, slow.spread_cost_usd)
        self.assertEqual(fast.spread_cost_usd, 50.0)

    def test_expansion_factor_scales_spread_linearly(self):
        engine = PortfolioStressTestEngine(Config())
        positions = [PortfolioPosition("SYM", 1000.0, 100.0,
                                       adv_shares=10_000_000.0, spread_bps=10.0)]
        one_x = engine.run_stress_test(
            positions, StressScenario("S", {"SYM": 0.0}, spread_expansion_factor=1.0))
        five_x = engine.run_stress_test(
            positions, StressScenario("S", {"SYM": 0.0}, spread_expansion_factor=5.0))
        self.assertEqual(five_x.spread_cost_usd, 5.0 * one_x.spread_cost_usd)
        self.assertEqual(five_x.positions[0].stressed_spread_bps, 50.0)

    def test_zero_spread_costs_nothing(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("SYM", 1000.0, 100.0, adv_shares=1_000_000.0,
                               spread_bps=0.0)],
            StressScenario("S", {"SYM": 0.0}))
        self.assertEqual(report.spread_cost_usd, 0.0)
        self.assertEqual(report.liquidity_slippage_haircut_usd, 0.0)


class TestMarketImpact(unittest.TestCase):

    def test_square_root_law_value(self):
        # 500 shares @ $1,000 = $500,000 gross. ADV 25,000, 50% drop -> stressed ADV
        # 12,500 -> phi = 500 / 12,500 = 0.04 -> sqrt(phi) = 0.2.
        # impact = Y(1.0) * sigma(0.04) * 0.2 * 500,000 = $4,000.
        engine = PortfolioStressTestEngine(Config())
        positions = [PortfolioPosition("MID", 500.0, 1000.0, adv_shares=25_000.0,
                                       spread_bps=0.0, daily_volatility=0.04)]
        report = engine.run_stress_test(
            positions, StressScenario("S", {"MID": 0.0}, liquidity_drop_pct=0.50))

        self.assertAlmostEqual(report.positions[0].volume_fraction, 0.04, places=6)
        self.assertAlmostEqual(report.market_impact_cost_usd, 4_000.0, places=2)
        self.assertEqual(report.liquidity_slippage_haircut_usd, report.market_impact_cost_usd)
        self.assertEqual(report.positions_missing_volatility, [])
        self.assertEqual(report.positions_outside_impact_calibration, [])

    def test_impact_scales_as_square_root_of_volume_fraction(self):
        # Quadrupling phi (via a quarter of the ADV) must exactly double the impact.
        engine = PortfolioStressTestEngine(Config())
        scenario = StressScenario("S", {"SYM": 0.0}, liquidity_drop_pct=0.0)

        def impact(adv: float) -> float:
            return engine.run_stress_test(
                [PortfolioPosition("SYM", 1000.0, 100.0, adv_shares=adv,
                                   spread_bps=0.0, daily_volatility=0.03)],
                scenario).market_impact_cost_usd

        self.assertAlmostEqual(impact(100_000.0) * 2.0, impact(25_000.0), places=6)

    def test_impact_not_priced_without_volatility(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("NOVOL", 1000.0, 100.0, adv_shares=25_000.0)],
            StressScenario("S", {"NOVOL": 0.0}))
        self.assertEqual(report.market_impact_cost_usd, 0.0)
        self.assertEqual(report.positions_missing_volatility, ["NOVOL"])
        self.assertFalse(report.positions[0].impact_priced)
        # The gap must be visible in the audit trail, not silently priced at zero.
        self.assertIn("NOT priced", report.audit_notes)

    def test_extrapolation_beyond_fitted_range_is_flagged(self):
        # phi = 100,000 / 25,000 = 4.0, far beyond the "few %" the law was fitted over.
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("SMALL_CAP", 100_000.0, 10.0, adv_shares=50_000.0,
                               spread_bps=0.0, daily_volatility=0.05)],
            StressScenario("S", {"SMALL_CAP": 0.0}, liquidity_drop_pct=0.50))

        self.assertAlmostEqual(report.positions[0].volume_fraction, 4.0, places=6)
        self.assertGreater(report.positions[0].volume_fraction, IMPACT_CALIBRATION_MAX_PHI)
        self.assertTrue(report.positions[0].impact_extrapolated)
        self.assertEqual(report.positions_outside_impact_calibration, ["SMALL_CAP"])
        # Y * 0.05 * sqrt(4.0) * 1,000,000 = 100,000
        self.assertAlmostEqual(report.market_impact_cost_usd, 100_000.0, places=2)
        self.assertIn("fitted range", report.audit_notes)

    def test_impact_coefficient_is_configurable(self):
        positions = [PortfolioPosition("MID", 500.0, 1000.0, adv_shares=25_000.0,
                                       spread_bps=0.0, daily_volatility=0.04)]
        scenario = StressScenario("S", {"MID": 0.0}, liquidity_drop_pct=0.50)

        zero = PortfolioStressTestEngine(
            Config(impact_coefficient_y=0.0)).run_stress_test(positions, scenario)
        half = PortfolioStressTestEngine(
            Config(impact_coefficient_y=0.5)).run_stress_test(positions, scenario)

        self.assertEqual(zero.market_impact_cost_usd, 0.0)
        self.assertAlmostEqual(half.market_impact_cost_usd, 2_000.0, places=2)


class TestAggregationAndAudit(unittest.TestCase):

    def setUp(self):
        self.engine = PortfolioStressTestEngine(Config(name="test_stress"))

    def test_liquid_portfolio_passes(self):
        # 1,000 shares @ $150 = $150,000. Shock -20% -> 30,000 loss.
        # ADV 1,000,000, 50% drop -> 500,000 -> 50,000/day -> DTL 0.02.
        # Spread: 0.5 * (5bps * 5 / 10,000) * 150,000 = 0.5 * 0.0025 * 150,000 = 187.50
        positions = [PortfolioPosition("AAPL", 1000.0, 150.0,
                                       adv_shares=1_000_000.0, spread_bps=5.0)]
        scenario = StressScenario("2020_COVID_SHOCK", {"AAPL": -0.20},
                                  liquidity_drop_pct=0.50, spread_expansion_factor=5.0)
        report = self.engine.run_stress_test(positions, scenario)

        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.price_shock_loss_usd, 30_000.0)
        self.assertEqual(report.spread_cost_usd, 187.50)
        self.assertEqual(report.total_stressed_loss_usd, 30_187.50)
        self.assertEqual(report.max_dtl_days, 0.02)
        self.assertLess(report.max_dtl_days, 1.0)

    def test_components_sum_to_the_total(self):
        positions = [
            PortfolioPosition("A", 1000.0, 100.0, adv_shares=50_000.0,
                              spread_bps=8.0, daily_volatility=0.03),
            PortfolioPosition("B", -2000.0, 50.0, adv_shares=20_000.0,
                              spread_bps=15.0, daily_volatility=0.06),
            PortfolioPosition("C", 500.0, 20.0, adv_shares=1_000.0, spread_bps=40.0),
        ]
        scenario = StressScenario("MIXED", {"A": -0.25, "B": -0.10, "DEFAULT": -0.15})
        report = self.engine.run_stress_test(positions, scenario)

        self.assertEqual(len(report.positions), 3)
        self.assertTrue(all(isinstance(r, PositionStressResult) for r in report.positions))
        self.assertIsInstance(report, StressTestReport)

        self.assertAlmostEqual(
            report.liquidity_slippage_haircut_usd,
            report.spread_cost_usd + report.market_impact_cost_usd, places=2)
        self.assertAlmostEqual(
            report.total_stressed_loss_usd,
            report.price_shock_loss_usd + report.liquidity_slippage_haircut_usd, places=2)
        self.assertAlmostEqual(
            report.spread_cost_usd,
            sum(r.spread_cost_usd for r in report.positions), places=2)
        self.assertAlmostEqual(
            report.price_shock_loss_usd,
            sum(r.price_shock_loss_usd for r in report.positions), places=2)

        # C takes the DEFAULT shock; gross is |Q|*P summed, net is signed.
        self.assertEqual(report.positions[2].price_shock_pct, -0.15)
        self.assertEqual(report.total_portfolio_value_usd, 100_000.0 + 100_000.0 + 10_000.0)
        self.assertEqual(report.net_exposure_usd, 100_000.0 - 100_000.0 + 10_000.0)
        self.assertEqual(report.positions_missing_volatility, ["C"])

    def test_loss_percentage_uses_gross_exposure(self):
        positions = [PortfolioPosition("SYM", 1000.0, 100.0,
                                       adv_shares=10_000_000.0, spread_bps=0.0)]
        report = self.engine.run_stress_test(
            positions, StressScenario("S", {"SYM": -0.25}))
        # 25,000 loss on 100,000 gross = 25%.
        self.assertEqual(report.total_stressed_loss_pct_of_gross, 0.25)
        self.assertIn("25.00%", report.audit_notes)

    def test_empty_portfolio_is_zero_not_a_division_error(self):
        report = self.engine.run_stress_test([], StressScenario("S", {"DEFAULT": -0.20}))
        self.assertEqual(report.total_portfolio_value_usd, 0.0)
        self.assertEqual(report.total_stressed_loss_usd, 0.0)
        self.assertEqual(report.total_stressed_loss_pct_of_gross, 0.0)
        self.assertEqual(report.max_dtl_days, 0.0)
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.positions, [])

    def test_audit_notes_name_the_scenario_and_status(self):
        report = self.engine.run_stress_test(
            [PortfolioPosition("SMALL_CAP", 100_000.0, 10.0, adv_shares=50_000.0)],
            StressScenario("GFC_2008", {"SMALL_CAP": -0.40}))
        self.assertIn("GFC_2008", report.audit_notes)
        self.assertIn(STATUS_ILLIQUID_WARNING, report.audit_notes)
        self.assertIn("SMALL_CAP", report.audit_notes)


class TestEngineInputGuards(unittest.TestCase):

    def setUp(self):
        self.engine = PortfolioStressTestEngine(Config())
        self.scenario = StressScenario("S", {"DEFAULT": -0.20})

    def test_duplicate_symbol_raises(self):
        # Two rows of 50,000 each would report DTL 20 apiece; the real holding of
        # 100,000 shares takes 40 days.
        positions = [
            PortfolioPosition("DUP", 50_000.0, 10.0, adv_shares=50_000.0),
            PortfolioPosition("DUP", 50_000.0, 10.0, adv_shares=50_000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.run_stress_test(positions, self.scenario)

    def test_rejects_wrong_types(self):
        good = PortfolioPosition("A", 100.0, 10.0, adv_shares=10_000.0)
        with self.assertRaises(ValueError):
            self.engine.run_stress_test("not a sequence", self.scenario)
        with self.assertRaises(ValueError):
            self.engine.run_stress_test([{"symbol": "A"}], self.scenario)
        with self.assertRaises(ValueError):
            self.engine.run_stress_test([good], {"scenario_name": "S"})
        # A generator would be consumed by the duplicate-symbol pre-pass and arrive empty
        # at the main loop, silently reporting a zero-loss book.
        with self.assertRaises(ValueError):
            self.engine.run_stress_test((p for p in [good]), self.scenario)

    def test_accepts_a_tuple_of_positions(self):
        good = PortfolioPosition("A", 100.0, 10.0, adv_shares=10_000.0)
        report = self.engine.run_stress_test((good,), self.scenario)
        self.assertEqual(len(report.positions), 1)

    def test_symbol_absent_from_scenario_raises(self):
        engine = PortfolioStressTestEngine(Config())
        positions = [PortfolioPosition("UNLISTED", 100.0, 10.0, adv_shares=10_000.0)]
        with self.assertRaises(ValueError):
            engine.run_stress_test(positions, StressScenario("S", {"OTHER": -0.20}))


class TestNumericalEdgeCases(unittest.TestCase):

    def test_zero_quantity_contributes_nothing(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("FLAT", 0.0, 100.0, adv_shares=10_000.0,
                               spread_bps=20.0, daily_volatility=0.05)],
            StressScenario("S", {"FLAT": -0.50}))
        self.assertEqual(report.total_stressed_loss_usd, 0.0)
        self.assertEqual(report.max_dtl_days, 0.0)
        self.assertEqual(report.status, STATUS_PASSED)

    def test_no_liquidity_drop_leaves_adv_intact(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("SYM", 1000.0, 10.0, adv_shares=100_000.0)],
            StressScenario("CALM", {"SYM": -0.05}, liquidity_drop_pct=0.0))
        self.assertEqual(report.positions[0].stressed_adv_shares, 100_000.0)
        self.assertEqual(report.positions[0].daily_capacity_shares, 10_000.0)
        self.assertEqual(report.max_dtl_days, 0.1)

    def test_full_wipeout_shock_is_bounded_by_position_value(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("ZERO", 1000.0, 100.0, adv_shares=1_000_000.0,
                               spread_bps=0.0)],
            StressScenario("WIPEOUT", {"ZERO": -1.0}))
        self.assertEqual(report.price_shock_loss_usd, 100_000.0)
        self.assertEqual(report.price_shock_loss_usd, report.total_portfolio_value_usd)

    def test_all_report_figures_are_finite(self):
        engine = PortfolioStressTestEngine(Config())
        report = engine.run_stress_test(
            [PortfolioPosition("TINY_ADV", 1_000_000.0, 5.0, adv_shares=1e-6,
                               spread_bps=500.0, daily_volatility=1.0)],
            StressScenario("S", {"TINY_ADV": -0.90}, liquidity_drop_pct=0.99))
        for value in (report.total_stressed_loss_usd, report.max_dtl_days,
                      report.spread_cost_usd, report.market_impact_cost_usd):
            self.assertTrue(math.isfinite(value))
        self.assertEqual(report.status, STATUS_ILLIQUID_WARNING)


if __name__ == "__main__":
    unittest.main()
