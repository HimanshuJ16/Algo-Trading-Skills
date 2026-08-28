"""
Unit tests for prime-brokerage-multi-venue-consolidation.

Expected values are derived by hand in each test's comments rather than by re-running
the implementation's own expression, so a sign flip, a dropped multiplier, or a
quantity-ratio masquerading as a notional ratio fails the assertion instead of being
reproduced by it.
"""
import unittest
from datetime import datetime, timedelta, timezone

from prime_brokerage_multi_venue_consolidation import (
    STATUS_LATE_GIVEUP,
    STATUS_NO_EXECUTIONS,
    STATUS_SUCCESSFUL,
    DuplicateExecutionError,
    MixedInstrumentError,
    PBConsolidationReport,
    PrimeBrokerSpec,
    PrimeBrokerageMultiVenueConsolidationEngine,
    VenueExecution,
)

TRADE_DATE = "2026-07-31"
# 2026-07-31 21:00 New York (EDT, UTC-4) -- the DTC affirmation cut-off on trade date
# under the T+1 regime. Expressed as a fixed offset so the test needs no tz database.
EDT = timezone(timedelta(hours=-4))
CUTOFF = datetime(2026, 7, 31, 21, 0, tzinfo=EDT)


def make_spec(**kwargs) -> PrimeBrokerSpec:
    kwargs.setdefault("prime_broker_name", "GOLDMAN_SACHS")
    kwargs.setdefault("pb_account_id", "PB_ACCT_99182")
    kwargs.setdefault("clearing_fee_per_unit", 0.0005)
    return PrimeBrokerSpec(**kwargs)


def make_engine(**kwargs) -> PrimeBrokerageMultiVenueConsolidationEngine:
    return PrimeBrokerageMultiVenueConsolidationEngine(make_spec(), **kwargs)


def aapl_batch():
    """BUY 1,000 @ 150 on NASDAQ via Broker A; SELL 400 @ 151 on BATS via Broker B."""
    return [
        VenueExecution(
            "EXEC_01", "BROKER_A", "NASDAQ", "AAPL", "BUY", 1000.0, 150.0, TRADE_DATE
        ),
        VenueExecution(
            "EXEC_02", "BROKER_B", "BATS", "AAPL", "SELL", 400.0, 151.0, TRADE_DATE
        ),
    ]


class TestNettingAndNotional(unittest.TestCase):

    def test_cross_venue_netting_and_gross_notional(self):
        report = make_engine().consolidate_venue_executions(aapl_batch())

        self.assertEqual(report.status, STATUS_SUCCESSFUL)
        self.assertEqual(report.total_executions_consolidated, 2)
        self.assertEqual(report.trade_dates, [TRADE_DATE])

        aapl = report.netted_positions["AAPL.USD"]
        # Net 1000 - 400 = 600 long; gross 1000 + 400 = 1400 shares changed hands.
        self.assertEqual(aapl.net_quantity, 600.0)
        self.assertEqual(aapl.gross_quantity, 1400.0)
        self.assertEqual(aapl.buy_quantity, 1000.0)
        self.assertEqual(aapl.sell_quantity, 400.0)
        self.assertTrue(aapl.is_internally_offset)
        self.assertEqual(aapl.execution_count, 2)

        # 1000*150 + 400*151 = 150,000 + 60,400 = 210,400.
        self.assertEqual(aapl.gross_notional, 210400.0)
        self.assertEqual(report.gross_notional_by_currency, {"USD": 210400.0})
        # VWAP = 210,400 / 1,400 = 150.285714...
        self.assertAlmostEqual(aapl.vwap, 150.2857142857143, places=9)
        # Residual = 600 * 150.285714... = 90,171.428571...
        self.assertAlmostEqual(aapl.residual_notional_at_vwap, 90171.42857142857, places=6)
        self.assertEqual(report.residual_notional_by_currency, {"USD": 90171.43})
        # (1 - 600/1400) * 100 = 57.142857...
        self.assertAlmostEqual(aapl.offset_ratio_pct, 57.1429, places=4)

        self.assertEqual(aapl.broker_breakdown, {"BROKER_A": 1000.0, "BROKER_B": -400.0})
        self.assertEqual(aapl.venue_breakdown, {"NASDAQ": 1000.0, "BATS": -400.0})
        self.assertEqual(report.net_quantity("aapl"), 600.0)
        self.assertEqual(report.net_quantity("MSFT"), 0.0)

    def test_offset_pct_is_notional_weighted_not_quantity_weighted(self):
        """
        Regression: the offset figure must be weighted by traded notional.

        A quantity-weighted ratio reports (1 - 1/2001)*100 = 99.95% "savings" on this
        book, while the only residual position is a 700,000 USD BRK.A long that offsets
        against nothing. Notional-weighted: residual 700,000 of 700,200 gross traded,
        i.e. 200/700,200 = 0.0286% offset.
        """
        executions = [
            VenueExecution("P1", "BROKER_A", "ARCA", "PENNY", "BUY", 1000.0, 0.10, TRADE_DATE),
            VenueExecution("P2", "BROKER_B", "EDGX", "PENNY", "SELL", 1000.0, 0.10, TRADE_DATE),
            VenueExecution("B1", "BROKER_C", "NYSE", "BRK.A", "BUY", 1.0, 700000.0, TRADE_DATE),
        ]

        report = make_engine().consolidate_venue_executions(executions)

        self.assertEqual(report.gross_notional_by_currency, {"USD": 700200.0})
        self.assertEqual(report.residual_notional_by_currency, {"USD": 700000.0})
        self.assertAlmostEqual(
            report.notional_offset_pct_by_currency["USD"], 0.0286, places=4
        )
        self.assertLess(report.notional_offset_pct_by_currency["USD"], 1.0)
        # The fully offset penny name still reports 100% offset at instrument level.
        self.assertEqual(report.netted_positions["PENNY.USD"].net_quantity, 0.0)
        self.assertAlmostEqual(
            report.netted_positions["PENNY.USD"].offset_ratio_pct, 100.0, places=6
        )
        self.assertFalse(report.netted_positions["BRK.A.USD"].is_internally_offset)

    def test_no_margin_savings_field_is_published(self):
        """Margin relief needs a margin model; this report must not imply one."""
        report = make_engine().consolidate_venue_executions(aapl_batch())
        fields = set(vars(report))
        self.assertNotIn("consolidated_margin_savings_pct", fields)
        self.assertFalse([f for f in fields if "margin" in f])

    def test_contract_multiplier_applied_to_notional(self):
        # 20 option contracts at a 7.50 premium, 100 shares per contract = 15,000,
        # not 150.
        executions = [
            VenueExecution(
                "O1", "BROKER_A", "CBOE", "AAPL_260918C200", "BUY", 20.0, 7.50,
                TRADE_DATE, contract_multiplier=100.0,
            )
        ]
        report = make_engine().consolidate_venue_executions(executions)
        position = report.netted_positions["AAPL_260918C200.USD"]

        self.assertEqual(position.gross_notional, 15000.0)
        # VWAP is per underlying unit (the premium), not per contract.
        self.assertAlmostEqual(position.vwap, 7.50, places=9)
        self.assertAlmostEqual(position.residual_notional_at_vwap, 15000.0, places=6)
        self.assertEqual(report.giveup_payload[0]["notional"], 15000.0)

    def test_currencies_are_never_summed(self):
        executions = [
            VenueExecution("U1", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, 150.0, TRADE_DATE),
            VenueExecution(
                "E1", "BROKER_D", "XETRA", "SAP", "BUY", 100.0, 200.0, TRADE_DATE,
                currency="EUR",
            ),
        ]
        report = make_engine().consolidate_venue_executions(executions)

        self.assertEqual(
            report.gross_notional_by_currency, {"USD": 15000.0, "EUR": 20000.0}
        )
        self.assertEqual(sorted(report.notional_offset_pct_by_currency), ["EUR", "USD"])
        # Nothing offsets in either currency, so both are 0%.
        self.assertAlmostEqual(report.notional_offset_pct_by_currency["EUR"], 0.0, places=6)


class TestFees(unittest.TestCase):

    def test_clearing_fee_and_executing_broker_commission_are_both_captured(self):
        executions = [
            VenueExecution(
                "F1", "BROKER_A", "NASDAQ", "AAPL", "BUY", 1000.0, 150.0, TRADE_DATE,
                executing_broker_commission=3.50,
            ),
            VenueExecution(
                "F2", "BROKER_B", "BATS", "AAPL", "SELL", 400.0, 151.0, TRADE_DATE,
                executing_broker_commission=1.25,
            ),
        ]
        report = make_engine().consolidate_venue_executions(executions)

        # PB clearing fee: (1000 + 400) shares * 0.0005 = 0.70 USD.
        self.assertAlmostEqual(report.total_clearing_fees, 0.70, places=6)
        self.assertEqual(report.clearing_fee_currency, "USD")
        # Third-party execution commissions: 3.50 + 1.25 = 4.75, tracked separately.
        self.assertEqual(
            report.executing_broker_commissions_by_currency, {"USD": 4.75}
        )
        self.assertAlmostEqual(report.giveup_payload[0]["pb_clearing_fee"], 0.50, places=6)
        self.assertAlmostEqual(
            report.giveup_payload[0]["executing_broker_commission"], 3.50, places=6
        )

    def test_fee_currency_is_independent_of_trade_currency(self):
        spec = make_spec(clearing_fee_per_unit=0.01, fee_currency="usd")
        engine = PrimeBrokerageMultiVenueConsolidationEngine(spec)
        executions = [
            VenueExecution(
                "X1", "BROKER_D", "XETRA", "SAP", "BUY", 500.0, 200.0, TRADE_DATE,
                currency="EUR", executing_broker_commission=8.0,
            )
        ]
        report = engine.consolidate_venue_executions(executions)

        # 500 * 0.01 = 5.00 USD of PB clearing fee on a EUR-denominated fill.
        self.assertAlmostEqual(report.total_clearing_fees, 5.0, places=6)
        self.assertEqual(report.clearing_fee_currency, "USD")
        self.assertEqual(report.executing_broker_commissions_by_currency, {"EUR": 8.0})


class TestGiveUpPayload(unittest.TestCase):

    def test_payload_carries_one_instruction_per_execution(self):
        report = make_engine().consolidate_venue_executions(aapl_batch())

        self.assertEqual(len(report.giveup_payload), 2)
        first = report.giveup_payload[0]
        self.assertEqual(first["give_up_id"], "GU_EXEC_01")
        self.assertEqual(first["execution_id"], "EXEC_01")
        self.assertEqual(first["pb_account"], "PB_ACCT_99182")
        self.assertEqual(first["prime_broker"], "GOLDMAN_SACHS")
        self.assertEqual(first["executing_broker"], "BROKER_A")
        self.assertEqual(first["venue"], "NASDAQ")
        self.assertEqual(first["side"], "BUY")
        self.assertEqual(first["notional"], 150000.0)
        self.assertEqual(first["trade_date"], TRADE_DATE)
        self.assertFalse(first["submitted_after_cutoff"])

    def test_empty_batch_reports_no_executions(self):
        report = make_engine().consolidate_venue_executions([])

        self.assertIsInstance(report, PBConsolidationReport)
        self.assertEqual(report.status, STATUS_NO_EXECUTIONS)
        self.assertEqual(report.total_executions_consolidated, 0)
        self.assertEqual(report.netted_positions, {})
        self.assertEqual(report.giveup_payload, [])
        self.assertEqual(report.gross_notional_by_currency, {})
        self.assertEqual(report.trade_dates, [])


class TestIdempotency(unittest.TestCase):

    def test_duplicate_execution_id_within_batch_raises(self):
        duplicated = aapl_batch()[:1] * 2
        with self.assertRaises(DuplicateExecutionError):
            make_engine().consolidate_venue_executions(duplicated)

    def test_replayed_batch_after_reconnect_raises(self):
        engine = make_engine()
        engine.consolidate_venue_executions(aapl_batch())

        with self.assertRaises(DuplicateExecutionError):
            engine.consolidate_venue_executions(aapl_batch())

        self.assertEqual(
            engine.submitted_execution_ids(), frozenset({"EXEC_01", "EXEC_02"})
        )

    def test_failed_batch_registers_nothing(self):
        engine = make_engine()
        bad = aapl_batch() + [
            VenueExecution(
                "EXEC_03", "BROKER_D", "XETRA", "AAPL", "BUY", 10.0, 130.0, TRADE_DATE,
                currency="EUR",
            )
        ]
        with self.assertRaises(MixedInstrumentError):
            engine.consolidate_venue_executions(bad)

        self.assertEqual(engine.submitted_execution_ids(), frozenset())
        # The corrected batch still goes through.
        self.assertEqual(
            engine.consolidate_venue_executions(aapl_batch()).status, STATUS_SUCCESSFUL
        )

    def test_reset_allows_deliberate_resubmission(self):
        engine = make_engine()
        engine.consolidate_venue_executions(aapl_batch())
        engine.reset_submitted_execution_ids()

        self.assertEqual(engine.submitted_execution_ids(), frozenset())
        self.assertEqual(
            engine.consolidate_venue_executions(aapl_batch()).status, STATUS_SUCCESSFUL
        )

    def test_cross_batch_check_can_be_disabled_but_within_batch_cannot(self):
        engine = make_engine(enforce_cross_batch_idempotency=False)
        engine.consolidate_venue_executions(aapl_batch())
        self.assertEqual(
            engine.consolidate_venue_executions(aapl_batch()).status, STATUS_SUCCESSFUL
        )
        self.assertEqual(engine.submitted_execution_ids(), frozenset())

        with self.assertRaises(DuplicateExecutionError):
            engine.consolidate_venue_executions(aapl_batch()[:1] * 2)


class TestGiveUpTimeliness(unittest.TestCase):

    def test_submission_before_cutoff_is_on_time(self):
        report = make_engine().consolidate_venue_executions(
            aapl_batch(),
            submitted_at=datetime(2026, 7, 31, 20, 59, tzinfo=EDT),
            giveup_cutoffs={TRADE_DATE: CUTOFF},
        )

        self.assertEqual(report.status, STATUS_SUCCESSFUL)
        self.assertEqual(report.late_giveup_execution_ids, [])
        self.assertFalse(any(p["submitted_after_cutoff"] for p in report.giveup_payload))

    def test_submission_exactly_at_cutoff_is_on_time(self):
        """The boundary is inclusive: only a strictly later submission is late."""
        report = make_engine().consolidate_venue_executions(
            aapl_batch(), submitted_at=CUTOFF, giveup_cutoffs={TRADE_DATE: CUTOFF}
        )
        self.assertEqual(report.status, STATUS_SUCCESSFUL)
        self.assertEqual(report.late_giveup_execution_ids, [])

    def test_submission_after_cutoff_is_flagged_on_every_instruction(self):
        report = make_engine().consolidate_venue_executions(
            aapl_batch(),
            submitted_at=datetime(2026, 7, 31, 21, 1, tzinfo=EDT),
            giveup_cutoffs={TRADE_DATE: CUTOFF},
        )

        self.assertEqual(report.status, STATUS_LATE_GIVEUP)
        self.assertEqual(report.late_giveup_execution_ids, ["EXEC_01", "EXEC_02"])
        self.assertTrue(all(p["submitted_after_cutoff"] for p in report.giveup_payload))

    def test_cutoff_comparison_respects_timezone_offsets(self):
        # 01:30 UTC on 2026-08-01 is 21:30 EDT on 2026-07-31 -- past the cut-off,
        # despite the later calendar date in UTC.
        report = make_engine().consolidate_venue_executions(
            aapl_batch(),
            submitted_at=datetime(2026, 8, 1, 1, 30, tzinfo=timezone.utc),
            giveup_cutoffs={TRADE_DATE: CUTOFF},
        )
        self.assertEqual(report.status, STATUS_LATE_GIVEUP)

    def test_uncovered_trade_date_raises_rather_than_passing_silently(self):
        executions = aapl_batch() + [
            VenueExecution(
                "EXEC_03", "BROKER_A", "NASDAQ", "MSFT", "BUY", 10.0, 400.0, "2026-08-03"
            )
        ]
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions(
                executions,
                submitted_at=datetime(2026, 7, 31, 20, 0, tzinfo=EDT),
                giveup_cutoffs={TRADE_DATE: CUTOFF},
            )

    def test_naive_timestamps_raise(self):
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions(
                aapl_batch(),
                submitted_at=datetime(2026, 7, 31, 20, 0),
                giveup_cutoffs={TRADE_DATE: CUTOFF},
            )
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions(
                aapl_batch(),
                submitted_at=datetime(2026, 7, 31, 20, 0, tzinfo=EDT),
                giveup_cutoffs={TRADE_DATE: datetime(2026, 7, 31, 21, 0)},
            )

    def test_half_configured_timeliness_check_raises(self):
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions(
                aapl_batch(), submitted_at=datetime(2026, 7, 31, 20, 0, tzinfo=EDT)
            )
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions(
                aapl_batch(), giveup_cutoffs={TRADE_DATE: CUTOFF}
            )


class TestInputValidation(unittest.TestCase):

    def test_unrecognised_side_raises_instead_of_defaulting_to_sell(self):
        with self.assertRaises(ValueError):
            VenueExecution(
                "T1", "BROKER_A", "NASDAQ", "AAPL", "BUYY", 100.0, 150.0, TRADE_DATE
            )

    def test_side_is_case_insensitive(self):
        execution = VenueExecution(
            "T2", "BROKER_A", "NASDAQ", "aapl", "buy", 100.0, 150.0, TRADE_DATE
        )
        self.assertEqual(execution.side, "BUY")
        self.assertEqual(execution.symbol, "AAPL")
        self.assertEqual(execution.signed_quantity, 100.0)

    def test_sell_carries_the_sign(self):
        execution = VenueExecution(
            "T3", "BROKER_A", "NASDAQ", "AAPL", "SELL", 100.0, 150.0, TRADE_DATE
        )
        self.assertEqual(execution.signed_quantity, -100.0)

    def test_non_positive_quantity_raises(self):
        for bad_quantity in (0.0, -500.0):
            with self.subTest(quantity=bad_quantity):
                with self.assertRaises(ValueError):
                    VenueExecution(
                        "T4", "BROKER_A", "NASDAQ", "AAPL", "BUY", bad_quantity,
                        150.0, TRADE_DATE,
                    )

    def test_non_finite_and_negative_numbers_raise(self):
        with self.assertRaises(ValueError):
            VenueExecution(
                "T5", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, float("nan"), TRADE_DATE
            )
        with self.assertRaises(ValueError):
            VenueExecution(
                "T6", "BROKER_A", "NASDAQ", "AAPL", "BUY", float("inf"), 150.0, TRADE_DATE
            )
        with self.assertRaises(ValueError):
            VenueExecution(
                "T7", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, -150.0, TRADE_DATE
            )
        with self.assertRaises(ValueError):
            VenueExecution(
                "T8", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, 150.0, TRADE_DATE,
                executing_broker_commission=-1.0,
            )

    def test_zero_price_is_allowed(self):
        execution = VenueExecution(
            "T9", "BROKER_A", "CBOE", "AAPL_260918C900", "SELL", 5.0, 0.0, TRADE_DATE,
            contract_multiplier=100.0,
        )
        self.assertEqual(execution.notional, 0.0)

    def test_malformed_trade_date_raises(self):
        for bad_date in ("not-a-date", "31-07-2026", "2026-13-01", ""):
            with self.subTest(trade_date=bad_date):
                with self.assertRaises(ValueError):
                    VenueExecution(
                        "TA", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, 150.0, bad_date
                    )

    def test_malformed_currency_and_identifiers_raise(self):
        with self.assertRaises(ValueError):
            VenueExecution(
                "TB", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, 150.0, TRADE_DATE,
                currency="US$",
            )
        with self.assertRaises(ValueError):
            VenueExecution(
                "  ", "BROKER_A", "NASDAQ", "AAPL", "BUY", 100.0, 150.0, TRADE_DATE
            )
        with self.assertRaises(ValueError):
            VenueExecution(
                "TC", "BROKER_A", "NASDAQ", "", "BUY", 100.0, 150.0, TRADE_DATE
            )

    def test_non_positive_multiplier_raises(self):
        with self.assertRaises(ValueError):
            VenueExecution(
                "TD", "BROKER_A", "CBOE", "AAPL", "BUY", 1.0, 7.5, TRADE_DATE,
                contract_multiplier=0.0,
            )

    def test_spec_validation(self):
        with self.assertRaises(ValueError):
            PrimeBrokerSpec("", "PB_ACCT_1")
        with self.assertRaises(ValueError):
            PrimeBrokerSpec("PB", "PB_ACCT_1", clearing_fee_per_unit=-0.01)
        with self.assertRaises(ValueError):
            PrimeBrokerSpec("PB", "PB_ACCT_1", fee_currency="DOLLAR")

    def test_engine_requires_a_prime_broker_spec(self):
        with self.assertRaises(ValueError):
            PrimeBrokerageMultiVenueConsolidationEngine(None)

    def test_non_execution_rows_raise(self):
        with self.assertRaises(ValueError):
            make_engine().consolidate_venue_executions([{"symbol": "AAPL"}])


class TestMixedInstruments(unittest.TestCase):

    def test_same_symbol_in_two_currencies_raises(self):
        executions = [
            VenueExecution("M1", "BROKER_A", "NASDAQ", "SAP", "BUY", 100.0, 220.0, TRADE_DATE),
            VenueExecution(
                "M2", "BROKER_D", "XETRA", "SAP", "SELL", 100.0, 200.0, TRADE_DATE,
                currency="EUR",
            ),
        ]
        with self.assertRaises(MixedInstrumentError):
            make_engine().consolidate_venue_executions(executions)

    def test_same_symbol_with_two_multipliers_raises(self):
        executions = [
            VenueExecution(
                "M3", "BROKER_A", "CBOE", "XYZ_C50", "BUY", 10.0, 3.0, TRADE_DATE,
                contract_multiplier=100.0,
            ),
            VenueExecution(
                "M4", "BROKER_B", "CBOE", "XYZ_C50", "SELL", 10.0, 3.0, TRADE_DATE,
                contract_multiplier=110.0,
            ),
        ]
        with self.assertRaises(MixedInstrumentError):
            make_engine().consolidate_venue_executions(executions)


if __name__ == "__main__":
    unittest.main()
