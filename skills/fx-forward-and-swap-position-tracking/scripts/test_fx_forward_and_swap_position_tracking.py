"""Unit tests for the FX forward and swap position tracking engine.

Expected values are derived independently of the implementation: each one is
worked from the CIRP and present-value identities written out in the test, not
by calling the engine and recording what it returned.
"""
import logging
import unittest

from fx_forward_and_swap_position_tracking import (
    BUCKET_BEYOND_ONE_YEAR,
    CONTRACT_FX_FORWARD,
    CONTRACT_FX_SWAP,
    MTM_BASIS_CIRP,
    MTM_BASIS_OBSERVED,
    FxContractPosition,
    FxForwardSwapTrackingEngine,
)

# Keep expected WARNING output off the suite's stderr; assertLogs sets its own
# level inside the context manager, so the warning assertions still hold.
logging.getLogger("fx_forward_and_swap_position_tracking").setLevel(logging.CRITICAL + 1)


def forward(spot, r_base, r_quote, days, basis_base, basis_quote):
    """CIRP forward, written out independently of the engine."""
    return spot * (1.0 + r_quote * days / basis_quote) / (1.0 + r_base * days / basis_base)


def eur_usd_position(**overrides):
    kwargs = dict(
        contract_id="FWD_EUR_01",
        currency_pair="EUR/USD",
        base_currency="EUR",
        quote_currency="USD",
        contract_type=CONTRACT_FX_FORWARD,
        position_side="BUY",
        notional_base_currency=1_000_000.0,
        agreed_forward_rate=1.1050,
        days_to_maturity=90,
    )
    kwargs.update(overrides)
    return FxContractPosition(**kwargs)


EUR_USD_MARKET = {"EUR/USD": {"spot": 1.1000, "r_base": 0.03, "r_quote": 0.05}}


class TestCirpForwardRate(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def test_eur_usd_matches_independent_formula(self):
        # EUR Act/360, USD Act/360: 1.10 * (1 + .05*90/360) / (1 + .03*90/360)
        expected = 1.10 * 1.0125 / 1.0075
        self.assertAlmostEqual(expected, 1.10545906, places=8)
        actual = self.engine.calculate_cirp_forward_rate(
            spot_rate=1.1000,
            base_interest_rate=0.03,
            quote_interest_rate=0.05,
            days_to_maturity=90,
            base_currency="EUR",
            quote_currency="USD",
        )
        self.assertAlmostEqual(actual, expected, places=12)

    def test_forward_is_not_rounded(self):
        # 1e-6 of rounding is 100 quote units on a 100mm notional, so the
        # pricing call must return full precision.
        actual = self.engine.calculate_cirp_forward_rate(
            1.1000, 0.03, 0.05, 90, "EUR", "USD"
        )
        self.assertNotEqual(actual, round(actual, 6))

    def test_mixed_day_count_basis_gbp_usd(self):
        # Regression: sterling accrues Act/365 while USD accrues Act/360.
        # A single Act/360 denominator on both legs misprices the 6M forward.
        correct = forward(1.25, 0.045, 0.05, 180, 365, 360)
        single_basis = forward(1.25, 0.045, 0.05, 180, 360, 360)
        self.assertAlmostEqual(correct, 1.25343407, places=8)
        self.assertAlmostEqual(single_basis, 1.25305623, places=8)
        self.assertAlmostEqual((correct - single_basis) * 10_000, 3.78, places=2)

        actual = self.engine.calculate_cirp_forward_rate(
            1.25, 0.045, 0.05, 180, "GBP", "USD"
        )
        self.assertAlmostEqual(actual, correct, places=12)
        self.assertNotAlmostEqual(actual, single_basis, places=6)

    def test_day_count_basis_table_and_override(self):
        self.assertEqual(self.engine.day_count_basis_for("USD"), 360)
        self.assertEqual(self.engine.day_count_basis_for("EUR"), 360)
        self.assertEqual(self.engine.day_count_basis_for("GBP"), 365)
        self.assertEqual(self.engine.day_count_basis_for("JPY"), 365)
        overridden = FxForwardSwapTrackingEngine(day_count_basis={"AUD": 365})
        self.assertEqual(overridden.day_count_basis_for("aud"), 365)

    def test_unknown_currency_falls_back_and_warns(self):
        with self.assertLogs("fx_forward_and_swap_position_tracking", level="WARNING") as ctx:
            basis = self.engine.day_count_basis_for("ZAR")
        self.assertEqual(basis, 360)
        self.assertIn("ZAR", ctx.output[0])

    def test_currencies_omitted_uses_default_basis_for_both_legs(self):
        expected = forward(1.25, 0.045, 0.05, 180, 360, 360)
        actual = self.engine.calculate_cirp_forward_rate(1.25, 0.045, 0.05, 180)
        self.assertAlmostEqual(actual, expected, places=12)

    def test_zero_days_returns_spot(self):
        self.assertAlmostEqual(
            self.engine.calculate_cirp_forward_rate(1.1000, 0.03, 0.05, 0, "EUR", "USD"),
            1.1000,
            places=12,
        )

    def test_negative_rates_are_allowed(self):
        # A negative quote rate must price a discount, not raise.
        expected = forward(1.1000, 0.03, -0.005, 90, 360, 360)
        actual = self.engine.calculate_cirp_forward_rate(
            1.1000, 0.03, -0.005, 90, "EUR", "USD"
        )
        self.assertAlmostEqual(actual, expected, places=12)
        self.assertLess(actual, 1.1000)

    def test_non_positive_accrual_factor_raises(self):
        # 1 + (-0.60) * (1200/360) = -1.0 -> the simple-interest form breaks down.
        with self.assertRaises(ValueError):
            self.engine.calculate_cirp_forward_rate(1.1000, -0.60, 0.05, 1200, "EUR", "USD")

    def test_invalid_inputs_raise(self):
        for kwargs in (
            {"spot_rate": 0.0},
            {"spot_rate": -1.1},
            {"spot_rate": float("nan")},
            {"days_to_maturity": -1},
            {"base_interest_rate": float("inf")},
            {"quote_interest_rate": float("nan")},
        ):
            base = {
                "spot_rate": 1.1,
                "base_interest_rate": 0.03,
                "quote_interest_rate": 0.05,
                "days_to_maturity": 90,
            }
            base.update(kwargs)
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.calculate_cirp_forward_rate(**base)


class TestForwardPoints(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def test_pip_factor_is_ten_thousand_for_four_decimal_pairs(self):
        self.assertEqual(self.engine.pip_factor_for("EUR/USD", "USD"), 10_000.0)
        points = self.engine.calculate_forward_points(1.10545906, 1.1000, "EUR/USD", "USD")
        self.assertAlmostEqual(points, 54.59, places=2)

    def test_pip_factor_is_one_hundred_for_yen_quoted_pairs(self):
        # Regression: a pip in USD/JPY is the second decimal, not the fourth.
        self.assertEqual(self.engine.pip_factor_for("USD/JPY", "JPY"), 100.0)
        fwd = forward(150.0, 0.05, 0.005, 90, 360, 365)
        self.assertAlmostEqual(fwd, 148.33079655, places=8)
        points = self.engine.calculate_forward_points(fwd, 150.0, "USD/JPY", "JPY")
        self.assertAlmostEqual(points, -166.92, places=2)
        self.assertAlmostEqual((fwd - 150.0) * 10_000, -16_692.03, places=2)

    def test_pip_factor_override(self):
        engine = FxForwardSwapTrackingEngine(pip_factor_overrides={"USD/KRW": 100.0})
        self.assertEqual(engine.pip_factor_for("USD/KRW", "KRW"), 100.0)
        self.assertEqual(engine.pip_factor_for("EUR/USD", "USD"), 10_000.0)


class TestMarkToMarket(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def test_long_mtm_is_discounted_to_present_value(self):
        # F_fair  = 1.10 * 1.0125 / 1.0075                     = 1.10545906
        # cash    = 1,000,000 * (1.10545906 - 1.1050)          = +459.06 USD at T
        # DF(USD) = 1 / (1 + 0.05 * 90/360)                    = 0.98765432
        # PV      = 459.06 * 0.98765432                        = +453.39 USD
        report = self.engine.audit_portfolio_positions([eur_usd_position()], EUR_USD_MARKET)
        detail = report.valuation_details[0]

        self.assertEqual(detail.mtm_basis, MTM_BASIS_CIRP)
        self.assertAlmostEqual(detail.undiscounted_mtm_quote, 459.06, places=2)
        self.assertAlmostEqual(detail.quote_discount_factor, 0.98765432, places=8)
        self.assertAlmostEqual(detail.mtm_pv_quote, 453.39, places=2)
        self.assertLess(detail.mtm_pv_quote, detail.undiscounted_mtm_quote)
        self.assertEqual(report.unrealized_mtm_pv_by_quote_currency, {"USD": 453.39})
        self.assertEqual(report.unrealized_mtm_undiscounted_by_quote_currency, {"USD": 459.06})

    def test_short_mtm_is_the_exact_negative_of_the_long(self):
        report = self.engine.audit_portfolio_positions(
            [eur_usd_position(position_side="SELL")], EUR_USD_MARKET
        )
        detail = report.valuation_details[0]
        self.assertAlmostEqual(detail.undiscounted_mtm_quote, -459.06, places=2)
        self.assertAlmostEqual(detail.mtm_pv_quote, -453.39, places=2)
        self.assertEqual(detail.base_exposure, -1_000_000.0)

    def test_zero_days_to_maturity_has_unit_discount_factor(self):
        with self.assertLogs("fx_forward_and_swap_position_tracking", level="WARNING"):
            report = self.engine.audit_portfolio_positions(
                [eur_usd_position(days_to_maturity=0)], EUR_USD_MARKET
            )
        detail = report.valuation_details[0]
        self.assertEqual(detail.quote_discount_factor, 1.0)
        self.assertEqual(detail.mtm_pv_quote, detail.undiscounted_mtm_quote)
        # At zero days the forward has converged to spot: 1e6 * (1.10 - 1.1050).
        self.assertAlmostEqual(detail.undiscounted_mtm_quote, -5_000.0, places=2)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("spot window", report.warnings[0])

    def test_observed_market_forward_overrides_cirp(self):
        # CIP does not hold exactly; where an outright is observable, mark to it.
        market = {
            "EUR/USD": {
                "spot": 1.1000,
                "r_base": 0.03,
                "r_quote": 0.05,
                "market_forward_rate": 1.1060,
            }
        }
        report = self.engine.audit_portfolio_positions([eur_usd_position()], market)
        detail = report.valuation_details[0]
        self.assertEqual(detail.mtm_basis, MTM_BASIS_OBSERVED)
        self.assertAlmostEqual(detail.valuation_forward_rate, 1.1060, places=8)
        self.assertAlmostEqual(detail.cirp_forward_rate, 1.10545906, places=8)
        # 1,000,000 * (1.1060 - 1.1050) * 0.98765432
        self.assertAlmostEqual(detail.undiscounted_mtm_quote, 1_000.0, places=2)
        self.assertAlmostEqual(detail.mtm_pv_quote, 987.65, places=2)

    def test_forward_points_reported_against_spot(self):
        report = self.engine.audit_portfolio_positions([eur_usd_position()], EUR_USD_MARKET)
        detail = report.valuation_details[0]
        self.assertAlmostEqual(detail.swap_points, 54.59, places=2)
        self.assertAlmostEqual(detail.contract_forward_points, 50.0, places=2)
        self.assertEqual(detail.pip_factor, 10_000.0)

    def test_per_leg_day_count_bases_are_reported(self):
        positions = [
            eur_usd_position(
                contract_id="GBPUSD_01",
                currency_pair="GBP/USD",
                base_currency="GBP",
                agreed_forward_rate=1.2500,
                days_to_maturity=180,
            )
        ]
        market = {"GBP/USD": {"spot": 1.2500, "r_base": 0.045, "r_quote": 0.05}}
        report = self.engine.audit_portfolio_positions(positions, market)
        detail = report.valuation_details[0]
        self.assertEqual(detail.base_day_count_basis, 365)
        self.assertEqual(detail.quote_day_count_basis, 360)
        self.assertAlmostEqual(detail.cirp_forward_rate, 1.25343407, places=8)


class TestExposureAggregation(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def test_both_currencies_of_the_pair_are_tracked(self):
        report = self.engine.audit_portfolio_positions([eur_usd_position()], EUR_USD_MARKET)
        # Long 1mm EUR against short 1mm * 1.1050 USD.
        self.assertEqual(report.net_exposure_by_currency["EUR"], 1_000_000.0)
        self.assertEqual(report.net_exposure_by_currency["USD"], -1_105_000.0)

    def test_offsetting_positions_net_to_zero(self):
        positions = [
            eur_usd_position(contract_id="A", position_side="BUY"),
            eur_usd_position(contract_id="B", position_side="SELL"),
        ]
        report = self.engine.audit_portfolio_positions(positions, EUR_USD_MARKET)
        self.assertEqual(report.net_exposure_by_currency["EUR"], 0.0)
        self.assertEqual(report.net_exposure_by_currency["USD"], 0.0)
        self.assertEqual(report.unrealized_mtm_pv_by_quote_currency["USD"], 0.0)

    def test_maturity_buckets(self):
        self.assertEqual(self.engine.maturity_bucket_for(0), "0-1M")
        self.assertEqual(self.engine.maturity_bucket_for(31), "0-1M")
        self.assertEqual(self.engine.maturity_bucket_for(32), "1M-3M")
        self.assertEqual(self.engine.maturity_bucket_for(92), "1M-3M")
        self.assertEqual(self.engine.maturity_bucket_for(184), "3M-6M")
        self.assertEqual(self.engine.maturity_bucket_for(366), "6M-1Y")
        self.assertEqual(self.engine.maturity_bucket_for(367), BUCKET_BEYOND_ONE_YEAR)

    def test_exposure_split_across_buckets(self):
        positions = [
            eur_usd_position(contract_id="NEAR_A", days_to_maturity=30),
            eur_usd_position(contract_id="FAR_A", days_to_maturity=400),
        ]
        report = self.engine.audit_portfolio_positions(positions, EUR_USD_MARKET)
        self.assertEqual(report.net_exposure_by_maturity_bucket["0-1M"]["EUR"], 1_000_000.0)
        self.assertEqual(
            report.net_exposure_by_maturity_bucket[BUCKET_BEYOND_ONE_YEAR]["EUR"], 1_000_000.0
        )
        # Netted to zero at book level, but 1mm of gap risk in each bucket.
        self.assertEqual(report.net_exposure_by_currency["EUR"], 2_000_000.0)


class TestMultiCurrencyPnl(unittest.TestCase):
    """P&L in different quote currencies must not be silently summed."""

    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()
        self.positions = [
            eur_usd_position(),
            eur_usd_position(
                contract_id="FWD_JPY_01",
                currency_pair="USD/JPY",
                base_currency="USD",
                quote_currency="JPY",
                notional_base_currency=10_000_000.0,
                agreed_forward_rate=149.00,
            ),
        ]
        self.market = {
            "EUR/USD": {"spot": 1.1000, "r_base": 0.03, "r_quote": 0.05},
            "USD/JPY": {"spot": 150.00, "r_base": 0.05, "r_quote": 0.005},
        }

    def test_pnl_is_reported_per_quote_currency(self):
        report = self.engine.audit_portfolio_positions(self.positions, self.market)
        self.assertEqual(set(report.unrealized_mtm_pv_by_quote_currency), {"USD", "JPY"})
        self.assertAlmostEqual(report.unrealized_mtm_pv_by_quote_currency["USD"], 453.39, places=2)
        # F = 150 * (1 + .005*90/365) / (1 + .05*90/360) = 148.33079655
        # cash = 10,000,000 * (148.33079655 - 149.00) = -6,692,034.50 JPY
        # DF   = 1 / (1 + 0.005 * 90/365) = 0.99876864
        self.assertAlmostEqual(
            report.unrealized_mtm_pv_by_quote_currency["JPY"], -6_683_794.21, places=2
        )
        self.assertIsNone(report.net_unrealized_mtm_pv_reporting_currency)

    def test_consolidation_requires_explicit_conversion_rates(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                self.positions, self.market, reporting_currency="USD"
            )
        self.assertIn("JPY", str(ctx.exception))

    def test_consolidated_total_uses_supplied_rates(self):
        report = self.engine.audit_portfolio_positions(
            self.positions,
            self.market,
            reporting_currency="USD",
            reporting_fx_rates={"JPY": 1.0 / 150.0},
        )
        expected = 453.39 + (-6_683_794.21 / 150.0)
        self.assertEqual(report.reporting_currency, "USD")
        self.assertAlmostEqual(
            report.net_unrealized_mtm_pv_reporting_currency, expected, places=1
        )

    def test_invalid_conversion_rate_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                self.positions,
                self.market,
                reporting_currency="USD",
                reporting_fx_rates={"JPY": 0.0},
            )


class TestFxSwapLegs(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def swap_legs(self, **far_overrides):
        near = eur_usd_position(
            contract_id="SWAP_01",
            contract_type=CONTRACT_FX_SWAP,
            position_side="BUY",
            agreed_forward_rate=1.1000,
            days_to_maturity=2,
            swap_leg="NEAR",
        )
        far_kwargs = dict(
            contract_id="SWAP_01",
            contract_type=CONTRACT_FX_SWAP,
            position_side="SELL",
            agreed_forward_rate=1.1055,
            days_to_maturity=92,
            swap_leg="FAR",
        )
        far_kwargs.update(far_overrides)
        return [near, eur_usd_position(**far_kwargs)]

    def test_valid_swap_nets_to_no_outright_exposure(self):
        with self.assertLogs("fx_forward_and_swap_position_tracking", level="WARNING"):
            report = self.engine.audit_portfolio_positions(self.swap_legs(), EUR_USD_MARKET)
        self.assertEqual(report.total_open_contracts, 2)
        self.assertEqual(report.net_exposure_by_currency["EUR"], 0.0)
        # The near leg's spot-window warning must be surfaced, not swallowed.
        self.assertEqual(len(report.warnings), 1)
        # Legs land in different maturity buckets: that gap is the swap.
        self.assertEqual(report.net_exposure_by_maturity_bucket["0-1M"]["EUR"], 1_000_000.0)
        self.assertEqual(report.net_exposure_by_maturity_bucket["1M-3M"]["EUR"], -1_000_000.0)

    def test_same_direction_legs_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                self.swap_legs(position_side="BUY"), EUR_USD_MARKET
            )
        self.assertIn("reverse the near leg", str(ctx.exception))

    def test_far_leg_must_mature_after_near_leg(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                self.swap_legs(days_to_maturity=1), EUR_USD_MARKET
            )

    def test_missing_far_leg_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(self.swap_legs()[:1], EUR_USD_MARKET)
        self.assertIn("NEAR leg and one FAR leg", str(ctx.exception))

    def test_swap_row_without_leg_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                [eur_usd_position(contract_type=CONTRACT_FX_SWAP)], EUR_USD_MARKET
            )
        self.assertIn("swap_leg", str(ctx.exception))

    def test_leg_on_an_outright_forward_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position(swap_leg="NEAR")], EUR_USD_MARKET
            )


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine()

    def test_empty_book_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions([], EUR_USD_MARKET)

    def test_unknown_side_is_not_treated_as_sell(self):
        # The old behaviour silently mapped anything that was not 'BUY' to SELL,
        # inverting the sign of a typo'd row.
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                [eur_usd_position(position_side="LONG")], EUR_USD_MARKET
            )
        self.assertIn("position_side", str(ctx.exception))

    def test_side_is_case_and_whitespace_tolerant(self):
        report = self.engine.audit_portfolio_positions(
            [eur_usd_position(position_side=" buy ")], EUR_USD_MARKET
        )
        self.assertEqual(report.valuation_details[0].position_side, "BUY")

    def test_negative_or_zero_notional_raises(self):
        for notional in (0.0, -1_000_000.0, float("nan")):
            with self.subTest(notional=notional):
                with self.assertRaises(ValueError):
                    self.engine.audit_portfolio_positions(
                        [eur_usd_position(notional_base_currency=notional)], EUR_USD_MARKET
                    )

    def test_negative_days_to_maturity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position(days_to_maturity=-1)], EUR_USD_MARKET
            )

    def test_currency_pair_must_match_base_and_quote(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                [eur_usd_position(currency_pair="USD/EUR")], EUR_USD_MARKET
            )
        self.assertIn("does not match", str(ctx.exception))

    def test_identical_base_and_quote_raise(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position(currency_pair="USD/USD", base_currency="USD")],
                {"USD/USD": {"spot": 1.0, "r_base": 0.05, "r_quote": 0.05}},
            )

    def test_invalid_contract_type_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position(contract_type="NDF")], EUR_USD_MARKET
            )

    def test_duplicate_row_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                [eur_usd_position(), eur_usd_position()], EUR_USD_MARKET
            )
        self.assertIn("Duplicate", str(ctx.exception))

    def test_missing_market_data_for_pair_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions([eur_usd_position()], {})
        self.assertIn("EUR/USD", str(ctx.exception))

    def test_missing_market_data_key_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.audit_portfolio_positions(
                [eur_usd_position()], {"EUR/USD": {"spot": 1.10, "r_base": 0.03}}
            )
        self.assertIn("r_quote", str(ctx.exception))

    def test_invalid_spot_raises(self):
        for spot in (0.0, -1.10, float("nan")):
            with self.subTest(spot=spot):
                with self.assertRaises(ValueError):
                    self.engine.audit_portfolio_positions(
                        [eur_usd_position()],
                        {"EUR/USD": {"spot": spot, "r_base": 0.03, "r_quote": 0.05}},
                    )

    def test_non_numeric_market_data_raises_value_error_not_type_error(self):
        for bad in (None, "not-a-rate", object()):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.audit_portfolio_positions(
                        [eur_usd_position()],
                        {"EUR/USD": {"spot": bad, "r_base": 0.03, "r_quote": 0.05}},
                    )

    def test_non_numeric_contract_rate_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position(agreed_forward_rate="1.1050")], EUR_USD_MARKET
            )

    def test_non_numeric_reporting_rate_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position()],
                EUR_USD_MARKET,
                reporting_currency="GBP",
                reporting_fx_rates={"USD": None},
            )

    def test_invalid_observed_forward_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_positions(
                [eur_usd_position()],
                {
                    "EUR/USD": {
                        "spot": 1.10,
                        "r_base": 0.03,
                        "r_quote": 0.05,
                        "market_forward_rate": -1.10,
                    }
                },
            )


class TestEngineConfiguration(unittest.TestCase):
    def test_scalar_day_count_basis_is_rejected(self):
        # A single denominator cannot express GBP/USD or USD/JPY.
        with self.assertRaises(TypeError) as ctx:
            FxForwardSwapTrackingEngine(day_count_basis=360)
        self.assertIn("mapping", str(ctx.exception))

    def test_default_basis_can_still_be_forced(self):
        engine = FxForwardSwapTrackingEngine(default_day_count_basis=365)
        self.assertEqual(engine.day_count_basis_for("ZAR"), 365)

    def test_invalid_basis_values_rejected(self):
        with self.assertRaises(ValueError):
            FxForwardSwapTrackingEngine(day_count_basis={"AUD": 0})
        with self.assertRaises(ValueError):
            FxForwardSwapTrackingEngine(default_day_count_basis=0)

    def test_non_ascending_buckets_rejected(self):
        with self.assertRaises(ValueError):
            FxForwardSwapTrackingEngine(maturity_buckets=[("A", 90), ("B", 30)])

    def test_report_decimals_respected(self):
        engine = FxForwardSwapTrackingEngine(report_decimals=6)
        report = engine.audit_portfolio_positions([eur_usd_position()], EUR_USD_MARKET)
        self.assertAlmostEqual(
            report.valuation_details[0].mtm_pv_quote, 453.389701, places=6
        )


if __name__ == "__main__":
    unittest.main()
