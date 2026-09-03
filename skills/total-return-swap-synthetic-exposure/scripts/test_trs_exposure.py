import datetime
import unittest

from trs_exposure import (
    BENCHMARK_DAY_COUNT,
    BenchmarkRate,
    DayCountConvention,
    DERIVATIVES_ERROR,
    DividendBasis,
    DividendEvent,
    TRSContractConfig,
    TRSEngine,
    TRSModelError,
    TRSResetPeriod,
    TRSSettlement,
    TRSSide,
)


class TestTRSEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TRSEngine()
        self.config = TRSContractConfig(
            swap_id="TRS-AAPL-2026",
            symbol="AAPL",
            notional_amount_usd=1_000_000.0,
            initial_reference_price=200.0,
            quantity_shares=5_000.0,  # 5,000 shares * $200 = $1,000,000
            funding_benchmark=BenchmarkRate.SOFR,
            funding_spread_bps=50.0,  # SOFR + 0.50%
            day_count=DayCountConvention.ACT_360,
            initial_margin_pct=15.0,
        )

    def _reset(self, **overrides):
        """90-day Q1 reset: 2026-01-01 -> 2026-04-01 is exactly 31 + 28 + 31 = 90 days."""
        kwargs = dict(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 1),
            start_price=200.0,
            end_price=200.0,
            benchmark_rate_pct=5.00,
        )
        kwargs.update(overrides)
        return TRSResetPeriod(**kwargs)

    # ------------------------------------------------------------------ day count

    def test_day_count_fraction_actual_conventions(self):
        self.assertEqual(TRSEngine.calculate_day_count_fraction(90, DayCountConvention.ACT_360), 0.25)
        self.assertEqual(TRSEngine.calculate_day_count_fraction(73, DayCountConvention.ACT_365), 0.2)

    def test_day_count_fraction_rejects_thirty_360_from_day_count(self):
        """
        Regression: 30/360 used to be computed as actual_days / 360, which is Act/360.
        It is a function of the two calendar dates and cannot come from a day count.
        """
        with self.assertRaises(TRSModelError):
            TRSEngine.calculate_day_count_fraction(90, DayCountConvention.THIRTY_360)

    def test_day_count_fraction_rejects_negative_days(self):
        with self.assertRaises(TRSModelError):
            TRSEngine.calculate_day_count_fraction(-1, DayCountConvention.ACT_360)

    def test_thirty_360_bond_basis_end_of_month_rules(self):
        """
        Independently derived from the ISDA Bond Basis formula
        [360*(Y2-Y1) + 30*(M2-M1) + (D2-D1)] / 360, D1=31->30, D2=31->30 when D1 in {30,31}.

          31 Jan 2026 -> 31 Mar 2026: D1=30, D2=31->30 => 30*2 + 0 = 60/360
          (Act/360 over the same dates is 59/360 -- the two must differ.)
          31 Aug 2026 -> 28 Feb 2027: D1=30, D2=28 => 360 - 180 - 2 = 178/360
          1 Jan 2026 -> 1 Jul 2026:  180/360 exactly (Act/360 would be 181/360)
        """
        frac = TRSEngine.day_count_fraction_for_dates(
            datetime.date(2026, 1, 31), datetime.date(2026, 3, 31), DayCountConvention.THIRTY_360
        )
        self.assertAlmostEqual(frac, 60.0 / 360.0, places=12)
        self.assertNotAlmostEqual(frac, 59.0 / 360.0, places=6)

        self.assertAlmostEqual(
            TRSEngine.day_count_fraction_for_dates(
                datetime.date(2026, 8, 31), datetime.date(2027, 2, 28), DayCountConvention.THIRTY_360
            ),
            178.0 / 360.0,
            places=12,
        )
        self.assertAlmostEqual(
            TRSEngine.day_count_fraction_for_dates(
                datetime.date(2026, 1, 1), datetime.date(2026, 7, 1), DayCountConvention.THIRTY_360
            ),
            0.5,
            places=12,
        )

    def test_thirty_360_funding_differs_from_act_360(self):
        """A 30/360 contract must not accrue the Act/360 amount over a 181-day half-year."""
        reset = self._reset(end_date=datetime.date(2026, 7, 1))
        self.assertEqual(reset.days_in_period, 181)

        act_config = self.config
        thirty_config = TRSContractConfig(**{**act_config.__dict__, "day_count": DayCountConvention.THIRTY_360})

        # 30/360: $1,000,000 * 5.50% * 0.5 = $27,500
        self.assertAlmostEqual(self.engine.calculate_funding_leg(thirty_config, reset), 27_500.0, places=6)
        # Act/360: $1,000,000 * 5.50% * (181/360) = $27,652.777...
        self.assertAlmostEqual(
            self.engine.calculate_funding_leg(act_config, reset), 1_000_000.0 * 0.055 * 181.0 / 360.0, places=6
        )

    def test_benchmark_day_count_map_matches_market_convention(self):
        self.assertEqual(BENCHMARK_DAY_COUNT[BenchmarkRate.SOFR], DayCountConvention.ACT_360)
        self.assertEqual(BENCHMARK_DAY_COUNT[BenchmarkRate.ESTR], DayCountConvention.ACT_360)
        self.assertEqual(BENCHMARK_DAY_COUNT[BenchmarkRate.SONIA], DayCountConvention.ACT_365)
        self.assertEqual(BENCHMARK_DAY_COUNT[BenchmarkRate.TONA], DayCountConvention.ACT_365)

    # ------------------------------------------------------- total return leg

    def test_total_return_leg_gain_and_manufactured_dividends(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 1),
            payment_date=datetime.date(2026, 3, 15),
            gross_amount_per_share=1.00,
            withholding_tax_pct=15.0,  # Net = $0.85 per share
        )
        reset = self._reset(end_date=datetime.date(2026, 3, 31), end_price=220.0, dividends=[div])

        cap_return, net_div = self.engine.calculate_total_return_leg(self.config, reset)
        # Cap Return: 5,000 shares * $20 = $100,000
        self.assertEqual(cap_return, 100_000.0)
        # Net Dividends: 5,000 shares * $0.85 = $4,250
        self.assertEqual(net_div, 4_250.0)

    def test_dividend_outside_period_is_excluded_not_accrued(self):
        """
        Regression: every dividend in the list used to accrue regardless of its date, so a
        Q2 dividend inflated the Q1 total return leg by 5,000 * $1.00.
        """
        out_of_period = DividendEvent(
            ex_date=datetime.date(2026, 5, 15),
            payment_date=datetime.date(2026, 6, 1),
            gross_amount_per_share=1.00,
            dividend_id="Q2-DIV",
        )
        reset = self._reset(dividends=[out_of_period])

        _, net_div = self.engine.calculate_total_return_leg(self.config, reset)
        self.assertEqual(net_div, 0.0)

        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.net_manufactured_dividends_usd, 0.0)
        self.assertIn("Q2-DIV", settlement.excluded_dividend_ids)

    def test_dividend_period_boundaries_are_start_exclusive_end_inclusive(self):
        """2002 ISDA EDD 10.3 default Second Period: (start_date, end_date]."""
        on_start = DividendEvent(
            ex_date=datetime.date(2026, 1, 1),
            payment_date=datetime.date(2026, 1, 20),
            gross_amount_per_share=1.00,
            dividend_id="ON-START",
        )
        on_end = DividendEvent(
            ex_date=datetime.date(2026, 4, 1),
            payment_date=datetime.date(2026, 4, 20),
            gross_amount_per_share=2.00,
            dividend_id="ON-END",
        )
        reset = self._reset(dividends=[on_start, on_end])

        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        # Only ON-END accrues: 5,000 * $2.00 = $10,000
        self.assertEqual(settlement.net_manufactured_dividends_usd, 10_000.0)
        self.assertEqual(settlement.excluded_dividend_ids, ["ON-START"])

    def test_dividend_basis_paid_uses_payment_date(self):
        """Ex-date inside the period, payment date after it: PAID basis must exclude it."""
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 30),
            payment_date=datetime.date(2026, 4, 20),
            gross_amount_per_share=1.00,
            dividend_id="STRADDLE",
        )
        reset = self._reset(dividends=[div])

        ex_basis = TRSContractConfig(**{**self.config.__dict__, "dividend_basis": DividendBasis.EX})
        paid_basis = TRSContractConfig(**{**self.config.__dict__, "dividend_basis": DividendBasis.PAID})

        self.assertEqual(self.engine.calculate_total_return_leg(ex_basis, reset)[1], 5_000.0)
        self.assertEqual(self.engine.calculate_total_return_leg(paid_basis, reset)[1], 0.0)

    def test_record_basis_without_record_date_warns_and_falls_back_to_ex_date(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 1),
            payment_date=datetime.date(2026, 3, 15),
            gross_amount_per_share=1.00,
            dividend_id="NO-RECORD-DATE",
        )
        reset = self._reset(dividends=[div])
        config = TRSContractConfig(**{**self.config.__dict__, "dividend_basis": DividendBasis.RECORD})

        settlement = self.engine.process_reset_period(config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.net_manufactured_dividends_usd, 5_000.0)
        self.assertTrue(any("falling back to ex_date" in w for w in settlement.warnings))

    def test_zero_withholding_passes_through_gross_dividend(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 1),
            payment_date=datetime.date(2026, 3, 15),
            gross_amount_per_share=1.00,
        )
        self.assertEqual(div.net_amount_per_share, 1.00)

    # ----------------------------------------------------------- funding leg

    def test_funding_leg_sofr_plus_spread(self):
        # 90 days, SOFR = 5.00%, Spread = 0.50% -> Total = 5.50%
        funding = self.engine.calculate_funding_leg(self.config, self._reset())
        # Funding = $1,000,000 * 5.50% * (90 / 360) = $13,750
        self.assertEqual(funding, 13_750.0)

    def test_funding_leg_negative_all_in_rate_credits_the_receiver(self):
        """EUR/JPY benchmarks have printed negative fixings; the accrual must not be floored at zero."""
        config = TRSContractConfig(
            **{**self.config.__dict__, "funding_benchmark": BenchmarkRate.ESTR, "funding_spread_bps": 50.0}
        )
        reset = self._reset(benchmark_rate_pct=-0.60)  # all-in = -0.10%
        # $1,000,000 * -0.10% * 0.25 = -$250
        self.assertAlmostEqual(self.engine.calculate_funding_leg(config, reset), -250.0, places=9)

        settlement = self.engine.process_reset_period(config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertTrue(any("negative" in w for w in settlement.warnings))

    def test_funding_leg_uses_period_reset_notional_not_booked_notional(self):
        """Share-locked TRS: the notional resets to quantity * start_price each period."""
        reset = self._reset(start_price=250.0, end_price=250.0)  # notional resets to $1,250,000
        # $1,250,000 * 5.50% * 0.25 = $17,187.50
        self.assertAlmostEqual(self.engine.calculate_funding_leg(self.config, reset), 17_187.50, places=6)

    # ------------------------------------------------------------ settlement

    def test_process_reset_period_receiver_positive_return(self):
        reset = self._reset(end_price=210.0)  # +$10/share = $50,000 cap return
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.total_return_amount_usd, 50_000.0)
        self.assertEqual(settlement.funding_interest_amount_usd, 13_750.0)
        # Net Cashflow to Receiver = $50,000 - $13,750 = $36,250
        self.assertEqual(settlement.net_cashflow_usd, 36_250.0)
        self.assertEqual(settlement.current_mtm_usd, 36_250.0)
        self.assertEqual(settlement.synthetic_delta_shares, 5_000.0)

    def test_process_reset_period_receiver_negative_return(self):
        reset = self._reset(end_price=180.0)  # -$20/share = -$100,000 cap return
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.total_return_amount_usd, -100_000.0)
        # Net Cashflow = -$100,000 - $13,750 = -$113,750 (outflow from receiver)
        self.assertEqual(settlement.net_cashflow_usd, -113_750.0)

    def test_process_reset_period_payer_short_synthetic(self):
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.PAYER_TOTAL_RETURN)
        # Payer net cashflow = Funding ($13,750) - Total Return (-$100,000) = +$113,750 inflow
        self.assertEqual(settlement.net_cashflow_usd, 113_750.0)
        self.assertEqual(settlement.synthetic_delta_shares, -5_000.0)

    def test_payer_mtm_is_the_mirror_of_the_receiver_mtm(self):
        """
        Regression: current_mtm_usd used to be computed side-independently, reporting the
        receiver's loss as the payer's loss even though the payer was up $113,750.
        """
        reset = self._reset(end_price=180.0)
        receiver = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        payer = self.engine.process_reset_period(self.config, reset, TRSSide.PAYER_TOTAL_RETURN)

        self.assertEqual(receiver.current_mtm_usd, -113_750.0)
        self.assertEqual(payer.current_mtm_usd, 113_750.0)
        self.assertAlmostEqual(receiver.current_mtm_usd, -payer.current_mtm_usd, places=9)
        # The funding accrual itself is a gross figure, identical on both sides.
        self.assertEqual(receiver.funding_interest_amount_usd, payer.funding_interest_amount_usd)

    def test_settlement_dataclass_is_constructible_from_the_core_fields(self):
        """The eight original fields remain positional; the reporting fields are defaulted."""
        settlement = TRSSettlement("P", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
        self.assertEqual(settlement.period_notional_usd, 0.0)
        self.assertEqual(settlement.warnings, [])

    # ---------------------------------------------------------------- margin

    def test_initial_margin_is_not_netted_against_the_variation_margin_call(self):
        """
        Regression: VM used to be max(0, -net_cashflow - initial_margin), which reported a
        $0 call on a $113,750 loss because the $150,000 IM was treated as an offset.
        BCBS-IOSCO requires IM to be exchanged gross and segregated; it cannot fund VM.
        """
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)

        self.assertEqual(settlement.variation_margin_requirement_usd, 113_750.0)
        self.assertEqual(settlement.variation_margin_due_usd, 113_750.0)
        # IM and maintenance margin sit on the period notional and are reported separately.
        self.assertEqual(settlement.period_notional_usd, 1_000_000.0)
        self.assertEqual(settlement.initial_margin_requirement_usd, 150_000.0)
        self.assertEqual(settlement.maintenance_margin_requirement_usd, 100_000.0)

    def test_in_the_money_party_owes_no_variation_margin(self):
        reset = self._reset(end_price=210.0)
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.variation_margin_requirement_usd, 0.0)
        self.assertEqual(settlement.variation_margin_due_usd, 0.0)

    def test_variation_margin_nets_collateral_already_posted(self):
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(
            self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN, collateral_already_posted_usd=100_000.0
        )
        self.assertEqual(settlement.variation_margin_requirement_usd, 113_750.0)
        self.assertEqual(settlement.variation_margin_due_usd, 13_750.0)

    def test_over_collateralised_party_is_owed_a_return_of_margin(self):
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(
            self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN, collateral_already_posted_usd=150_000.0
        )
        self.assertEqual(settlement.variation_margin_due_usd, -36_250.0)

    def test_minimum_transfer_amount_suppresses_a_small_call(self):
        config = TRSContractConfig(**{**self.config.__dict__, "minimum_transfer_amount_usd": 200_000.0})
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        # Requirement still stands; the transfer is below the MTA so no collateral moves.
        self.assertEqual(settlement.variation_margin_requirement_usd, 113_750.0)
        self.assertEqual(settlement.variation_margin_due_usd, 0.0)

    def test_variation_margin_threshold_reduces_the_requirement(self):
        config = TRSContractConfig(**{**self.config.__dict__, "vm_threshold_usd": 50_000.0})
        reset = self._reset(end_price=180.0)
        settlement = self.engine.process_reset_period(config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.variation_margin_requirement_usd, 63_750.0)

    # ------------------------------------------------------------ validation

    def test_invalid_dates_raises_error(self):
        bad_reset = self._reset(
            period_id="BAD",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 1, 1),  # end before start
        )
        with self.assertRaises(DERIVATIVES_ERROR):
            self.engine.calculate_funding_leg(self.config, bad_reset)

    def test_legacy_exception_alias_is_the_current_error_type(self):
        self.assertIs(DERIVATIVES_ERROR, TRSModelError)

    def test_zero_length_period_raises_error(self):
        bad_reset = self._reset(end_date=datetime.date(2026, 1, 1))
        with self.assertRaises(TRSModelError):
            self.engine.calculate_funding_leg(self.config, bad_reset)

    def test_non_positive_prices_are_rejected(self):
        for bad in (0.0, -200.0):
            with self.assertRaises(TRSModelError):
                self.engine.calculate_total_return_leg(self.config, self._reset(end_price=bad))

    def test_nan_price_is_rejected_rather_than_propagated(self):
        with self.assertRaises(TRSModelError):
            self.engine.calculate_total_return_leg(self.config, self._reset(end_price=float("nan")))

    def test_negative_share_quantity_is_rejected(self):
        config = TRSContractConfig(**{**self.config.__dict__, "quantity_shares": -5_000.0})
        with self.assertRaises(TRSModelError):
            self.engine.calculate_funding_leg(config, self._reset())

    def test_withholding_tax_outside_percentage_range_is_rejected(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 1),
            payment_date=datetime.date(2026, 3, 15),
            gross_amount_per_share=1.00,
            withholding_tax_pct=150.0,  # a fraction (0.15) passed as a percentage field
        )
        with self.assertRaises(TRSModelError):
            self.engine.calculate_total_return_leg(self.config, self._reset(dividends=[div]))

    def test_dividend_payment_before_ex_date_is_rejected(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 15),
            payment_date=datetime.date(2026, 3, 1),
            gross_amount_per_share=1.00,
        )
        with self.assertRaises(TRSModelError):
            self.engine.calculate_total_return_leg(self.config, self._reset(dividends=[div]))

    def test_margin_percentages_outside_range_are_rejected(self):
        config = TRSContractConfig(**{**self.config.__dict__, "initial_margin_pct": 150.0})
        with self.assertRaises(TRSModelError):
            self.engine.calculate_funding_leg(config, self._reset())

    # -------------------------------------------------------- booking checks

    def test_day_count_mismatch_against_benchmark_convention_is_flagged(self):
        """SONIA accrues Act/365; booking it Act/360 over-accrues by ~1.4% of the interest."""
        config = TRSContractConfig(
            **{
                **self.config.__dict__,
                "funding_benchmark": BenchmarkRate.SONIA,
                "day_count": DayCountConvention.ACT_360,
            }
        )
        settlement = self.engine.process_reset_period(config, self._reset(), TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertTrue(any("market convention ACT_365" in w for w in settlement.warnings))

    def test_booked_notional_inconsistent_with_shares_times_price_is_flagged(self):
        config = TRSContractConfig(**{**self.config.__dict__, "notional_amount_usd": 2_000_000.0})
        settlement = self.engine.process_reset_period(config, self._reset(), TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertTrue(any("disagrees with" in w for w in settlement.warnings))

    def test_consistent_booking_produces_no_warnings(self):
        settlement = self.engine.process_reset_period(self.config, self._reset(), TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.warnings, [])


if __name__ == "__main__":
    unittest.main()
