import math
import unittest

from multi_currency_var_aggregator import (
    BASEL_ES_CONFIDENCE_LEVEL,
    MultiCurrencyPosition,
    MultiCurrencyVarAggregatorEngine,
    VarConfig,
)

# Independently sourced standard normal quantiles (Wichura AS241 reference values),
# used so the assertions do not simply re-run the implementation's own lookup.
Z_90 = 1.2815515655446004
Z_95 = 1.6448536269514722
Z_975 = 1.9599639845400545
Z_99 = 2.3263478740408408


class TestZScoreAndTailCount(unittest.TestCase):
    """Regression cover for the two pure quantile helpers."""

    def test_tabulated_confidence_levels_match_reference_quantiles(self):
        engine = MultiCurrencyVarAggregatorEngine()
        self.assertAlmostEqual(engine._get_z_score(0.90), Z_90, places=9)
        self.assertAlmostEqual(engine._get_z_score(0.95), Z_95, places=9)
        self.assertAlmostEqual(engine._get_z_score(0.99), Z_99, places=9)

    def test_non_tabulated_confidence_level_does_not_raise(self):
        # Regression: the previous implementation fell through to `math.erfinv`,
        # which does not exist in the Python `math` module, so every confidence
        # level outside {0.90, 0.95, 0.99} raised AttributeError at call time.
        engine = MultiCurrencyVarAggregatorEngine()
        self.assertAlmostEqual(
            engine._get_z_score(BASEL_ES_CONFIDENCE_LEVEL), Z_975, places=9)
        self.assertAlmostEqual(engine._get_z_score(0.995), 2.5758293035489004, places=9)

    def test_z_score_rejects_out_of_range_confidence(self):
        engine = MultiCurrencyVarAggregatorEngine()
        for bad in (0.0, 1.0, -0.5, 1.5):
            with self.assertRaises(ValueError):
                engine._get_z_score(bad)

    def test_tail_count_uses_ceiling_convention(self):
        engine = MultiCurrencyVarAggregatorEngine()
        # 100 daily observations at 95% -> the 5th worst loss, not the 6th.
        self.assertEqual(engine._tail_count(100, 0.95), 5)
        # ceil(250 * 0.01) = 3
        self.assertEqual(engine._tail_count(250, 0.99), 3)
        # ceil(99 * 0.05) = 5 -- the two conventions agree off the integer case
        self.assertEqual(engine._tail_count(99, 0.95), 5)
        # Never zero, never larger than the sample
        self.assertEqual(engine._tail_count(100, 0.999), 1)
        self.assertEqual(engine._tail_count(20, 0.95), 1)


def _single_usd_position(quantity=1000.0, price=100.0, symbol="AAPL"):
    return MultiCurrencyPosition(symbol, "USD", quantity=quantity,
                                 current_price_native=price, fx_rate_to_base=1.0)


class TestHistoricalVarAndExpectedShortfall(unittest.TestCase):
    """
    Order-statistic behaviour, checked against hand-computed losses.

    One USD position worth exactly $100,000 with r_t = -t / 10000 for t = 1..100.
    P&L_t = -10t, so the loss series is 10, 20, ... 1000 -- all distinct, so the
    selected order statistic is unambiguous.
    """

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()
        self.position = _single_usd_position()
        self.returns = {"AAPL": [-(t / 10000.0) for t in range(1, 101)]}

    def test_var_is_kth_worst_loss_and_es_is_mean_of_k_worst(self):
        cfg = VarConfig(confidence_level=0.95, holding_period_days=1,
                        base_currency="USD")
        report = self.engine.calculate_multi_currency_var(
            cfg, [self.position], self.returns, {})

        self.assertEqual(report.observations_used, 100)
        self.assertEqual(report.tail_observations_used, 5)
        # Worst five losses: 1000, 990, 980, 970, 960.
        self.assertAlmostEqual(report.historical_var_base, 960.0, places=2)
        self.assertAlmostEqual(report.expected_shortfall_cvar_base, 980.0, places=2)

    def test_previous_off_by_one_result_is_not_produced(self):
        # The superseded floor()-index convention selected the 6th worst loss (950)
        # and averaged six losses (975), understating both measures.
        cfg = VarConfig(confidence_level=0.95, base_currency="USD")
        report = self.engine.calculate_multi_currency_var(
            cfg, [self.position], self.returns, {})
        self.assertNotAlmostEqual(report.historical_var_base, 950.0, places=2)
        self.assertNotAlmostEqual(report.expected_shortfall_cvar_base, 975.0, places=2)

    def test_expected_shortfall_never_below_var(self):
        for confidence in (0.90, 0.95, 0.975, 0.99):
            cfg = VarConfig(confidence_level=confidence, base_currency="USD")
            report = self.engine.calculate_multi_currency_var(
                cfg, [self.position], self.returns, {})
            self.assertGreaterEqual(
                report.expected_shortfall_cvar_base, report.historical_var_base,
                msg=f"ES < VaR at confidence {confidence}")


class TestParametricVar(unittest.TestCase):
    """Parametric VaR against a closed-form value derived outside the module."""

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()

    def test_parametric_var_matches_hand_computed_value(self):
        # $1,000,000 USD position, returns alternating +/-1% over 100 periods.
        # P&L_t = +/-10,000 with a zero sample mean, so the (n-1) sample standard
        # deviation is 10,000 * sqrt(100/99) and VaR = z_95 * sigma.
        position = _single_usd_position(quantity=10000.0, price=100.0)
        returns = {"AAPL": [0.01 if t % 2 == 0 else -0.01 for t in range(100)]}
        expected_sigma = 10000.0 * math.sqrt(100.0 / 99.0)
        expected_var = Z_95 * expected_sigma

        cfg = VarConfig(confidence_level=0.95, base_currency="USD")
        report = self.engine.calculate_multi_currency_var(
            cfg, [position], returns, {})

        self.assertAlmostEqual(report.portfolio_volatility_base, expected_sigma,
                               places=4)
        self.assertAlmostEqual(report.parametric_var_base, round(expected_var, 2),
                               places=2)

    def test_sqrt_time_scaling_multiplies_by_sqrt_holding_period(self):
        position = _single_usd_position(quantity=10000.0)
        returns = {"AAPL": [0.01 if t % 2 == 0 else -0.01 for t in range(100)]}

        one_day = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, holding_period_days=1), [position],
            returns, {})
        four_day = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, holding_period_days=4), [position],
            returns, {})

        self.assertFalse(one_day.holding_period_scaled)
        self.assertTrue(four_day.holding_period_scaled)
        self.assertAlmostEqual(four_day.parametric_var_base,
                               2.0 * one_day.parametric_var_base, places=2)
        self.assertAlmostEqual(four_day.historical_var_base,
                               2.0 * one_day.historical_var_base, places=2)

    def test_drift_subtraction_lowers_var_on_a_positive_drift_book(self):
        position = _single_usd_position(quantity=10000.0)
        # Mean return +0.5% per period.
        returns = {"AAPL": [0.015 if t % 2 == 0 else -0.005 for t in range(100)]}

        without = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95), [position], returns, {})
        with_drift = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, subtract_mean_drift=True), [position],
            returns, {})

        self.assertGreater(without.portfolio_mean_pnl_base, 0.0)
        self.assertAlmostEqual(
            without.parametric_var_base - with_drift.parametric_var_base,
            round(without.portfolio_mean_pnl_base, 2), places=1)


class TestMultiCurrencySynthesis(unittest.TestCase):
    """The joint asset-FX compounding that is the point of the skill."""

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()
        self.usd = MultiCurrencyPosition("AAPL", "USD", 1000.0, 100.0, 1.0)
        self.eur = MultiCurrencyPosition("SAP", "EUR", 1000.0, 100.0, 1.10)
        self.r_aapl = [0.01 if i % 2 == 0 else -0.01 for i in range(100)]
        self.r_sap = [0.015 if i % 3 == 0 else -0.008 for i in range(100)]
        self.r_eur = [0.002 if i % 4 == 0 else -0.002 for i in range(100)]

    def test_baseline_two_currency_portfolio(self):
        cfg = VarConfig(confidence_level=0.95, holding_period_days=1,
                        base_currency="USD")
        report = self.engine.calculate_multi_currency_var(
            cfg, [self.usd, self.eur],
            {"AAPL": self.r_aapl, "SAP": self.r_sap},
            {"USD": [0.0] * 100, "EUR": self.r_eur},
        )

        self.assertEqual(report.status, "VAR_CALCULATION_SUCCESS")
        self.assertAlmostEqual(report.total_portfolio_value_base, 210000.0, places=2)
        self.assertAlmostEqual(report.gross_exposure_base, 210000.0, places=2)
        self.assertAlmostEqual(report.currency_risk_breakdown["USD"], 100000.0,
                               places=2)
        self.assertAlmostEqual(report.currency_risk_breakdown["EUR"], 110000.0,
                               places=2)
        self.assertGreater(report.parametric_var_base, 0.0)
        self.assertGreater(report.historical_var_base, 0.0)
        self.assertGreaterEqual(report.expected_shortfall_cvar_base,
                                report.historical_var_base)

    def test_joint_return_compounds_asset_and_fx(self):
        # A single EUR position: base return must be (1+r_asset)(1+r_fx)-1, so the
        # P&L volatility differs from the asset-only volatility.
        asset = [0.01 if i % 2 == 0 else -0.01 for i in range(100)]
        fx_same_sign = list(asset)          # FX amplifies the asset move
        fx_opposite = [-r for r in asset]   # FX offsets it almost exactly

        amplified = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [self.eur], {"SAP": asset},
            {"EUR": fx_same_sign})
        offset = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [self.eur], {"SAP": asset},
            {"EUR": fx_opposite})

        self.assertGreater(amplified.parametric_var_base,
                           10.0 * offset.parametric_var_base)

    def test_missing_fx_series_for_non_base_currency_raises(self):
        # Regression: the previous implementation substituted a zero vector, which
        # silently removed all currency risk and understated VaR.
        with self.assertRaises(ValueError) as ctx:
            self.engine.calculate_multi_currency_var(
                VarConfig(base_currency="USD"), [self.usd, self.eur],
                {"AAPL": self.r_aapl, "SAP": self.r_sap},
                {"USD": [0.0] * 100},
            )
        self.assertIn("EUR", str(ctx.exception))

    def test_base_currency_fx_series_may_be_omitted(self):
        report = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [self.usd], {"AAPL": self.r_aapl}, {})
        self.assertGreater(report.parametric_var_base, 0.0)

    def test_non_zero_base_currency_fx_series_raises(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(base_currency="USD"), [self.usd], {"AAPL": self.r_aapl},
                {"USD": self.r_eur})

    def test_base_currency_position_must_have_unit_fx_rate(self):
        bad = MultiCurrencyPosition("AAPL", "USD", 1000.0, 100.0, 1.10)
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(base_currency="USD"), [bad], {"AAPL": self.r_aapl}, {})

    def test_duplicate_symbol_lots_are_both_counted(self):
        # Regression: series and weights were keyed by symbol, so a second lot of
        # the same symbol overwrote the first and the portfolio silently shrank.
        one_lot = MultiCurrencyPosition("AAPL", "USD", 1000.0, 100.0, 1.0)
        lot_a = MultiCurrencyPosition("AAPL", "USD", 800.0, 100.0, 1.0)
        lot_b = MultiCurrencyPosition("AAPL", "USD", 200.0, 100.0, 1.0)

        combined = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [one_lot], {"AAPL": self.r_aapl}, {})
        split = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [lot_a, lot_b], {"AAPL": self.r_aapl}, {})

        self.assertAlmostEqual(split.total_portfolio_value_base,
                               combined.total_portfolio_value_base, places=2)
        self.assertAlmostEqual(split.parametric_var_base,
                               combined.parametric_var_base, places=2)
        self.assertAlmostEqual(split.historical_var_base,
                               combined.historical_var_base, places=2)

    def test_market_neutral_book_with_near_zero_net_value_is_measurable(self):
        # Regression: a `total_value_base <= 0` guard refused to measure a hedged
        # cross-border book whose net value is ~0 but whose gross exposure is not.
        short_eur = MultiCurrencyPosition("SAP", "EUR", -909.0909, 100.0, 1.10)
        report = self.engine.calculate_multi_currency_var(
            VarConfig(base_currency="USD"), [self.usd, short_eur],
            {"AAPL": self.r_aapl, "SAP": self.r_sap},
            {"EUR": self.r_eur},
        )
        self.assertLess(abs(report.total_portfolio_value_base), 1.0)
        self.assertAlmostEqual(report.gross_exposure_base, 200000.0, delta=1.0)
        self.assertGreater(report.parametric_var_base, 0.0)


class TestComponentVarDecomposition(unittest.TestCase):
    """Euler additivity: the components must sum to the total with no residual."""

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()
        self.positions = [
            MultiCurrencyPosition("AAPL", "USD", 1000.0, 100.0, 1.0),
            MultiCurrencyPosition("SAP", "EUR", 1000.0, 100.0, 1.10),
            MultiCurrencyPosition("SONY", "JPY", 1000.0, 1500.0, 0.0068),
        ]
        self.native = {
            "AAPL": [0.01 if i % 2 == 0 else -0.01 for i in range(120)],
            "SAP": [0.015 if i % 3 == 0 else -0.008 for i in range(120)],
            "SONY": [0.012 if i % 5 == 0 else -0.003 for i in range(120)],
        }
        self.fx = {
            "EUR": [0.002 if i % 4 == 0 else -0.002 for i in range(120)],
            "JPY": [-0.003 if i % 7 == 0 else 0.0005 for i in range(120)],
        }

    def test_currency_components_sum_to_parametric_var(self):
        report = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.99, base_currency="USD"),
            self.positions, self.native, self.fx)
        total = sum(report.currency_component_var_base.values())
        self.assertAlmostEqual(total, report.parametric_var_base, places=1)
        self.assertEqual(set(report.currency_component_var_base),
                         {"USD", "EUR", "JPY"})

    def test_symbol_components_sum_to_parametric_var_with_drift(self):
        report = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, base_currency="USD",
                      subtract_mean_drift=True),
            self.positions, self.native, self.fx)
        total = sum(report.symbol_component_var_base.values())
        self.assertAlmostEqual(total, report.parametric_var_base, places=1)

    def test_components_sum_to_var_on_a_zero_variance_series(self):
        # A constant return series gives sigma = 0. Without drift the total and every
        # component must be 0; with drift both must still reconcile.
        flat = MultiCurrencyPosition("FLAT", "USD", 1000.0, 100.0, 1.0)
        constant = {"FLAT": [0.002] * 60}

        plain = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, base_currency="USD"), [flat],
            constant, {})
        self.assertAlmostEqual(plain.portfolio_volatility_base, 0.0, places=6)
        self.assertAlmostEqual(plain.parametric_var_base, 0.0, places=2)
        self.assertAlmostEqual(sum(plain.symbol_component_var_base.values()),
                               plain.parametric_var_base, places=2)

        drifted = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, base_currency="USD",
                      subtract_mean_drift=True), [flat], constant, {})
        self.assertAlmostEqual(sum(drifted.symbol_component_var_base.values()),
                               drifted.parametric_var_base, places=2)

    def test_component_var_is_not_the_exposure_breakdown(self):
        report = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95, base_currency="USD"),
            self.positions, self.native, self.fx)
        # Exposure is in the hundred-thousands; component VaR is a risk number.
        self.assertAlmostEqual(report.currency_risk_breakdown["EUR"], 110000.0,
                               places=2)
        self.assertNotAlmostEqual(report.currency_component_var_base["EUR"],
                                  110000.0, places=2)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyVarAggregatorEngine()
        self.position = _single_usd_position()
        self.returns = {"AAPL": [0.01 if i % 2 == 0 else -0.01 for i in range(100)]}

    def test_empty_position_list_raises(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(), [], self.returns, {})

    def test_missing_symbol_return_series_raises(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(), [self.position], {}, {})

    def test_non_finite_return_raises(self):
        for bad in (float("nan"), float("inf")):
            corrupt = dict(self.returns)
            corrupt["AAPL"] = list(self.returns["AAPL"])
            corrupt["AAPL"][17] = bad
            with self.assertRaises(ValueError):
                self.engine.calculate_multi_currency_var(
                    VarConfig(), [self.position], corrupt, {})

    def test_mismatched_fx_series_length_raises(self):
        # Regression: zip() silently truncated to the shorter series, dropping
        # observations from the sample without any error.
        eur = MultiCurrencyPosition("SAP", "EUR", 1000.0, 100.0, 1.10)
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(base_currency="USD"), [eur],
                {"SAP": self.returns["AAPL"]}, {"EUR": [0.001] * 50})

    def test_mismatched_symbol_series_length_raises(self):
        second = MultiCurrencyPosition("MSFT", "USD", 500.0, 200.0, 1.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(), [self.position, second],
                {"AAPL": self.returns["AAPL"], "MSFT": [0.01] * 60}, {})

    def test_sample_too_short_for_confidence_level_raises(self):
        short = {"AAPL": [0.01 if i % 2 == 0 else -0.01 for i in range(30)]}
        # 30 observations cannot locate a 99% quantile (needs >= 100).
        with self.assertRaises(ValueError) as ctx:
            self.engine.calculate_multi_currency_var(
                VarConfig(confidence_level=0.99), [self.position], short, {})
        self.assertIn("Insufficient history", str(ctx.exception))
        # ... but 30 is enough at 95% (needs >= 20).
        report = self.engine.calculate_multi_currency_var(
            VarConfig(confidence_level=0.95), [self.position], short, {})
        self.assertEqual(report.observations_used, 30)

    def test_invalid_config_values_raise(self):
        for cfg in (
            VarConfig(confidence_level=0.0),
            VarConfig(confidence_level=1.0),
            VarConfig(confidence_level=0.05),      # inverted input
            VarConfig(holding_period_days=0),
            VarConfig(holding_period_days=-1),
            VarConfig(base_currency=""),
            VarConfig(min_observations=-5),
        ):
            with self.assertRaises(ValueError):
                self.engine.calculate_multi_currency_var(
                    cfg, [self.position], self.returns, {})

    def test_invalid_position_values_raise(self):
        for bad in (
            MultiCurrencyPosition("", "USD", 1000.0, 100.0, 1.0),
            MultiCurrencyPosition("AAPL", "", 1000.0, 100.0, 1.0),
            MultiCurrencyPosition("AAPL", "EUR", 1000.0, -100.0, 1.10),
            MultiCurrencyPosition("AAPL", "EUR", 1000.0, 100.0, 0.0),
            MultiCurrencyPosition("AAPL", "EUR", 1000.0, 100.0, -1.10),
            MultiCurrencyPosition("AAPL", "USD", float("nan"), 100.0, 1.0),
        ):
            with self.assertRaises(ValueError):
                self.engine.calculate_multi_currency_var(
                    VarConfig(base_currency="USD"), [bad],
                    {"AAPL": self.returns["AAPL"]},
                    {"EUR": [0.0005] * 100})

    def test_explicit_min_observations_overrides_derived_floor(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                VarConfig(confidence_level=0.95, min_observations=250),
                [self.position], self.returns, {})

    def test_min_observations_never_drops_below_two(self):
        # min_observations=1 must not admit a single-observation sample: the (n-1)
        # sample variance would divide by zero.
        cfg = VarConfig(confidence_level=0.95, min_observations=1)
        self.assertEqual(cfg.required_observations(), 2)
        with self.assertRaises(ValueError):
            self.engine.calculate_multi_currency_var(
                cfg, [self.position], {"AAPL": [0.01]}, {})


if __name__ == '__main__':
    unittest.main()
