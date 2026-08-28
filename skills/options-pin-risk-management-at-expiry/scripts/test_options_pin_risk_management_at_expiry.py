"""Unit tests for the expiry pin risk engine.

Expected values are derived independently of the implementation: pin distances
are computed by hand in the test docstrings, share deltas follow from the
delivery direction of a call/put on each side, and the exercise-by-exception
boundary cases are the literal $0.01 threshold from OCC Rule 805.
"""

import logging
import unittest

from options_pin_risk_management_at_expiry import (
    OCC_EX_BY_EX_THRESHOLD_USD,
    ExpiryOptionPosition,
    OptionsPinRiskManagementEngine,
    PinRiskPolicyConfig,
)

logging.disable(logging.CRITICAL)


def make_position(**overrides) -> ExpiryOptionPosition:
    """Short 10 AAPL $100 calls, spot $100.50, 1.0h before the close."""
    kwargs = dict(
        symbol="AAPL240119C00100000",
        underlying_symbol="AAPL",
        strike=100.0,
        option_type="CALL",
        position_qty=-10,
        spot_price=100.50,
        hours_to_trading_close=1.0,
    )
    kwargs.update(overrides)
    return ExpiryOptionPosition(**kwargs)


class TestInputValidation(unittest.TestCase):
    """Corrupt or meaningless inputs must be rejected, never scored as safe."""

    def test_nan_spot_price_raises(self):
        # Regression: NaN <= 0 is False, so a bare positivity guard lets NaN
        # through; every downstream comparison is then False and the engine
        # reports LOW_PIN_RISK_SAFE on unusable data.
        with self.assertRaises(ValueError):
            make_position(spot_price=float("nan"))

    def test_infinite_spot_price_raises(self):
        with self.assertRaises(ValueError):
            make_position(spot_price=float("inf"))

    def test_nan_hours_to_close_raises(self):
        with self.assertRaises(ValueError):
            make_position(hours_to_trading_close=float("nan"))

    def test_zero_quantity_raises(self):
        # A flat position must never produce an action directive.
        with self.assertRaises(ValueError):
            make_position(position_qty=0)

    def test_non_positive_strike_and_multiplier_raise(self):
        with self.assertRaises(ValueError):
            make_position(strike=0.0)
        with self.assertRaises(ValueError):
            make_position(contract_multiplier=0.0)

    def test_fractional_quantity_raises(self):
        # int(2.9) is 2, which would silently size the position 30% light.
        with self.assertRaises(ValueError):
            make_position(position_qty=2.9)
        self.assertEqual(make_position(position_qty=-3.0).position_qty, -3)

    def test_unrecognised_option_type_raises(self):
        # A typo must not be silently coerced into a call.
        with self.assertRaises(ValueError):
            make_position(option_type="C")

    def test_unrecognised_settlement_type_raises(self):
        with self.assertRaises(ValueError):
            make_position(settlement_type="PHYSICALLY")

    def test_case_insensitive_enums_are_normalised(self):
        pos = make_position(option_type="call", settlement_type="physical")
        self.assertEqual(pos.option_type, "CALL")
        self.assertEqual(pos.settlement_type, "PHYSICAL")

    def test_invalid_policy_config_raises(self):
        with self.assertRaises(ValueError):
            PinRiskPolicyConfig(pin_distance_pct=0.0)
        with self.assertRaises(ValueError):
            PinRiskPolicyConfig(pin_cutoff_hours=-1.0)
        with self.assertRaises(ValueError):
            PinRiskPolicyConfig(pin_distance_abs_usd=-0.25)


class TestPinZoneDetection(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_short_call_inside_pin_zone(self):
        """|100.50 - 100| / 100.50 * 100 = 0.497512...%, inside the 1.0% band."""
        report = self.engine.audit_position_pin_risk(make_position())
        self.assertAlmostEqual(report.pin_distance_pct, 0.497512, places=6)
        self.assertEqual(report.pin_distance_usd, 0.5)
        self.assertTrue(report.is_in_pin_zone)
        self.assertTrue(report.is_pin_risk_high)
        self.assertEqual(report.status, "HIGH_PIN_RISK_ACTION_REQUIRED")
        self.assertEqual(report.recommended_action, "CLOSE_POSITION_BEFORE_EXPIRY")

    def test_deep_out_of_the_money_short_call_holds(self):
        """|90 - 100| / 90 * 100 = 11.11%, far outside the 1.0% band."""
        report = self.engine.audit_position_pin_risk(make_position(spot_price=90.0))
        self.assertFalse(report.is_in_pin_zone)
        self.assertFalse(report.is_pin_risk_high)
        self.assertEqual(report.status, "LOW_PIN_RISK_SAFE")
        self.assertEqual(report.recommended_action, "HOLD_TO_EXPIRY")

    def test_distance_band_boundary_is_inclusive(self):
        """S = 101.0101...: |S - 100| / S * 100 = exactly 1.0%."""
        spot = 100.0 / 0.99
        inside = self.engine.audit_position_pin_risk(make_position(spot_price=spot))
        self.assertTrue(inside.is_in_pin_zone)
        outside = self.engine.audit_position_pin_risk(
            make_position(spot_price=spot * 1.01)
        )
        self.assertFalse(outside.is_in_pin_zone)

    def test_cutoff_hours_boundary_is_inclusive(self):
        at_cutoff = self.engine.audit_position_pin_risk(
            make_position(hours_to_trading_close=2.0)
        )
        self.assertTrue(at_cutoff.is_in_pin_zone)
        before_cutoff = self.engine.audit_position_pin_risk(
            make_position(hours_to_trading_close=2.01)
        )
        self.assertFalse(before_cutoff.is_in_pin_zone)
        self.assertEqual(before_cutoff.recommended_action, "HOLD_TO_EXPIRY")

    def test_absolute_band_catches_what_the_percentage_band_misses(self):
        """A $0.06 gap on a $5.00 underlying is 1.2% -- outside a 1% band, but
        well inside any realistic after-hours move."""
        pos = make_position(strike=5.0, spot_price=5.06)
        self.assertFalse(
            OptionsPinRiskManagementEngine()
            .audit_position_pin_risk(pos)
            .is_in_pin_zone
        )
        widened = OptionsPinRiskManagementEngine(
            PinRiskPolicyConfig(pin_distance_abs_usd=0.10)
        )
        self.assertTrue(widened.audit_position_pin_risk(pos).is_in_pin_zone)

    def test_absolute_band_is_disabled_by_default(self):
        self.assertIsNone(PinRiskPolicyConfig().pin_distance_abs_usd)


class TestExerciseByExceptionBoundary(unittest.TestCase):
    """OCC Rule 805 turns on being in the money by $0.01 or more per share."""

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_exactly_one_cent_in_the_money_is_auto_exercised(self):
        # Regression: 1234.01 - 1234.0 evaluates to 0.009999999999990905 in
        # binary floating point, so a raw comparison against 0.01 classifies an
        # underlying that closed exactly on the boundary as out of the money.
        for strike, spot in [(45.0, 45.01), (3.0, 3.01), (1234.0, 1234.01)]:
            with self.subTest(strike=strike):
                report = self.engine.audit_position_pin_risk(
                    make_position(strike=strike, spot_price=spot)
                )
                self.assertEqual(report.moneyness_usd, OCC_EX_BY_EX_THRESHOLD_USD)
                self.assertTrue(report.is_itm_at_ex_by_ex_threshold)
                self.assertEqual(report.default_expiry_outcome, "AUTO_EXERCISED")

    def test_half_a_cent_in_the_money_is_below_the_threshold(self):
        report = self.engine.audit_position_pin_risk(
            make_position(strike=100.0, spot_price=100.005)
        )
        self.assertFalse(report.is_itm_at_ex_by_ex_threshold)
        self.assertEqual(report.default_expiry_outcome, "EXPIRES_WORTHLESS")

    def test_put_moneyness_is_the_mirror_of_call_moneyness(self):
        """Regression: option_type used to be accepted and never read, so a put
        and a call at the same distance produced identical verdicts."""
        call = self.engine.audit_position_pin_risk(
            make_position(option_type="CALL", spot_price=100.40)
        )
        put = self.engine.audit_position_pin_risk(
            make_position(option_type="PUT", spot_price=100.40)
        )
        self.assertAlmostEqual(call.moneyness_usd, 0.40, places=6)
        self.assertAlmostEqual(put.moneyness_usd, -0.40, places=6)
        self.assertTrue(call.is_itm_at_ex_by_ex_threshold)
        self.assertFalse(put.is_itm_at_ex_by_ex_threshold)


class TestShareDeltaAndNotional(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_short_call_delivers_shares_away(self):
        """10 contracts x 100 = 1,000 shares delivered by the writer."""
        report = self.engine.audit_position_pin_risk(make_position())
        self.assertEqual(report.assigned_share_delta, -1000.0)
        self.assertEqual(report.assigned_share_notional_usd, 100_500.0)  # 1000 x 100.50
        self.assertEqual(report.assignment_cash_usd, 100_000.0)          # 1000 x 100.00

    def test_short_put_receives_shares(self):
        """A short put assigned buys stock at the strike: +1,000 shares."""
        report = self.engine.audit_position_pin_risk(
            make_position(option_type="PUT", spot_price=99.60)
        )
        self.assertEqual(report.assigned_share_delta, 1000.0)
        # Regression: the cash the writer must fund is struck at $100.00, not at
        # the $99.60 spot -- and spot understates it exactly when the put is ITM.
        self.assertEqual(report.assignment_cash_usd, 100_000.0)
        self.assertEqual(report.assigned_share_notional_usd, 99_600.0)
        self.assertGreater(report.assignment_cash_usd, report.assigned_share_notional_usd)

    def test_long_call_receives_shares(self):
        report = self.engine.audit_position_pin_risk(
            make_position(position_qty=5, spot_price=100.02)
        )
        self.assertEqual(report.assigned_share_delta, 500.0)

    def test_long_put_delivers_shares_away(self):
        report = self.engine.audit_position_pin_risk(
            make_position(position_qty=5, option_type="PUT", spot_price=99.60)
        )
        self.assertEqual(report.assigned_share_delta, -500.0)

    def test_non_standard_multiplier_is_honoured(self):
        """Regression: the multiplier used to be hard-coded at 100, so an
        adjusted contract or a mini contract was sized wrongly."""
        report = self.engine.audit_position_pin_risk(
            make_position(position_qty=-10, contract_multiplier=150.0)
        )
        self.assertEqual(report.assigned_share_delta, -1500.0)
        self.assertEqual(report.assigned_share_notional_usd, 150_750.0)  # 1500 x 100.50


class TestSideAndSettlementResolution(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_out_of_the_money_short_is_still_high_risk(self):
        """A holder may file a contrary exercise advice to exercise an option
        that would otherwise be abandoned, so an OTM short is not safe."""
        report = self.engine.audit_position_pin_risk(make_position(spot_price=99.80))
        self.assertFalse(report.is_itm_at_ex_by_ex_threshold)
        self.assertEqual(report.default_expiry_outcome, "EXPIRES_WORTHLESS")
        self.assertTrue(report.is_pin_risk_high)
        self.assertEqual(report.recommended_action, "CLOSE_POSITION_BEFORE_EXPIRY")
        self.assertIn("contrary exercise advice", report.audit_notes)

    def test_long_in_the_money_flags_delivery_and_dne_tradeoff(self):
        """5 contracts, $0.02 ITM: $10 of intrinsic against $50,000 of delivery."""
        report = self.engine.audit_position_pin_risk(
            make_position(position_qty=5, spot_price=100.02)
        )
        self.assertTrue(report.is_pin_risk_high)
        self.assertTrue(report.dne_eligible)
        self.assertEqual(report.intrinsic_forfeited_if_dne_usd, 10.0)
        self.assertEqual(report.assignment_cash_usd, 50_000.0)

    def test_long_out_of_the_money_is_a_holder_election_not_a_risk(self):
        """Regression: a pinned long used to be flagged HIGH with an ISSUE_DNE
        directive, but a do-not-exercise instruction is a no-op on a contract
        that is already abandoned by default -- and the holder, not a
        counterparty, decides the outcome."""
        report = self.engine.audit_position_pin_risk(
            make_position(position_qty=5, spot_price=99.98)
        )
        self.assertTrue(report.is_in_pin_zone)
        self.assertFalse(report.is_pin_risk_high)
        self.assertFalse(report.dne_eligible)
        self.assertEqual(report.status, "PIN_ZONE_HOLDER_ELECTION")
        self.assertEqual(report.recommended_action, "NO_ACTION_HOLDER_ELECTS")

    def test_cash_settled_position_has_no_share_assignment(self):
        """Regression: a cash-settled index option used to be reported with a
        six-figure 'assigned share notional' it can never deliver."""
        report = self.engine.audit_position_pin_risk(
            make_position(underlying_symbol="SPX", settlement_type="CASH")
        )
        self.assertTrue(report.is_in_pin_zone)
        self.assertFalse(report.is_pin_risk_high)
        self.assertEqual(report.status, "CASH_SETTLED_NO_ASSIGNMENT_AMBIGUITY")
        self.assertEqual(report.recommended_action, "REVIEW_CASH_SETTLEMENT_EXPOSURE")
        self.assertEqual(report.assigned_share_delta, 0.0)
        self.assertEqual(report.assigned_share_notional_usd, 0.0)

    def test_after_the_close_the_directive_is_not_a_close_order(self):
        """Once trading has ceased the position cannot be closed in the market,
        so emitting a close directive would be an unexecutable instruction."""
        report = self.engine.audit_position_pin_risk(
            make_position(hours_to_trading_close=-0.5)
        )
        self.assertTrue(report.is_pin_risk_high)
        self.assertEqual(report.recommended_action, "POST_CLOSE_EXPOSURE_REVIEW")
        self.assertIn("TRADING_WINDOW_ALREADY_CLOSED", report.data_quality_flags)


class TestDataQualityFlags(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def test_provisional_price_is_flagged_by_default(self):
        report = self.engine.audit_position_pin_risk(make_position())
        self.assertIn("PROVISIONAL_PRICE_NOT_OFFICIAL_CLOSE", report.data_quality_flags)

    def test_official_close_clears_the_flag(self):
        report = self.engine.audit_position_pin_risk(
            make_position(price_is_official_close=True)
        )
        self.assertNotIn(
            "PROVISIONAL_PRICE_NOT_OFFICIAL_CLOSE", report.data_quality_flags
        )


class TestPortfolioAggregation(unittest.TestCase):
    """The spread-leg failure mode a per-position audit structurally cannot see."""

    def setUp(self):
        self.engine = OptionsPinRiskManagementEngine()

    def _vertical(self, long_strike: float):
        return [
            make_position(symbol="SHORT_100C", strike=100.0, position_qty=-10,
                          spot_price=100.20),
            make_position(symbol="LONG_LEG", strike=long_strike, position_qty=10,
                          spot_price=100.20),
        ]

    def test_out_of_the_money_long_leg_leaves_the_short_naked(self):
        """Short 10 x $100 calls pinned at $100.20; long 10 x $105 calls are
        $4.80 out of the money and will not be exercised, so they deliver
        nothing. Worst case is -1,000 shares over the weekend."""
        report = self.engine.audit_portfolio_pin_risk(self._vertical(105.0))
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "UNPAIRED_SHORT_PIN_EXPOSURE")
        self.assertEqual(exposure.unpaired_short_shares, {"CALL": 1000.0})
        self.assertEqual(exposure.min_net_share_delta, -1000.0)
        self.assertEqual(exposure.max_net_share_delta, 0.0)
        self.assertEqual(exposure.share_delta_uncertainty, 1000.0)
        self.assertEqual(exposure.notional_uncertainty_usd, 100_200.0)  # 1000 x 100.20
        self.assertEqual(report.status, "ACTION_REQUIRED")
        self.assertEqual(report.action_required_symbols, ["SHORT_100C"])

    def test_in_the_money_long_leg_covers_the_delivery(self):
        """Long 10 x $95 calls are $5.20 in the money and outside the pin band,
        so their 1,000 shares are delivered with certainty: the account ends up
        either flat or long 1,000 shares, never short."""
        report = self.engine.audit_portfolio_pin_risk(self._vertical(95.0))
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "PINNED_SHORT_DELIVERY_COVERED")
        self.assertEqual(exposure.unpaired_short_shares, {})
        self.assertEqual(exposure.certain_share_delta, 1000.0)
        self.assertEqual(exposure.min_net_share_delta, 0.0)
        self.assertEqual(exposure.max_net_share_delta, 1000.0)

    def test_coverage_is_netted_in_shares_not_contracts(self):
        """10 short contracts of 100 shares against 10 long contracts of 10
        shares is 900 naked shares, not a covered spread. Matching on contract
        counts alone would report it covered."""
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="SHORT_100C", strike=100.0, position_qty=-10,
                          spot_price=100.20, contract_multiplier=100.0),
            make_position(symbol="LONG_MINI", strike=95.0, position_qty=10,
                          spot_price=100.20, contract_multiplier=10.0),
        ])
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "UNPAIRED_SHORT_PIN_EXPOSURE")
        self.assertEqual(exposure.unpaired_short_shares, {"CALL": 900.0})

    def test_wrong_type_long_leg_does_not_cover_a_short(self):
        """An in-the-money long call delivers nothing against an assigned short
        put -- the put assignment buys stock, the call exercise buys more."""
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="SHORT_PUT", option_type="PUT", strike=100.0,
                          position_qty=-10, spot_price=100.20),
            make_position(symbol="LONG_CALL", option_type="CALL", strike=95.0,
                          position_qty=10, spot_price=100.20),
        ])
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "UNPAIRED_SHORT_PIN_EXPOSURE")
        self.assertEqual(exposure.unpaired_short_shares, {"PUT": 1000.0})

    def test_a_long_leg_that_is_itself_pinned_does_not_cover(self):
        """A long leg one cent from the strike is as uncertain as the short it
        is meant to hedge, so it cannot be counted as reliable delivery."""
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="SHORT_100C", strike=100.0, position_qty=-10,
                          spot_price=100.20),
            make_position(symbol="LONG_99_5C", strike=99.5, position_qty=10,
                          spot_price=100.20),
        ])
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "UNPAIRED_SHORT_PIN_EXPOSURE")
        self.assertEqual(exposure.min_net_share_delta, -1000.0)
        self.assertEqual(exposure.max_net_share_delta, 1000.0)

    def test_book_with_no_pinned_legs_reports_no_exposure(self):
        report = self.engine.audit_portfolio_pin_risk(
            [make_position(spot_price=90.0), make_position(symbol="B", spot_price=90.0)]
        )
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.status, "NO_PIN_EXPOSURE")
        self.assertEqual(exposure.share_delta_uncertainty, 0.0)
        self.assertEqual(report.status, "NO_ACTION_REQUIRED")
        self.assertEqual(report.action_required_symbols, [])

    def test_positions_are_grouped_and_sorted_per_underlying(self):
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="MSFT_C", underlying_symbol="MSFT"),
            make_position(symbol="AAPL_C", underlying_symbol="AAPL"),
        ])
        self.assertEqual(
            [e.underlying_symbol for e in report.underlying_exposures],
            ["AAPL", "MSFT"],
        )

    def test_conflicting_spot_prices_for_one_underlying_are_flagged(self):
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="A", spot_price=100.20),
            make_position(symbol="B", spot_price=100.35),
        ])
        self.assertIn("INCONSISTENT_SPOT_PRICE", report.underlying_exposures[0].notes)

    def test_cash_settled_legs_do_not_enter_the_share_range(self):
        report = self.engine.audit_portfolio_pin_risk([
            make_position(symbol="SPX_C", underlying_symbol="SPX",
                          settlement_type="CASH"),
        ])
        exposure = report.underlying_exposures[0]
        self.assertEqual(exposure.pinned_position_count, 0)
        self.assertEqual(exposure.share_delta_uncertainty, 0.0)

    def test_empty_book_is_handled(self):
        report = self.engine.audit_portfolio_pin_risk([])
        self.assertEqual(report.position_reports, [])
        self.assertEqual(report.underlying_exposures, [])
        self.assertEqual(report.status, "NO_ACTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
