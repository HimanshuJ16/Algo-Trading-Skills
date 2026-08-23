import logging
import math
import unittest

from dividend_futures import (
    ArbitrageSignal,
    DiscreteDividendEvent,
    DividendForwardAuditReport,
    DividendForwardModelingEngine,
    DividendModelError,
)


def setUpModule():
    """The engine warns by design on excluded dividends; keep the suite output quiet."""
    logging.getLogger("dividend_futures").addHandler(logging.NullHandler())
    logging.getLogger("dividend_futures").propagate = False


class TestDividendForwardModelingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DividendForwardModelingEngine(arbitrage_cost_threshold_usd=0.50)
        self.divs = [
            DiscreteDividendEvent("DIV_Q1", amount_usd=2.00, payment_time_years=0.25),
            DiscreteDividendEvent("DIV_Q3", amount_usd=2.00, payment_time_years=0.75),
        ]

    # ------------------------------------------------------------------
    # Core pricing
    # ------------------------------------------------------------------

    def test_theoretical_forward_price_with_discrete_dividends(self):
        # Spot = $100.0, r = 5.0%, T = 1.0 yr
        # Dividend 1: $2.00 at t = 0.25 yrs -> PV = 2.00 * exp(-0.05 * 0.25) = 1.975155
        # Dividend 2: $2.00 at t = 0.75 yrs -> PV = 2.00 * exp(-0.05 * 0.75) = 1.926389
        # Total PV(D) = 3.901544
        # Theo Forward F(0, T) = (100 - 3.901544) * exp(0.05) = 101.025529 ~ $101.03
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.03,
        )

        self.assertAlmostEqual(report.pv_dividends_usd, 3.9015, places=2)
        self.assertAlmostEqual(report.theoretical_forward_price, 101.03, places=2)
        self.assertEqual(report.fair_value_dividend_future_points, 4.0)
        self.assertEqual(report.arbitrage_opportunity, "NO_ARBITRAGE")

    def test_forward_price_identity_holds_both_ways(self):
        # F = (S - PV(D)) e^{rT} must equal S e^{rT} - FV(D). Independent derivation.
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.03,
        )
        via_fv = 100.0 * math.exp(0.05) - report.fv_dividends_usd
        self.assertAlmostEqual(report.theoretical_forward_price, via_fv, places=3)

        # And against a hand-computed value that never touches the implementation.
        expected_pv = 2.0 * math.exp(-0.05 * 0.25) + 2.0 * math.exp(-0.05 * 0.75)
        self.assertAlmostEqual(report.pv_dividends_usd, round(expected_pv, 4), places=4)
        self.assertAlmostEqual(
            report.theoretical_forward_price,
            round((100.0 - round(expected_pv, 4)) * math.exp(0.05), 4),
            places=4,
        )

    def test_zero_dividends_reduces_to_pure_cost_of_carry(self):
        report = self.engine.audit_forward_arbitrage(
            symbol="NODIV", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=[], market_forward_price=105.13,
        )
        self.assertEqual(report.pv_dividends_usd, 0.0)
        self.assertEqual(report.fair_value_dividend_future_points, 0.0)
        self.assertAlmostEqual(report.theoretical_forward_price, 100.0 * math.exp(0.05), places=3)

    def test_negative_risk_free_rate_is_supported(self):
        # EUR rates were negative for years; a negative r must not be rejected.
        report = self.engine.audit_forward_arbitrage(
            symbol="SX5E", spot_price=100.0, maturity_years=1.0, risk_free_rate=-0.005,
            dividends=[], market_forward_price=99.50,
        )
        self.assertAlmostEqual(report.theoretical_forward_price, 100.0 * math.exp(-0.005), places=3)
        self.assertLess(report.theoretical_forward_price, 100.0)

    # ------------------------------------------------------------------
    # Arbitrage classification (both directions)
    # ------------------------------------------------------------------

    def test_cash_and_carry_arbitrage_detection(self):
        # Market forward = $104.00 vs Theo $101.0255 -> spread $2.9745 > $0.50 threshold.
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=104.00,
        )
        self.assertEqual(report.arbitrage_opportunity, "ARBITRAGE_SHORT_FORWARD_LONG_SPOT")
        self.assertGreater(report.estimated_gross_profit_usd, 2.0)
        self.assertAlmostEqual(report.mispricing_spread_usd, 2.9745, places=3)

    def test_reverse_cash_and_carry_arbitrage_detection(self):
        # Previously untested branch: market forward far below theoretical.
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=95.00,
        )
        self.assertEqual(report.arbitrage_opportunity, "ARBITRAGE_LONG_FORWARD_SHORT_SPOT")
        self.assertAlmostEqual(report.mispricing_spread_usd, -6.0255, places=3)
        self.assertAlmostEqual(report.estimated_gross_profit_usd, 6.03, places=2)
        self.assertAlmostEqual(report.estimated_net_profit_usd, 5.53, places=2)

    def test_gross_profit_is_gross_and_net_profit_is_net(self):
        # Regression: `estimated_gross_profit_usd` previously held the COST-NET figure,
        # contradicting its own name and understating the signal used for sizing.
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=104.00,
        )
        self.assertAlmostEqual(report.estimated_gross_profit_usd, abs(report.mispricing_spread_usd), places=2)
        self.assertAlmostEqual(
            report.estimated_net_profit_usd,
            abs(report.mispricing_spread_usd) - report.applied_cost_threshold_usd,
            places=2,
        )
        self.assertEqual(report.applied_cost_threshold_usd, 0.50)
        self.assertGreater(report.estimated_gross_profit_usd, report.estimated_net_profit_usd)

    def test_spread_inside_threshold_is_no_arbitrage(self):
        # Theo is 101.0255; a market forward 0.40 above it sits inside the 0.50 cost band.
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.4255,
        )
        self.assertAlmostEqual(report.mispricing_spread_usd, 0.40, places=3)
        self.assertEqual(report.arbitrage_opportunity, "NO_ARBITRAGE")
        self.assertEqual(report.estimated_gross_profit_usd, 0.0)
        self.assertEqual(report.estimated_net_profit_usd, 0.0)

    def test_threshold_must_be_strictly_exceeded(self):
        # The standards doc requires the spread to EXCEED round-trip costs, not merely equal.
        # Theo rounds to 101.0256, so a market forward of 101.5256 sits exactly one
        # threshold above it.
        at_threshold = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.5256,
        )
        self.assertAlmostEqual(at_threshold.mispricing_spread_usd, 0.50, places=4)
        self.assertEqual(at_threshold.arbitrage_opportunity, "NO_ARBITRAGE")

        just_above = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.5356,
        )
        self.assertEqual(just_above.arbitrage_opportunity, "ARBITRAGE_SHORT_FORWARD_LONG_SPOT")

    def test_asymmetric_reverse_threshold_suppresses_uneconomic_short_leg(self):
        # The reverse trade needs stock borrow; with a realistic 8.00 borrow-inclusive cost
        # the same -6.03 spread is no longer actionable, while the forward leg is unchanged.
        engine = DividendForwardModelingEngine(
            arbitrage_cost_threshold_usd=0.50, reverse_arbitrage_cost_threshold_usd=8.00
        )
        cheap = engine.audit_forward_arbitrage(
            symbol="HTB", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=95.00,
        )
        self.assertEqual(cheap.arbitrage_opportunity, "NO_ARBITRAGE")

        rich = engine.audit_forward_arbitrage(
            symbol="HTB", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=104.00,
        )
        self.assertEqual(rich.arbitrage_opportunity, "ARBITRAGE_SHORT_FORWARD_LONG_SPOT")
        self.assertEqual(rich.applied_cost_threshold_usd, 0.50)

    # ------------------------------------------------------------------
    # Accrual window: ex-date eligibility, past dividends, expiry boundary
    # ------------------------------------------------------------------

    def test_dividend_after_maturity_is_excluded(self):
        # The skill's own standard: dividends with t_i > T MUST be excluded.
        divs = self.divs + [DiscreteDividendEvent("DIV_LATE", amount_usd=2.00, payment_time_years=1.5)]
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=divs, market_forward_price=101.03,
        )
        self.assertAlmostEqual(report.pv_dividends_usd, 3.9015, places=3)
        self.assertEqual(report.fair_value_dividend_future_points, 4.0)
        self.assertIn("DIV_LATE", report.excluded_dividend_ids)

    def test_already_ex_dividend_is_excluded_not_silently_priced(self):
        # Regression: a dividend that already went ex was included, inflating PV(D) by
        # 2.0252, cutting the theoretical forward to ~98.9, and manufacturing a false
        # ARBITRAGE_SHORT_FORWARD_LONG_SPOT against an honest market quote.
        stale = [DiscreteDividendEvent("DIV_PAST", amount_usd=2.00, payment_time_years=-0.25)]
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=stale, market_forward_price=105.13,
        )
        self.assertEqual(report.pv_dividends_usd, 0.0)
        self.assertEqual(report.arbitrage_opportunity, "NO_ARBITRAGE")
        self.assertIn("DIV_PAST", report.excluded_dividend_ids)
        self.assertTrue(any("excluded" in w for w in report.warnings))

    def test_dividend_at_exactly_maturity_is_included(self):
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=[DiscreteDividendEvent("DIV_AT_T", amount_usd=2.00, payment_time_years=1.0)],
            market_forward_price=103.0,
        )
        self.assertAlmostEqual(report.pv_dividends_usd, round(2.0 * math.exp(-0.05), 4), places=4)
        self.assertEqual(report.excluded_dividend_ids, [])

    def test_accrual_window_start_bounds_the_dividend_future(self):
        # A dividend-index contract accrues over a bounded period and resets after expiry;
        # dividends before the window start belong to the previous contract.
        divs = [
            DiscreteDividendEvent("PREV_CONTRACT", amount_usd=3.00, payment_time_years=0.20),
            DiscreteDividendEvent("THIS_CONTRACT", amount_usd=2.00, payment_time_years=0.60),
        ]
        report = self.engine.audit_forward_arbitrage(
            symbol="SX5E", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=divs, market_forward_price=103.0, accrual_start_years=0.40,
        )
        self.assertEqual(report.fair_value_dividend_future_points, 2.00)
        self.assertIn("PREV_CONTRACT", report.excluded_dividend_ids)

    def test_eligibility_uses_ex_date_while_discounting_uses_payment_date(self):
        # Goes ex just before maturity but pays after it: still owned by the forward holder,
        # and the cash flow must be discounted from the later payment date.
        div = DiscreteDividendEvent(
            "DIV_EX_BEFORE_PAY", amount_usd=2.00, payment_time_years=1.10, ex_time_years=0.95
        )
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=[div], market_forward_price=103.0,
        )
        self.assertEqual(report.excluded_dividend_ids, [])
        # Discounted from 1.10, not from the 1.0 maturity or the 0.95 ex-date.
        self.assertAlmostEqual(report.pv_dividends_usd, round(2.0 * math.exp(-0.05 * 1.10), 4), places=4)

    def test_payment_before_ex_date_is_rejected(self):
        bad = DiscreteDividendEvent("BAD", amount_usd=2.0, payment_time_years=0.3, ex_time_years=0.5)
        with self.assertRaises(DividendModelError):
            self.engine.audit_forward_arbitrage(
                symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
                dividends=[bad], market_forward_price=101.0,
            )

    # ------------------------------------------------------------------
    # Gross vs net: withholding tax and special dividends
    # ------------------------------------------------------------------

    def test_withholding_tax_reduces_forward_pv_but_not_index_accrual(self):
        # Eurex FEXD settles on "the cumulative total of the relevant GROSS dividends";
        # CME SDA accumulates ordinary GROSS dividends. Withholding must not net them down.
        # The cash-and-carry holder, by contrast, receives only the net cash.
        taxed = [
            DiscreteDividendEvent("D1", amount_usd=2.00, payment_time_years=0.25, withholding_tax_pct=0.15),
            DiscreteDividendEvent("D2", amount_usd=2.00, payment_time_years=0.75, withholding_tax_pct=0.15),
        ]
        report = self.engine.audit_forward_arbitrage(
            symbol="SX5E", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=taxed, market_forward_price=102.0,
        )
        expected_pv = 0.85 * (2.0 * math.exp(-0.05 * 0.25) + 2.0 * math.exp(-0.05 * 0.75))
        self.assertAlmostEqual(report.pv_dividends_usd, round(expected_pv, 4), places=4)
        # Gross accrual is untouched by the 15% withholding.
        self.assertEqual(report.fair_value_dividend_future_points, 4.00)
        # A taxed holder loses less dividend, so the forward is HIGHER than the untaxed case.
        untaxed = self.engine.audit_forward_arbitrage(
            symbol="SX5E", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=102.0,
        )
        self.assertGreater(report.theoretical_forward_price, untaxed.theoretical_forward_price)

    def test_special_dividends_excluded_from_index_accrual_but_priced_in_forward(self):
        # CME: "special or extraordinary dividends are not included as dividend points".
        # They still depress the forward, because the shareholder does receive the cash.
        divs = self.divs + [
            DiscreteDividendEvent("SPECIAL", amount_usd=5.00, payment_time_years=0.50, is_special=True)
        ]
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=divs, market_forward_price=101.03,
        )
        self.assertEqual(report.fair_value_dividend_future_points, 4.00)  # ordinary only
        expected_pv = (
            2.0 * math.exp(-0.05 * 0.25) + 2.0 * math.exp(-0.05 * 0.75) + 5.0 * math.exp(-0.05 * 0.50)
        )
        self.assertAlmostEqual(report.pv_dividends_usd, round(expected_pv, 4), places=4)

    def test_invalid_withholding_tax_is_rejected(self):
        # tax >= 1 flips the net dividend negative; tax < 0 inflates it above gross.
        for bad_tax in (1.0, 1.5, -0.5):
            with self.subTest(bad_tax=bad_tax):
                with self.assertRaises(DividendModelError):
                    self.engine.calculate_dividend_present_value(
                        [DiscreteDividendEvent("X", 2.0, 0.5, withholding_tax_pct=bad_tax)],
                        0.05, 1.0,
                    )

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_non_finite_inputs_are_rejected(self):
        # Regression: a NaN spot previously returned a confident "NO_ARBITRAGE", because
        # every NaN comparison is False.
        for kwargs in (
            {"spot_price": float("nan")},
            {"spot_price": float("inf")},
            {"maturity_years": float("nan")},
            {"risk_free_rate": float("nan")},
            {"market_forward_price": float("inf")},
        ):
            with self.subTest(**kwargs):
                call = {
                    "symbol": "X", "spot_price": 100.0, "maturity_years": 1.0,
                    "risk_free_rate": 0.05, "dividends": self.divs,
                    "market_forward_price": 101.0,
                }
                call.update(kwargs)
                with self.assertRaises(DividendModelError):
                    self.engine.audit_forward_arbitrage(**call)

    def test_non_finite_dividend_amount_is_rejected(self):
        with self.assertRaises(DividendModelError):
            self.engine.calculate_dividend_present_value(
                [DiscreteDividendEvent("X", float("nan"), 0.5)], 0.05, 1.0
            )

    def test_invalid_spot_and_maturity_are_rejected(self):
        for kwargs in (
            {"spot_price": 0.0},
            {"spot_price": -100.0},
            {"maturity_years": 0.0},
            {"maturity_years": -1.0},
            {"market_forward_price": -5.0},
        ):
            with self.subTest(**kwargs):
                call = {
                    "symbol": "X", "spot_price": 100.0, "maturity_years": 1.0,
                    "risk_free_rate": 0.05, "dividends": self.divs,
                    "market_forward_price": 101.0,
                }
                call.update(kwargs)
                with self.assertRaises(DividendModelError):
                    self.engine.audit_forward_arbitrage(**call)

    def test_negative_dividend_amount_is_rejected(self):
        with self.assertRaises(DividendModelError):
            self.engine.calculate_dividend_present_value(
                [DiscreteDividendEvent("X", -2.0, 0.5)], 0.05, 1.0
            )

    def test_negative_cost_threshold_is_rejected(self):
        with self.assertRaises(DividendModelError):
            DividendForwardModelingEngine(arbitrage_cost_threshold_usd=-1.0)
        with self.assertRaises(DividendModelError):
            DividendForwardModelingEngine(reverse_arbitrage_cost_threshold_usd=-1.0)

    def test_accrual_start_at_or_after_maturity_is_rejected(self):
        with self.assertRaises(DividendModelError):
            self.engine.audit_forward_arbitrage(
                symbol="X", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
                dividends=self.divs, market_forward_price=101.0, accrual_start_years=1.0,
            )

    def test_dividends_exceeding_spot_warn_on_non_positive_forward(self):
        # Not rejected -- deep-discount and mis-scaled feeds both land here -- but the
        # report must say so rather than emitting a confident arbitrage signal.
        report = self.engine.audit_forward_arbitrage(
            symbol="BADFEED", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=[DiscreteDividendEvent("HUGE", amount_usd=200.0, payment_time_years=0.5)],
            market_forward_price=101.0,
        )
        self.assertLess(report.theoretical_forward_price, 0.0)
        self.assertTrue(any("non-positive" in w for w in report.warnings))

    def test_rate_unit_mistake_overflows_cleanly(self):
        # Passing 5 for "5%" instead of 0.05 is a realistic unit error. On a long horizon
        # it overflows math.exp; that must surface as a modelling error, not an
        # unhandled OverflowError escaping the pricing engine.
        with self.assertRaises(DividendModelError):
            self.engine.audit_forward_arbitrage(
                symbol="X", spot_price=100.0, maturity_years=1000.0, risk_free_rate=1000.0,
                dividends=[], market_forward_price=101.0,
            )

    def test_none_dividend_list_is_rejected_clearly(self):
        # An agent reaching for None to mean "no dividends" should get a directed message,
        # not a raw TypeError from iterating None.
        with self.assertRaises(DividendModelError):
            self.engine.audit_forward_arbitrage(
                symbol="X", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
                dividends=None, market_forward_price=101.0,
            )
        with self.assertRaises(DividendModelError):
            self.engine.calculate_dividend_present_value(None, 0.05, 1.0)

    def test_non_numeric_prices_are_rejected(self):
        # bool subclasses int, so it must be screened out explicitly rather than
        # silently pricing True as 1.0.
        for bad in (True, None, "100"):
            with self.subTest(bad=bad):
                with self.assertRaises(DividendModelError):
                    self.engine.audit_forward_arbitrage(
                        symbol="X", spot_price=bad, maturity_years=1.0, risk_free_rate=0.05,
                        dividends=[], market_forward_price=101.0,
                    )

    # ------------------------------------------------------------------
    # Shape and API compatibility
    # ------------------------------------------------------------------

    def test_signal_enum_compares_equal_to_legacy_strings(self):
        self.assertEqual(ArbitrageSignal.NO_ARBITRAGE, "NO_ARBITRAGE")
        self.assertEqual(
            ArbitrageSignal.ARBITRAGE_SHORT_FORWARD_LONG_SPOT, "ARBITRAGE_SHORT_FORWARD_LONG_SPOT"
        )

    def test_report_type_and_rate_units(self):
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=self.divs, market_forward_price=101.03,
        )
        self.assertIsInstance(report, DividendForwardAuditReport)
        self.assertEqual(report.risk_free_rate_pct, 5.0)  # stored as percent, not decimal


if __name__ == "__main__":
    unittest.main()
