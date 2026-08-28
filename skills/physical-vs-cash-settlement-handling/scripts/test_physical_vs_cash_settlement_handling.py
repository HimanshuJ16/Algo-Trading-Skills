"""Unit tests for the expiry settlement handling engine.

Expected values are derived independently of the implementation: invoice
amounts are worked out by hand from published contract terms (CL is 1,000
barrels; a standard US equity option deliverable is 100 shares), the
long/short deadline split follows the delivery mechanics (the short delivers,
the long pays and is assigned from first notice day), and the futures
variation-margin figures are computed against a stated prior settlement price
rather than re-deriving the engine's own expression.
"""

import logging
import unittest

from physical_vs_cash_settlement_handling import (
    ContractSettlementSpec,
    PhysicalVsCashSettlementHandlingEngine,
    PortfolioSettlementReport,
    PositionState,
    SettlementPolicyConfig,
)

# The engine logs delivery alerts at ERROR; silence them so a discovery run of
# the full repo suite is not flooded with expected alarms.
logging.disable(logging.CRITICAL)


def _cl_spec(**overrides) -> ContractSettlementSpec:
    """NYMEX WTI Crude Oil: 1,000 barrels, physically delivered FOB Cushing."""
    kwargs = dict(
        symbol="CLZ5",
        underlying_asset="WTI_CRUDE",
        settlement_type="PHYSICAL",
        multiplier=1000.0,
        business_days_to_first_notice=10,
        business_days_to_last_trading_day=10,
        deliverable_description="1,000 bbl FOB Cushing, OK",
    )
    kwargs.update(overrides)
    return ContractSettlementSpec(**kwargs)


class TestCashSettlement(unittest.TestCase):
    """A cash-settled contract delivers nothing, but still moves money."""

    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()
        # E-mini S&P 500: $50 multiplier, cash settled to the SOQ.
        self.spec = ContractSettlementSpec(
            symbol="ESZ5",
            underlying_asset="SP500",
            settlement_type="CASH",
            multiplier=50.0,
        )

    def test_cash_settlement_reports_no_delivery_obligation(self) -> None:
        pos = PositionState(
            position_qty=10.0, entry_price=5000.0, account_cash_balance=100_000.0
        )
        report = self.engine.evaluate_settlement_risk(self.spec, pos, 5050.0)

        self.assertEqual(report.status, "CASH_SETTLED_NO_DELIVERY")
        self.assertEqual(report.delivery_obligation, "NONE")
        self.assertEqual(report.delivery_invoice_usd, 0.0)
        self.assertEqual(report.deliverable_units_required, 0.0)
        self.assertEqual(report.binding_deadline, "NONE")
        self.assertEqual(report.required_action, "NO_ACTION")

    def test_final_variation_differs_from_lifetime_pnl(self) -> None:
        """The money moving *at* expiry is one day's variation, not lifetime PnL.

        Long 10 at 5000, prior settle 5040, final settle 5050:
          lifetime      = 10 * 50 * (5050 - 5000) = $25,000
          final variation = 10 * 50 * (5050 - 5040) = $5,000
        """
        pos = PositionState(
            position_qty=10.0,
            entry_price=5000.0,
            account_cash_balance=100_000.0,
            prior_settlement_price=5040.0,
        )
        report = self.engine.evaluate_settlement_risk(self.spec, pos, 5050.0)

        self.assertEqual(report.lifetime_pnl, 25_000.0)
        self.assertEqual(report.final_variation_cashflow, 5_000.0)
        self.assertNotIn("NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY", report.warnings)

    def test_missing_prior_settlement_is_flagged_not_silently_assumed(self) -> None:
        pos = PositionState(
            position_qty=10.0, entry_price=5000.0, account_cash_balance=100_000.0
        )
        report = self.engine.evaluate_settlement_risk(self.spec, pos, 5050.0)

        self.assertEqual(report.final_variation_cashflow, report.lifetime_pnl)
        self.assertIn("NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY", report.warnings)

    def test_adverse_cash_settlement_beyond_balance_is_a_funding_shortfall(self) -> None:
        """Short 10 ES from a 4900 prior settle into a 5050 print:
        10 * 50 * (5050 - 4900) = $75,000 debit against a $50,000 balance."""
        pos = PositionState(
            position_qty=-10.0,
            entry_price=4900.0,
            account_cash_balance=50_000.0,
            prior_settlement_price=4900.0,
        )
        report = self.engine.evaluate_settlement_risk(self.spec, pos, 5050.0)

        self.assertEqual(report.final_variation_cashflow, -75_000.0)
        self.assertEqual(report.status, "CASH_SETTLEMENT_FUNDING_SHORTFALL")
        self.assertEqual(report.required_action, "FUND_ACCOUNT_BEFORE_SETTLEMENT")
        # A cash-settled contract has no delivery invoice, so the flag raised
        # must not be the one named for one.
        self.assertIn("INSUFFICIENT_CASH_FOR_SETTLEMENT_DEBIT", report.warnings)
        self.assertNotIn("INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE", report.warnings)

    def test_provisional_price_is_flagged_until_marked_final(self) -> None:
        pos = PositionState(
            position_qty=1.0, entry_price=5000.0, account_cash_balance=100_000.0
        )
        provisional = self.engine.evaluate_settlement_risk(self.spec, pos, 5050.0)
        final = self.engine.evaluate_settlement_risk(
            self.spec, pos, 5050.0, settlement_price_is_final=True
        )

        self.assertIn("PROVISIONAL_SETTLEMENT_PRICE", provisional.warnings)
        self.assertNotIn("PROVISIONAL_SETTLEMENT_PRICE", final.warnings)


class TestPhysicalDeliveryDirection(unittest.TestCase):
    """The obligation, the deadline and the resource tested all flip with the side."""

    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()

    def test_long_owes_cash_and_is_bound_by_first_notice_date(self) -> None:
        """5 CL at $70: 5 * 1,000 bbl * $70 = $350,000 invoice."""
        pos = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=500_000.0,
            has_delivery_facility=True,
        )
        report = self.engine.evaluate_settlement_risk(_cl_spec(), pos, 70.0)

        self.assertEqual(report.delivery_obligation, "TAKE_DELIVERY_AND_PAY")
        self.assertEqual(report.delivery_invoice_usd, 350_000.0)
        self.assertEqual(report.deliverable_units_required, 0.0)
        self.assertEqual(report.binding_deadline, "FIRST_NOTICE_DATE")
        self.assertEqual(report.delivery_price_basis, "FINAL_SETTLEMENT_PRICE")
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_PROVISIONED")

    def test_short_owes_barrels_and_is_bound_by_last_trading_day(self) -> None:
        """A short does not fund an invoice; it must produce 5,000 barrels."""
        pos = PositionState(
            position_qty=-5.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=True,
            deliverable_units_available=5_000.0,
        )
        report = self.engine.evaluate_settlement_risk(_cl_spec(), pos, 70.0)

        self.assertEqual(report.delivery_obligation, "MAKE_DELIVERY_OF_UNDERLYING")
        self.assertEqual(report.delivery_invoice_usd, 0.0)
        self.assertEqual(report.deliverable_units_required, 5_000.0)
        self.assertEqual(report.binding_deadline, "LAST_TRADING_DAY")
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_PROVISIONED")

    def test_zero_cash_short_with_the_deliverable_is_not_a_breach(self) -> None:
        """Regression: the old symmetric `cash >= notional` test failed a fully
        provisioned short purely for holding no cash."""
        pos = PositionState(
            position_qty=-5.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=True,
            deliverable_units_available=5_000.0,
        )
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_last_trading_day=1), pos, 70.0
        )

        self.assertNotEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertNotIn("INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE", report.warnings)

    def test_cash_rich_short_without_the_deliverable_is_a_breach(self) -> None:
        """The converse: money does not discharge a delivery obligation."""
        pos = PositionState(
            position_qty=-5.0,
            entry_price=70.0,
            account_cash_balance=10_000_000.0,
            has_delivery_facility=True,
            deliverable_units_available=0.0,
        )
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_last_trading_day=1), pos, 70.0
        )

        self.assertEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertIn("INSUFFICIENT_DELIVERABLE_UNITS", report.warnings)
        self.assertEqual(report.required_action, "CLOSE_OR_ROLL_BEFORE_DEADLINE")

    def test_each_side_reads_its_own_clock(self) -> None:
        """COMEX Gold shape: first notice day already inside the buffer while
        last trading day is still weeks away. The long is at the deadline; the
        short, in the same contract, is not."""
        spec = _cl_spec(
            symbol="GCZ5",
            underlying_asset="GOLD",
            multiplier=100.0,
            business_days_to_first_notice=1,
            business_days_to_last_trading_day=20,
        )
        unprovisioned = dict(
            entry_price=2000.0,
            account_cash_balance=0.0,
            has_delivery_facility=False,
            deliverable_units_available=0.0,
        )

        long_report = self.engine.evaluate_settlement_risk(
            spec, PositionState(position_qty=1.0, **unprovisioned), 2000.0
        )
        short_report = self.engine.evaluate_settlement_risk(
            spec, PositionState(position_qty=-1.0, **unprovisioned), 2000.0
        )

        self.assertEqual(long_report.business_days_to_deadline, 1)
        self.assertEqual(long_report.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertEqual(short_report.business_days_to_deadline, 20)
        self.assertEqual(short_report.status, "PHYSICAL_DELIVERY_NOT_PROVISIONED")


class TestPhysicalDeadlineBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()
        self.unprovisioned = dict(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=False,
        )

    def test_exactly_at_the_buffer_is_inside_it(self) -> None:
        """Default long buffer is 2 business days: day 2 breaches, day 3 does not."""
        at_buffer = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=2),
            PositionState(**self.unprovisioned),
            70.0,
        )
        outside_buffer = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=3),
            PositionState(**self.unprovisioned),
            70.0,
        )

        self.assertEqual(at_buffer.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertEqual(outside_buffer.status, "PHYSICAL_DELIVERY_NOT_PROVISIONED")
        self.assertEqual(
            outside_buffer.required_action, "CLOSE_OR_ROLL_BEFORE_DEADLINE"
        )

    def test_past_the_deadline_escalates_rather_than_recommending_a_roll(self) -> None:
        """Once first notice day has passed a roll no longer avoids assignment."""
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=-1),
            PositionState(**self.unprovisioned),
            70.0,
        )

        self.assertTrue(report.is_past_deadline)
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_DEADLINE_PASSED")
        self.assertEqual(report.required_action, "ESCALATE_TO_DELIVERY_OPERATIONS")

    def test_buffer_is_configurable_not_a_hard_coded_three_days(self) -> None:
        engine = PhysicalVsCashSettlementHandlingEngine(
            SettlementPolicyConfig(long_close_out_buffer_days=5)
        )
        report = engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=4),
            PositionState(**self.unprovisioned),
            70.0,
        )

        self.assertEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")

    def test_missing_binding_clock_raises_rather_than_reporting_compliant(self) -> None:
        long_pos = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=10_000_000.0,
            has_delivery_facility=True,
        )
        with self.assertRaises(ValueError):
            self.engine.evaluate_settlement_risk(
                _cl_spec(business_days_to_first_notice=None), long_pos, 70.0
            )

        short_pos = PositionState(
            position_qty=-5.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=True,
            deliverable_units_available=5_000.0,
        )
        with self.assertRaises(ValueError):
            self.engine.evaluate_settlement_risk(
                _cl_spec(business_days_to_last_trading_day=None), short_pos, 70.0
            )

    def test_a_missing_non_binding_clock_is_not_required(self) -> None:
        """A long needs only the first notice date; a short only the last trade date."""
        long_pos = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=10_000_000.0,
            has_delivery_facility=True,
        )
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_last_trading_day=None), long_pos, 70.0
        )
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_PROVISIONED")


class TestPhysicallySettledOptions(unittest.TestCase):
    """An exercised equity option is funded at the strike, not at spot."""

    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()

    def test_invoice_is_priced_at_the_strike(self) -> None:
        """Long 10 x $100 calls, 100 shares each, settling at $130:
          funded at the strike: 10 * 100 * $100 = $100,000
          spot would have said: 10 * 100 * $130 = $130,000
        The account holds $110,000, so the two prices give opposite verdicts."""
        spec = ContractSettlementSpec(
            symbol="XYZ 251219C00100000",
            underlying_asset="XYZ",
            settlement_type="PHYSICAL",
            multiplier=100.0,
            instrument_kind="OPTION",
            strike_price=100.0,
            business_days_to_first_notice=1,
        )
        pos = PositionState(
            position_qty=10.0,
            entry_price=2.50,
            account_cash_balance=110_000.0,
            has_delivery_facility=True,
        )
        report = self.engine.evaluate_settlement_risk(spec, pos, 130.0)

        self.assertEqual(report.delivery_price_basis, "STRIKE_PRICE")
        self.assertEqual(report.delivery_invoice_usd, 100_000.0)
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_PROVISIONED")

    def test_underfunded_exercise_breaches(self) -> None:
        spec = ContractSettlementSpec(
            symbol="XYZ 251219C00100000",
            underlying_asset="XYZ",
            settlement_type="PHYSICAL",
            multiplier=100.0,
            instrument_kind="OPTION",
            strike_price=100.0,
            business_days_to_first_notice=0,
        )
        pos = PositionState(
            position_qty=10.0,
            entry_price=2.50,
            account_cash_balance=25_000.0,
            has_delivery_facility=True,
        )
        report = self.engine.evaluate_settlement_risk(spec, pos, 130.0)

        self.assertEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertIn("INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE", report.warnings)

    def test_physical_option_without_a_strike_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ContractSettlementSpec(
                symbol="XYZ 251219C00100000",
                underlying_asset="XYZ",
                settlement_type="PHYSICAL",
                multiplier=100.0,
                instrument_kind="OPTION",
            )


class TestFlatAndValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()

    def test_flat_position_reports_no_obligation(self) -> None:
        """Regression: a flat row in an expiry sweep used to raise a delivery
        breach purely because the account had no warehouse."""
        pos = PositionState(
            position_qty=0.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=False,
        )
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=1), pos, 70.0
        )

        self.assertEqual(report.status, "FLAT_NO_OBLIGATION")
        self.assertEqual(report.delivery_obligation, "NONE")
        self.assertEqual(report.delivery_invoice_usd, 0.0)
        self.assertEqual(report.required_action, "NO_ACTION")

    def test_unrecognised_settlement_type_raises(self) -> None:
        """Regression: any string that was not 'CASH' used to be treated as a
        physical delivery obligation."""
        for bad in ("CSH", "cash-settled", "", "FINANCIAL"):
            with self.subTest(settlement_type=bad):
                with self.assertRaises(ValueError):
                    ContractSettlementSpec(
                        symbol="X",
                        underlying_asset="X",
                        settlement_type=bad,
                    )

    def test_settlement_type_is_case_and_whitespace_tolerant(self) -> None:
        spec = ContractSettlementSpec(
            symbol="ESZ5", underlying_asset="SP500", settlement_type=" cash "
        )
        self.assertEqual(spec.settlement_type, "CASH")

    def test_non_finite_and_non_positive_inputs_raise(self) -> None:
        spec = ContractSettlementSpec(
            symbol="ESZ5",
            underlying_asset="SP500",
            settlement_type="CASH",
            multiplier=50.0,
        )
        pos = PositionState(
            position_qty=1.0, entry_price=5000.0, account_cash_balance=1.0
        )
        for bad_price in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(settlement_price=bad_price):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_settlement_risk(spec, pos, bad_price)

        with self.assertRaises(ValueError):
            ContractSettlementSpec(
                symbol="X",
                underlying_asset="X",
                settlement_type="CASH",
                multiplier=-50.0,
            )
        with self.assertRaises(ValueError):
            PositionState(
                position_qty=float("nan"),
                entry_price=1.0,
                account_cash_balance=0.0,
            )
        with self.assertRaises(ValueError):
            PositionState(
                position_qty=1.0,
                entry_price=1.0,
                account_cash_balance=0.0,
                deliverable_units_available=-1.0,
            )

    def test_negative_settlement_prices_are_accepted(self) -> None:
        """NYMEX WTI settled at -$37.63 on 20 April 2020, driven by longs unable
        to take delivery at Cushing. A settlement engine that rejects negative
        prices refuses the exact event it exists to screen for.

        Long 1 CL entered at $20.00, settling at -$37.63:
          lifetime  = 1 * 1,000 * (-37.63 - 20.00) = -$57,630
          invoice   = 1 * 1,000 * -37.63           = -$37,630 (the long is paid)
        The cash test passes trivially, but the missing warehouse still binds.
        """
        pos = PositionState(
            position_qty=1.0,
            entry_price=20.0,
            account_cash_balance=0.0,
            has_delivery_facility=False,
        )
        report = self.engine.evaluate_settlement_risk(
            _cl_spec(business_days_to_first_notice=1), pos, -37.63
        )

        self.assertAlmostEqual(report.lifetime_pnl, -57_630.0, places=2)
        self.assertAlmostEqual(report.delivery_invoice_usd, -37_630.0, places=2)
        self.assertNotIn("INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE", report.warnings)
        self.assertIn("NO_DELIVERY_FACILITY", report.warnings)
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")

    def test_fractional_business_day_counts_raise(self) -> None:
        with self.assertRaises(ValueError):
            _cl_spec(business_days_to_first_notice=1.5)
        # A whole-valued float is accepted and normalised.
        self.assertEqual(_cl_spec(business_days_to_first_notice=2.0)
                         .business_days_to_first_notice, 2)

    def test_negative_close_out_buffer_raises(self) -> None:
        with self.assertRaises(ValueError):
            SettlementPolicyConfig(long_close_out_buffer_days=-1)


class TestPortfolioAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PhysicalVsCashSettlementHandlingEngine()

    def test_individually_funded_deliveries_can_collectively_overdraw(self) -> None:
        """Three long CL deliveries of $350,000 each pass on their own against a
        $400,000 balance; together they need $1,050,000."""
        pos = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=400_000.0,
            has_delivery_facility=True,
        )
        items = [
            (_cl_spec(symbol=f"CL{i}"), pos, 70.0) for i in range(3)
        ]
        report = self.engine.audit_portfolio_settlement(
            items, account_cash_balance=400_000.0
        )

        self.assertIsInstance(report, PortfolioSettlementReport)
        for position in report.positions:
            self.assertEqual(position.status, "PHYSICAL_DELIVERY_PROVISIONED")
        self.assertEqual(report.aggregate_delivery_invoice_usd, 1_050_000.0)
        self.assertIn("AGGREGATE_DELIVERY_INVOICE_EXCEEDS_CASH", report.warnings)

    def test_breaching_symbols_are_collected(self) -> None:
        broke = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=0.0,
            has_delivery_facility=False,
        )
        funded = PositionState(
            position_qty=5.0,
            entry_price=70.0,
            account_cash_balance=10_000_000.0,
            has_delivery_facility=True,
        )
        items = [
            (_cl_spec(symbol="CL_BAD", business_days_to_first_notice=1), broke, 70.0),
            (_cl_spec(symbol="CL_OK", business_days_to_first_notice=1), funded, 70.0),
        ]
        report = self.engine.audit_portfolio_settlement(
            items, account_cash_balance=10_000_000.0
        )

        self.assertEqual(report.breaching_symbols, ("CL_BAD",))

    def test_debit_balance_with_no_deliveries_is_not_a_delivery_shortfall(self) -> None:
        """A negative cash balance on an all-cash-settled book must not raise a
        flag about delivery invoices that do not exist."""
        spec = ContractSettlementSpec(
            symbol="ESZ5",
            underlying_asset="SP500",
            settlement_type="CASH",
            multiplier=50.0,
        )
        pos = PositionState(
            position_qty=1.0,
            entry_price=5000.0,
            account_cash_balance=-1_000.0,
            prior_settlement_price=5000.0,
        )
        report = self.engine.audit_portfolio_settlement(
            [(spec, pos, 5050.0)], account_cash_balance=-1_000.0
        )

        self.assertEqual(report.aggregate_delivery_invoice_usd, 0.0)
        self.assertNotIn("AGGREGATE_DELIVERY_INVOICE_EXCEEDS_CASH", report.warnings)


if __name__ == "__main__":
    unittest.main()
