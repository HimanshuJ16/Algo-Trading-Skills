"""Unit tests for cross-validation-of-commission-schedules-over-time."""
import logging
import unittest
from datetime import date, datetime

from commission_schedule_modeler import (
    BUY,
    SELL,
    CommissionScheduleError,
    CommissionTier,
    HistoricalCommissionModeler,
    IBKR_FIXED_US_EQUITY_TIER,
    RegulatoryFeeTier,
)

# The modeler warns (correctly) whenever the built-in reference schedule is used
# or a schedule has a gap. Those warnings are asserted behaviour of the module,
# not test failures, so keep them out of the test runner's output.
logging.getLogger("commission_schedule_modeler").setLevel(logging.CRITICAL)


class TestDateEffectiveTierResolution(unittest.TestCase):
    """The core promise of the skill: the rate in force on the trade date."""

    def setUp(self):
        self.modeler = HistoricalCommissionModeler()

    def test_each_era_charges_its_own_flat_ticket(self):
        # Expected values are the broker's published flat ticket fees, not a
        # re-derivation of the implementation's arithmetic.
        for trade_date, expected, label in [
            ("2016-06-15", 8.95, "schwab-8.95"),
            ("2017-02-03", 6.95, "schwab-6.95"),
            ("2018-03-15", 4.95, "schwab-4.95"),
            ("2021-01-20", 0.00, "schwab-zero-commission"),
        ]:
            with self.subTest(trade_date=trade_date):
                res = self.modeler.calculate_trade_commission("t", trade_date, "AAPL", 100, 150.0)
                self.assertEqual(res.calculated_commission, expected)
                self.assertEqual(res.tier_label, label)

    def test_zero_commission_transition_boundary_is_exact(self):
        # Schwab's cutover was effective 2019-10-07: the day before still costs $4.95.
        self.assertEqual(
            self.modeler.calculate_trade_commission("a", "2019-10-06", "AAPL", 10, 50.0).calculated_commission,
            4.95,
        )
        self.assertEqual(
            self.modeler.calculate_trade_commission("b", "2019-10-07", "AAPL", 10, 50.0).calculated_commission,
            0.00,
        )

    def test_flat_ticket_is_independent_of_size(self):
        small = self.modeler.calculate_trade_commission("s", "2018-01-02", "AAPL", 1, 10.0)
        large = self.modeler.calculate_trade_commission("l", "2018-01-02", "AAPL", 100_000, 10.0)
        self.assertEqual(small.calculated_commission, large.calculated_commission)
        self.assertEqual(small.calculated_commission, 4.95)


class TestNoSilentZeroCommissionFallback(unittest.TestCase):
    """Regression tests. Each of these silently returned $0.00 before the fix --
    i.e. the modeler applied today's zero-commission rate to a trade it could not
    classify, which is precisely the bias this skill exists to prevent."""

    def setUp(self):
        self.modeler = HistoricalCommissionModeler()

    def test_date_before_schedule_coverage_raises(self):
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("old", "1998-06-01", "AAPL", 100, 50.0)

    def test_unparseable_timestamp_raises(self):
        for bad in ["05/10/2015", "not-a-date", "", None, 20150510]:
            with self.subTest(bad=bad), self.assertRaises(CommissionScheduleError):
                self.modeler.calculate_trade_commission("bad", bad, "AAPL", 100, 50.0)

    def test_timestamp_with_time_component_resolves_to_its_own_day(self):
        # A tier-final date carrying a time component previously sorted *after*
        # the tier end under string comparison and fell through to the $0 tier.
        res = self.modeler.calculate_trade_commission("t", "2019-10-06T15:59:59", "AAPL", 10, 50.0)
        self.assertEqual(res.calculated_commission, 4.95)
        self.assertEqual(res.timestamp, "2019-10-06")

    def test_accepts_date_and_datetime_objects(self):
        self.assertEqual(
            self.modeler.calculate_trade_commission("d", date(2018, 5, 1), "AAPL", 10, 50.0).calculated_commission,
            4.95,
        )
        self.assertEqual(
            self.modeler.calculate_trade_commission(
                "dt", datetime(2018, 5, 1, 9, 30), "AAPL", 10, 50.0
            ).calculated_commission,
            4.95,
        )

    def test_trade_in_schedule_gap_raises(self):
        gapped = HistoricalCommissionModeler([
            CommissionTier("2015-01-01", "2015-12-31", fixed_ticket_fee=5.0),
            CommissionTier("2017-01-01", "2017-12-31", fixed_ticket_fee=1.0),
        ])
        self.assertEqual(
            gapped.calculate_trade_commission("in", "2015-06-01", "X", 1, 1.0).calculated_commission, 5.0
        )
        with self.assertRaises(CommissionScheduleError):
            gapped.calculate_trade_commission("gap", "2016-06-01", "X", 1, 1.0)


class TestPerShareFloorAndCap(unittest.TestCase):
    """IBKR Fixed structure: $0.005/share, $1.00 minimum, maximum 1% of value."""

    def setUp(self):
        self.modeler = HistoricalCommissionModeler([IBKR_FIXED_US_EQUITY_TIER])

    def test_per_share_rate_applies_between_floor_and_cap(self):
        # 1,000 shares @ $50 -> $50,000 value. 1000 * 0.005 = $5.00.
        # Floor $1.00 does not bind; cap 1% of $50,000 = $500 does not bind.
        res = self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 1000, 50.0)
        self.assertEqual(res.calculated_commission, 5.00)

    def test_minimum_ticket_charge_binds_on_small_orders(self):
        # 10 shares @ $100 -> $1,000 value. 10 * 0.005 = $0.05, below the $1.00
        # minimum; cap 1% of $1,000 = $10.00 does not bind.
        res = self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 10, 100.0)
        self.assertEqual(res.calculated_commission, 1.00)

    def test_percent_of_value_cap_binds_on_low_priced_shares(self):
        # 10,000 shares @ $0.10 -> $1,000 value. Uncapped per-share fee would be
        # 10000 * 0.005 = $50.00, i.e. 5% of notional. The 1% cap gives $10.00.
        res = self.modeler.calculate_trade_commission("t", "2018-01-02", "PENNY", 10_000, 0.10)
        self.assertEqual(res.calculated_commission, 10.00)

    def test_cap_overrides_minimum_when_both_bind(self):
        # 5 shares @ $0.10 -> $0.50 value. Floor lifts to $1.00; the 1% cap
        # ($0.005) is applied after the floor and dominates it.
        res = self.modeler.calculate_trade_commission("t", "2018-01-02", "PENNY", 5, 0.10)
        self.assertEqual(res.calculated_commission, 0.01)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.modeler = HistoricalCommissionModeler()

    def test_signed_share_quantity_is_rejected(self):
        # Previously a -100 share sell silently reduced the per-share component.
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", -100, 50.0)

    def test_zero_shares_and_negative_price_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 0, 50.0)
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 10, -1.0)

    def test_nan_inputs_rejected(self):
        nan = float("nan")
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", nan, 50.0)
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 10, nan)

    def test_invalid_side_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("t", "2018-01-02", "AAPL", 10, 50.0, side="SHORT")


class TestScheduleValidation(unittest.TestCase):
    def test_empty_schedule_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            HistoricalCommissionModeler([])

    def test_overlapping_tiers_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            HistoricalCommissionModeler([
                CommissionTier("2015-01-01", "2016-12-31", fixed_ticket_fee=5.0),
                CommissionTier("2016-01-01", "2017-12-31", fixed_ticket_fee=1.0),
            ])

    def test_inverted_tier_dates_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            HistoricalCommissionModeler([CommissionTier("2017-01-01", "2016-01-01")])

    def test_negative_fee_rejected(self):
        with self.assertRaises(CommissionScheduleError):
            HistoricalCommissionModeler([CommissionTier("2015-01-01", "2016-12-31", per_share_fee=-0.001)])

    def test_schedule_is_isolated_from_the_shared_reference_tiers(self):
        # Regression: tiers were retained by reference, so mutating one modeler's
        # schedule corrupted the module-level default and every other instance.
        import commission_schedule_modeler as mod

        a = HistoricalCommissionModeler()
        b = HistoricalCommissionModeler()
        a.schedule[0].fixed_ticket_fee = 999.0
        self.assertEqual(
            b.calculate_trade_commission("t", "2016-06-01", "X", 1, 1.0).calculated_commission, 8.95
        )
        self.assertEqual(mod.DEFAULT_SCHWAB_RETAIL_SCHEDULE[0].fixed_ticket_fee, 8.95)

    def test_unsorted_input_is_normalized_not_mispriced(self):
        modeler = HistoricalCommissionModeler([
            CommissionTier("2017-01-01", "2017-12-31", fixed_ticket_fee=1.0),
            CommissionTier("2015-01-01", "2016-12-31", fixed_ticket_fee=5.0),
        ])
        self.assertEqual(
            modeler.calculate_trade_commission("t", "2015-06-01", "X", 1, 1.0).calculated_commission, 5.0
        )


class TestRegulatoryFees(unittest.TestCase):
    """SEC Section 31 and FINRA TAF are assessed on sales only."""

    def setUp(self):
        # FINRA Schedule A: $0.000166/share, $8.30 per-trade maximum.
        # SEC Section 31 FY2025: $27.80 per $1,000,000 of covered sales.
        self.reg = [RegulatoryFeeTier(
            "2024-10-01", "2025-05-13", sec_fee_per_million=27.80,
            taf_per_share=0.000166, taf_max_per_trade=8.30,
        )]
        self.modeler = HistoricalCommissionModeler(regulatory_schedule=self.reg)

    def test_buy_incurs_no_regulatory_fee(self):
        res = self.modeler.calculate_trade_commission("b", "2025-01-15", "AAPL", 1000, 200.0, side=BUY)
        self.assertEqual(res.regulatory_fees, 0.0)

    def test_sell_incurs_section31_plus_taf(self):
        # 1,000 shares @ $200 -> $200,000 proceeds.
        # Section 31: 200000 * 27.80 / 1e6 = $5.56
        # TAF:        1000 * 0.000166      = $0.166  (below the $8.30 cap)
        # Total: 5.56 + 0.166 = 5.726 -> $5.73
        res = self.modeler.calculate_trade_commission("s", "2025-01-15", "AAPL", 1000, 200.0, side=SELL)
        self.assertEqual(res.regulatory_fees, 5.73)

    def test_zero_commission_era_sell_still_costs_money(self):
        # The headline correction: "zero commission" is not zero cost on a sale.
        res = self.modeler.calculate_trade_commission("s", "2025-01-15", "AAPL", 1000, 200.0, side=SELL)
        self.assertEqual(res.calculated_commission, 0.0)
        self.assertGreater(res.total_cost, 0.0)
        self.assertEqual(res.total_cost, 5.73)

    def test_taf_per_trade_cap_binds_on_very_large_share_counts(self):
        # 1,000,000 shares would be 1e6 * 0.000166 = $166.00 uncapped; capped at $8.30.
        # Section 31 on $1,000 of proceeds: 1000 * 27.80 / 1e6 = $0.0278 -> rounds into the cent.
        res = self.modeler.calculate_trade_commission("s", "2025-01-15", "SUB", 1_000_000, 0.001, side=SELL)
        self.assertEqual(res.regulatory_fees, 8.33)

    def test_unmodelled_regulatory_fees_are_flagged_not_reported_as_zero(self):
        plain = HistoricalCommissionModeler()
        res = plain.calculate_trade_commission("s", "2021-01-15", "AAPL", 100, 50.0, side=SELL)
        self.assertFalse(res.regulatory_fees_modeled)
        report = plain.audit_impact([
            {"id": "s", "date": "2021-01-15", "symbol": "AAPL", "shares": 100, "price": 50.0, "side": SELL}
        ])
        self.assertFalse(report.regulatory_fees_modeled)
        self.assertIn("NOT modelled", report.message)

    def test_sell_outside_regulatory_coverage_raises(self):
        with self.assertRaises(CommissionScheduleError):
            self.modeler.calculate_trade_commission("s", "2018-01-15", "AAPL", 10, 50.0, side=SELL)


class TestAuditImpact(unittest.TestCase):
    def setUp(self):
        self.modeler = HistoricalCommissionModeler()

    def test_audit_totals_against_hand_computed_fees(self):
        trades = [
            {"id": "t1", "date": "2016-01-11", "symbol": "AAPL", "shares": 100, "price": 100.0},  # $8.95
            {"id": "t2", "date": "2018-01-10", "symbol": "AAPL", "shares": 100, "price": 130.0},  # $4.95
            {"id": "t3", "date": "2021-01-11", "symbol": "AAPL", "shares": 100, "price": 130.0},  # $0.00
        ]
        report = self.modeler.audit_impact(trades, starting_capital=10_000.0)
        self.assertEqual(report.total_trades, 3)
        self.assertEqual(report.total_commission_historical, 13.90)
        self.assertEqual(report.total_commission_modern_flat, 0.0)
        self.assertEqual(report.pnl_impact_delta_usd, 13.90)
        # 13.90 / 10,000 = 0.139% -> 0.14%
        self.assertEqual(report.historical_fee_drag_pct, 0.14)
        self.assertEqual(report.total_volume, 36_000.0)

    def test_non_zero_modern_baseline_is_honoured(self):
        trades = [{"id": "t1", "date": "2016-01-11", "symbol": "AAPL", "shares": 100, "price": 100.0}]
        report = self.modeler.audit_impact(
            trades, starting_capital=10_000.0,
            modern_baseline=CommissionTier("2020-01-01", "9999-12-31", fixed_ticket_fee=1.0),
        )
        self.assertEqual(report.total_commission_modern_flat, 1.00)
        self.assertEqual(report.pnl_impact_delta_usd, 7.95)

    def test_zero_starting_capital_rejected(self):
        # Previously raised ZeroDivisionError from inside the drag calculation.
        with self.assertRaises(CommissionScheduleError):
            self.modeler.audit_impact([], starting_capital=0.0)

    def test_missing_trade_key_reports_the_offending_index(self):
        with self.assertRaises(CommissionScheduleError) as ctx:
            self.modeler.audit_impact([{"id": "t1", "date": "2018-01-10", "symbol": "AAPL", "shares": 100}])
        self.assertIn("index 0", str(ctx.exception))
        self.assertIn("price", str(ctx.exception))

    def test_empty_trade_list_produces_zero_drag(self):
        report = self.modeler.audit_impact([], starting_capital=10_000.0)
        self.assertEqual(report.total_trades, 0)
        self.assertEqual(report.historical_fee_drag_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
