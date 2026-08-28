"""
Unit tests for perpetual-futures-funding-rate-handling.

Expected values are derived independently of the implementation: notional and
payment by hand ($500,000 x 0.0001 = $50.00), and the annualized figures from a
separate Decimal computation of rate x (8760 / interval) x 100 and
((1 + rate) ** (8760 / interval) - 1) x 100 rather than by re-running the module's
own arithmetic.
"""
import unittest
from datetime import datetime, timedelta, timezone

from perpetual_futures_funding_rate_handling import (
    DEFAULT_MAX_PLAUSIBLE_FUNDING_RATE,
    FundingInputError,
    FundingPolicyConfig,
    FundingRateReport,
    FundingRateUpdate,
    PerpetualFuturesFundingRateHandlingEngine,
    PerpetualPosition,
    STATUS_BREACH,
    STATUS_INFLOW,
    STATUS_NEUTRAL,
    STATUS_OUTFLOW_OK,
    annualize_funding_rate,
    funding_timestamp_from_epoch_ms,
    parse_funding_timestamp,
    periods_per_year,
)

FUNDING_TS = "2026-07-31T16:00:00Z"


def long_btc(qty=10.0, mark=50000.0):
    return PerpetualPosition("BTCUSDT", qty, "LONG", 49000.0, mark)


def short_btc(qty=-10.0, mark=50000.0):
    return PerpetualPosition("BTCUSDT", qty, "SHORT", 49000.0, mark)


class TestAnnualizationMath(unittest.TestCase):
    """Independently derived: 8760 / interval periods, simple and compounded."""

    def test_periods_per_year_matches_hand_calculation(self):
        self.assertEqual(periods_per_year(8), 1095.0)
        self.assertEqual(periods_per_year(4), 2190.0)
        self.assertEqual(periods_per_year(1), 8760.0)

    def test_simple_annualization_is_rate_times_periods(self):
        # 0.01% per 8h -> 0.0001 * 1095 = 0.1095 = 10.95%
        self.assertAlmostEqual(annualize_funding_rate(0.0001, 8), 0.1095, places=12)
        self.assertAlmostEqual(annualize_funding_rate(0.0001, 4), 0.2190, places=12)
        self.assertAlmostEqual(annualize_funding_rate(0.0001, 1), 0.8760, places=12)

    def test_compounded_annualization_matches_independent_decimal_values(self):
        # Decimal-derived: ((1+r)**n - 1) * 100 for (r, interval_hours).
        cases = {
            (0.0001, 8): 11.571396,
            (0.0001, 4): 24.481765,
            (0.0001, 1): 140.117020,
            (0.001, 8): 198.754764,
            (-0.0002, 8): -19.669588,
        }
        for (rate, hours), expected_pct in cases.items():
            with self.subTest(rate=rate, hours=hours):
                got = annualize_funding_rate(rate, hours, compound=True) * 100.0
                self.assertAlmostEqual(got, expected_pct, places=5)

    def test_compounding_materially_exceeds_simple_at_high_rates(self):
        # The whole reason both numbers are reported: at 0.1%/8h they differ by ~89pp.
        simple = annualize_funding_rate(0.001, 8) * 100.0
        compounded = annualize_funding_rate(0.001, 8, compound=True) * 100.0
        self.assertAlmostEqual(simple, 109.5, places=6)
        self.assertGreater(compounded - simple, 80.0)

    def test_annualization_rejects_non_positive_interval(self):
        for bad in (0, -8):
            with self.subTest(interval=bad):
                with self.assertRaises(FundingInputError):
                    periods_per_year(bad)

    def test_compounding_total_loss_floor(self):
        self.assertEqual(annualize_funding_rate(-1.0, 8, compound=True), -1.0)
        self.assertEqual(annualize_funding_rate(-2.0, 8, compound=True), -1.0)


class TestFundingPayment(unittest.TestCase):

    def setUp(self):
        self.engine = PerpetualFuturesFundingRateHandlingEngine()

    def test_long_pays_when_rate_is_positive(self):
        # 10 BTC @ $50,000 = $500,000 notional; $500,000 * 0.0001 = $50.00 outflow.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_OUTFLOW_OK)
        self.assertEqual(report.position_notional_usd, 500000.00)
        self.assertEqual(report.funding_payment_usd, 50.00)
        self.assertAlmostEqual(report.annualized_funding_apr, 10.95, places=2)
        self.assertAlmostEqual(report.annualized_funding_apy, 11.57, places=2)
        self.assertEqual(report.periods_per_year, 1095.0)
        self.assertEqual(report.recommended_action, "HOLD")

    def test_short_receives_when_rate_is_positive(self):
        report = self.engine.process_funding_update(
            short_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_INFLOW)
        self.assertEqual(report.funding_payment_usd, -50.00)

    def test_long_receives_when_rate_is_negative(self):
        # SKILL.md verification case: -0.02% on $500,000 = $100.00 inflow.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", -0.0002, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_INFLOW)
        self.assertEqual(report.funding_payment_usd, -100.00)

    def test_short_pays_when_rate_is_negative(self):
        report = self.engine.process_funding_update(
            short_btc(), FundingRateUpdate("BTCUSDT", -0.0002, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_OUTFLOW_OK)
        self.assertEqual(report.funding_payment_usd, 100.00)

    def test_notional_uses_mark_price_not_entry_price(self):
        # Entry $49,000 vs mark $60,000: funding must be charged on the mark.
        report = self.engine.process_funding_update(
            PerpetualPosition("BTCUSDT", 10.0, "LONG", 49000.0, 60000.0),
            FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8),
        )
        self.assertEqual(report.position_notional_usd, 600000.00)
        self.assertEqual(report.funding_payment_usd, 60.00)

    def test_short_quantity_sign_does_not_change_the_payment(self):
        # -10 and +10 both mean "short 10" once side says SHORT.
        signed = self.engine.process_funding_update(
            short_btc(qty=-10.0), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        magnitude = self.engine.process_funding_update(
            short_btc(qty=10.0), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertEqual(signed.funding_payment_usd, magnitude.funding_payment_usd)

    def test_zero_rate_is_neutral_not_income(self):
        # Regression: a zero rate previously reported FUNDING_INFLOW_INCOME.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_NEUTRAL)
        self.assertEqual(report.funding_payment_usd, 0.0)
        self.assertFalse(report.is_adverse_drag_high)


class TestAnnualizedSignConvention(unittest.TestCase):
    """APR/APY are position-relative: positive means this position is paying."""

    def setUp(self):
        self.engine = PerpetualFuturesFundingRateHandlingEngine()

    def test_short_under_positive_rate_reports_negative_apr(self):
        # Regression: the APR used to carry the rate's sign, so a short earning
        # funding reported +10.95% alongside a negative (income) payment.
        report = self.engine.process_funding_update(
            short_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertLess(report.funding_payment_usd, 0.0)
        self.assertAlmostEqual(report.annualized_funding_apr, -10.95, places=2)
        self.assertLess(report.annualized_funding_apy, 0.0)

    def test_apr_sign_always_agrees_with_payment_sign(self):
        cases = [(long_btc(), 0.0001), (long_btc(), -0.0001),
                 (short_btc(), 0.0001), (short_btc(), -0.0001)]
        for pos, rate in cases:
            with self.subTest(side=pos.side, rate=rate):
                report = self.engine.process_funding_update(
                    pos, FundingRateUpdate("BTCUSDT", rate, FUNDING_TS, 8)
                )
                self.assertEqual(
                    report.funding_payment_usd > 0, report.annualized_funding_apr > 0
                )


class TestFundingInterval(unittest.TestCase):

    def setUp(self):
        self.engine = PerpetualFuturesFundingRateHandlingEngine()

    def test_four_hour_interval_doubles_the_apr(self):
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 4)
        )
        self.assertAlmostEqual(report.annualized_funding_apr, 21.90, places=2)
        self.assertEqual(report.periods_per_year, 2190.0)
        self.assertEqual(report.funding_interval_hours, 4)

    def test_hourly_interval_annualizes_at_eight_times_the_eight_hour_figure(self):
        # Binance switches a symbol to hourly settlement at the cap/floor.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 1)
        )
        self.assertAlmostEqual(report.annualized_funding_apr, 87.60, places=2)
        self.assertEqual(report.periods_per_year, 8760.0)

    def test_interval_does_not_change_the_per_interval_payment(self):
        eight = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        one = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 1)
        )
        self.assertEqual(eight.funding_payment_usd, one.funding_payment_usd)

    def test_non_positive_interval_is_rejected_not_coerced(self):
        # Regression: max(1, interval) silently turned 0 into a 1-hour interval,
        # producing an APR 8x the truth instead of an error.
        for bad in (0, -8):
            with self.subTest(interval=bad):
                with self.assertRaises(FundingInputError):
                    FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, bad)

    def test_non_integer_interval_is_rejected(self):
        with self.assertRaises(FundingInputError):
            FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8.0)


class TestAdverseDragAudit(unittest.TestCase):

    def test_breach_when_apr_exceeds_policy(self):
        # 0.10% per 8h = 109.5% APR, far above the 25% ceiling.
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_adverse_funding_apr=0.25)
        )
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0010, FUNDING_TS, 8)
        )
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertTrue(report.is_adverse_drag_high)
        self.assertEqual(report.recommended_action, "CLOSE_OR_HEDGE")

    def test_exact_threshold_is_not_a_breach(self):
        # 0.02% per 8h = exactly 21.9% APR against a 21.9% ceiling.
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_adverse_funding_apr=0.219)
        )
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0002, FUNDING_TS, 8)
        )
        self.assertAlmostEqual(report.annualized_funding_apr, 21.90, places=6)
        self.assertFalse(report.is_adverse_drag_high)
        self.assertEqual(report.status, STATUS_OUTFLOW_OK)

    def test_income_never_breaches_however_large(self):
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_adverse_funding_apr=0.01)
        )
        report = engine.process_funding_update(
            short_btc(), FundingRateUpdate("BTCUSDT", 0.0010, FUNDING_TS, 8)
        )
        self.assertFalse(report.is_adverse_drag_high)
        self.assertEqual(report.status, STATUS_INFLOW)

    def test_shorter_interval_can_turn_a_passing_rate_into_a_breach(self):
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_adverse_funding_apr=0.25)
        )
        ok = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0002, FUNDING_TS, 8)
        )
        breach = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0002, FUNDING_TS, 1)
        )
        self.assertFalse(ok.is_adverse_drag_high)
        self.assertTrue(breach.is_adverse_drag_high)

    def test_advisory_action_respects_auto_close_flag(self):
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_adverse_funding_apr=0.25, auto_close_high_drag=False)
        )
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0010, FUNDING_TS, 8)
        )
        self.assertTrue(report.is_adverse_drag_high)
        self.assertEqual(report.recommended_action, "REVIEW")

    def test_policy_rejects_negative_ceiling(self):
        with self.assertRaises(FundingInputError):
            FundingPolicyConfig(max_adverse_funding_apr=-0.1)


class TestSideValidation(unittest.TestCase):

    def test_unknown_side_is_rejected_rather_than_treated_as_short(self):
        # Regression: any side string that was not exactly 'LONG' fell through to
        # the short branch, silently flipping the sign of a real cash flow.
        for bad in ("LNG", "", "   ", "long position", "NEUTRAL"):
            with self.subTest(side=bad):
                with self.assertRaises(FundingInputError):
                    PerpetualPosition("BTCUSDT", 10.0, bad, 49000.0, 50000.0)

    def test_binance_one_way_both_is_rejected_with_guidance(self):
        with self.assertRaises(FundingInputError) as ctx:
            PerpetualPosition("BTCUSDT", 10.0, "BOTH", 49000.0, 50000.0)
        self.assertIn("positionAmt", str(ctx.exception))

    def test_buy_sell_aliases_and_whitespace_are_accepted(self):
        self.assertEqual(PerpetualPosition("BTCUSDT", 10.0, " buy ", 1.0, 1.0).side, "LONG")
        self.assertEqual(PerpetualPosition("BTCUSDT", 10.0, "sell", 1.0, 1.0).side, "SHORT")

    def test_long_with_negative_quantity_is_a_contradiction(self):
        with self.assertRaises(FundingInputError):
            PerpetualPosition("BTCUSDT", -10.0, "LONG", 49000.0, 50000.0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = PerpetualFuturesFundingRateHandlingEngine()

    def test_nan_rate_is_rejected_not_silently_reported_as_income(self):
        # Regression: NaN > 0 is False, so a NaN rate used to produce a
        # zero-cost-looking FUNDING_INFLOW_INCOME report.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(rate=bad):
                with self.assertRaises(FundingInputError):
                    FundingRateUpdate("BTCUSDT", bad, FUNDING_TS, 8)

    def test_non_finite_prices_and_quantities_are_rejected(self):
        with self.assertRaises(FundingInputError):
            PerpetualPosition("BTCUSDT", float("nan"), "LONG", 49000.0, 50000.0)
        with self.assertRaises(FundingInputError):
            PerpetualPosition("BTCUSDT", 10.0, "LONG", 49000.0, float("inf"))

    def test_non_positive_mark_price_is_rejected(self):
        for bad in (0.0, -50000.0):
            with self.subTest(mark=bad):
                with self.assertRaises(FundingInputError):
                    PerpetualPosition("BTCUSDT", 10.0, "LONG", 49000.0, bad)

    def test_flat_position_is_rejected(self):
        with self.assertRaises(FundingInputError):
            PerpetualPosition("BTCUSDT", 0.0, "LONG", 49000.0, 50000.0)

    def test_symbol_mismatch_is_rejected(self):
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(
                long_btc(), FundingRateUpdate("ETHUSDT", 0.0001, FUNDING_TS, 8)
            )

    def test_symbols_are_normalised_before_comparison(self):
        report = self.engine.process_funding_update(
            PerpetualPosition(" btcusdt ", 10.0, "LONG", 49000.0, 50000.0),
            FundingRateUpdate("btcusdt", 0.0001, FUNDING_TS, 8),
        )
        self.assertEqual(report.symbol, "BTCUSDT")

    def test_percent_number_passed_as_a_decimal_rate_is_rejected(self):
        # Binance's cap for a capped contract is 0.75 *percent*; handing 0.75 to a
        # decimal-rate field is a 100x fee error and must not compute silently.
        for bad in (0.75, 2.0, -0.3):
            with self.subTest(rate=bad):
                with self.assertRaises(FundingInputError) as ctx:
                    self.engine.process_funding_update(
                        long_btc(), FundingRateUpdate("BTCUSDT", bad, FUNDING_TS, 8)
                    )
                self.assertIn("plausibility", str(ctx.exception))

    def test_guard_cannot_catch_small_unit_confusions(self):
        # Documented limitation: 0.01 could be a mistyped 0.01% or a genuine 1%
        # print, and 1% is inside Binance's +/-2% cap. The guard deliberately lets
        # it through rather than rejecting legitimate extreme funding.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.01, FUNDING_TS, 8)
        )
        self.assertEqual(report.funding_payment_usd, 5000.00)

    def test_plausibility_guard_admits_every_published_venue_cap(self):
        # Binance's general +/-2% cap is the widest published; it must pass.
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.02, FUNDING_TS, 8)
        )
        self.assertEqual(report.funding_payment_usd, 10000.00)
        self.assertLess(0.02, DEFAULT_MAX_PLAUSIBLE_FUNDING_RATE)

    def test_plausibility_guard_can_be_raised_deliberately(self):
        engine = PerpetualFuturesFundingRateHandlingEngine(
            FundingPolicyConfig(max_plausible_funding_rate=0.5)
        )
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.1, FUNDING_TS, 8)
        )
        self.assertEqual(report.funding_payment_usd, 50000.00)

    def test_post_construction_mutation_is_revalidated_at_use(self):
        # Dataclasses are mutable and agents do mutate them. A side mutated to an
        # unrecognised token must not fall through to the short branch.
        pos = long_btc()
        pos.side = "BOTH"
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(
                pos, FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
            )

        update = FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        update.funding_rate = float("nan")
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(long_btc(), update)

        stale = long_btc()
        stale.mark_price = -1.0
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(
                stale, FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
            )

    def test_notional_overflow_is_rejected(self):
        pos = PerpetualPosition("BTCUSDT", 1e300, "LONG", 1e300, 1e300)
        with self.assertRaises(FundingInputError) as ctx:
            self.engine.process_funding_update(
                pos, FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
            )
        self.assertIn("overflowed", str(ctx.exception))

    def test_wrong_argument_types_are_rejected(self):
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(
                {"symbol": "BTCUSDT"}, FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
            )
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(long_btc(), {"funding_rate": 0.0001})


class TestFundingTimestamps(unittest.TestCase):

    def setUp(self):
        self.engine = PerpetualFuturesFundingRateHandlingEngine()

    def test_hours_to_next_funding_is_none_without_a_supplied_clock(self):
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertIsNone(report.hours_to_next_funding)

    def test_hours_to_next_funding_from_supplied_clock(self):
        now = datetime(2026, 7, 31, 13, 30, tzinfo=timezone.utc)
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8), now_utc=now
        )
        self.assertAlmostEqual(report.hours_to_next_funding, 2.5, places=6)

    def test_non_utc_clock_is_converted_not_misread(self):
        # 19:00 at +05:30 is 13:30 UTC -- the same 2.5 hours to funding.
        now = datetime(2026, 7, 31, 19, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8), now_utc=now
        )
        self.assertAlmostEqual(report.hours_to_next_funding, 2.5, places=6)

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(FundingInputError):
            self.engine.process_funding_update(
                long_btc(),
                FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8),
                now_utc=datetime(2026, 7, 31, 13, 30),
            )

    def test_stale_funding_timestamp_is_flagged_negative_not_hidden(self):
        now = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
        report = self.engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8), now_utc=now
        )
        self.assertAlmostEqual(report.hours_to_next_funding, -4.0, places=6)
        self.assertIn("STALE FUNDING TIMESTAMP", report.audit_notes)

    def test_unparseable_timestamp_is_rejected_at_construction(self):
        for bad in ("", "   ", "31/07/2026 16:00", "1785513600000"):
            with self.subTest(raw=bad):
                with self.assertRaises(FundingInputError):
                    FundingRateUpdate("BTCUSDT", 0.0001, bad, 8)

    def test_naive_timestamp_string_is_treated_as_utc(self):
        parsed = parse_funding_timestamp("2026-07-31T16:00:00")
        self.assertEqual(parsed, datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc))

    def test_epoch_ms_helper_round_trips_a_venue_funding_time(self):
        # 2026-07-31T16:00:00Z as epoch milliseconds.
        epoch_ms = int(datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(funding_timestamp_from_epoch_ms(epoch_ms), FUNDING_TS)
        self.assertEqual(parse_funding_timestamp(funding_timestamp_from_epoch_ms(epoch_ms)),
                         datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc))

    def test_epoch_ms_helper_rejects_non_int(self):
        for bad in ("1785513600000", 1785513600.0, True):
            with self.subTest(value=bad):
                with self.assertRaises(FundingInputError):
                    funding_timestamp_from_epoch_ms(bad)


class TestReportShape(unittest.TestCase):

    def test_report_is_a_dataclass_with_the_documented_fields(self):
        engine = PerpetualFuturesFundingRateHandlingEngine()
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        )
        self.assertIsInstance(report, FundingRateReport)
        self.assertEqual(report.symbol, "BTCUSDT")
        self.assertEqual(report.funding_rate_pct, 0.01)
        self.assertIn("PERPETUAL FUNDING AUDIT", report.audit_notes)

    def test_sub_basis_point_rate_survives_reporting_precision(self):
        # Binance publishes funding rates to 8 decimals; rounding the reported
        # percent to 4 dp would collapse the smallest prints to zero.
        engine = PerpetualFuturesFundingRateHandlingEngine()
        report = engine.process_funding_update(
            long_btc(), FundingRateUpdate("BTCUSDT", 0.00000001, FUNDING_TS, 8)
        )
        self.assertEqual(report.funding_rate_pct, 0.000001)
        self.assertEqual(report.status, STATUS_OUTFLOW_OK)

    def test_engine_is_deterministic_across_repeated_calls(self):
        engine = PerpetualFuturesFundingRateHandlingEngine()
        update = FundingRateUpdate("BTCUSDT", 0.0001, FUNDING_TS, 8)
        first = engine.process_funding_update(long_btc(), update)
        second = engine.process_funding_update(long_btc(), update)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
