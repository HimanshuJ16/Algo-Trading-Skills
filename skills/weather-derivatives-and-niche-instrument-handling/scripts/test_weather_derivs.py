"""
Tests for the weather derivatives engine.

Every asserted number is derived **independently** of the engine, from the contract
definitions in ``references/standards.md``:

- Degree-day arithmetic is worked by hand in the test body (e.g. 10 days at
  T_mean = 30 F against a 65 F base is 10 * 35 = 350 HDD points).
- Multipliers come from the published contract specifications, not from the module's
  own constants: USD 20/point for CME US degree-day contracts, EUR 20/point for CME
  European HDD and CAT, JPY 2,500/point for CME Pacific Rim (Tokyo) CAT.
- The detrending test builds a series with an exactly known slope, so the expected
  adjusted values are computable in closed form without running an OLS fit.
- The percentile test uses a sample whose 5th percentile under linear interpolation
  is computable by hand.

Regression coverage for the defects fixed in v2.0.0 is marked ``REGRESSION``; each
such test fails against the v1.1.0 implementation.
"""
import datetime
import math
import unittest

from weather_derivs import (
    CME_CONTRACT_SPECS,
    InstrumentType,
    SettlementPayoff,
    TemperatureUnit,
    WeatherDerivativeContract,
    WeatherDerivativeError,
    WeatherDerivativesEngine,
    WeatherIndexType,
)


class TestContractSpecs(unittest.TestCase):
    """The multiplier and its currency are venue-specific, not universally USD 20."""

    def test_us_degree_day_spec_is_twenty_usd_at_sixty_five_fahrenheit(self):
        spec = CME_CONTRACT_SPECS["CME_US_DEGREE_DAY"]
        self.assertEqual(spec.tick_value, 20.0)
        self.assertEqual(spec.currency, "USD")
        self.assertEqual(spec.base_temperature, 65.0)
        self.assertIs(spec.temperature_unit, TemperatureUnit.FAHRENHEIT)

    def test_european_hdd_spec_is_twenty_eur_at_eighteen_celsius(self):
        spec = CME_CONTRACT_SPECS["CME_EUROPEAN_HDD"]
        self.assertEqual(spec.tick_value, 20.0)
        self.assertEqual(spec.currency, "EUR")
        self.assertEqual(spec.base_temperature, 18.0)
        self.assertIs(spec.temperature_unit, TemperatureUnit.CELSIUS)

    def test_pacific_rim_cat_spec_is_2500_jpy(self):
        # REGRESSION: v1.1.0 documented and defaulted every contract, CAT included,
        # to a "$20 per index point" multiplier. Tokyo CAT is JPY 2,500 per point.
        spec = CME_CONTRACT_SPECS["CME_PACIFIC_RIM_CAT"]
        self.assertEqual(spec.tick_value, 2500.0)
        self.assertEqual(spec.currency, "JPY")
        self.assertIsNone(spec.base_temperature)

    def test_european_cat_spec_is_twenty_eur_not_usd(self):
        # REGRESSION: CAT is a European / Pacific Rim index and is never USD 20.
        spec = CME_CONTRACT_SPECS["CME_EUROPEAN_CAT"]
        self.assertEqual(spec.currency, "EUR")
        self.assertNotEqual(spec.currency, "USD")

    def test_from_spec_populates_multiplier_and_currency(self):
        contract = WeatherDerivativeContract.from_spec(
            "CME_PACIFIC_RIM_CAT",
            contract_id="CME-CAT-TYO-JUL",
            symbol="TOKYO_CAT",
            location="TOKYO",
            index_type=WeatherIndexType.CAT,
            instrument_type=InstrumentType.CAPPED_SWAP,
        )
        self.assertEqual(contract.tick_value, 2500.0)
        self.assertEqual(contract.currency, "JPY")

    def test_from_spec_rejects_index_type_the_venue_does_not_list(self):
        with self.assertRaises(WeatherDerivativeError):
            WeatherDerivativeContract.from_spec(
                "CME_EUROPEAN_CAT",
                contract_id="BAD",
                symbol="BAD",
                location="LONDON",
                index_type=WeatherIndexType.CDD,   # CAT venue lists CAT only
                instrument_type=InstrumentType.FUTURES,
            )

    def test_from_spec_rejects_unknown_key(self):
        with self.assertRaises(WeatherDerivativeError):
            WeatherDerivativeContract.from_spec(
                "CME_ATLANTIS_HDD",
                contract_id="X", symbol="X", location="X",
                index_type=WeatherIndexType.HDD,
                instrument_type=InstrumentType.FUTURES,
            )


class TestIndexAccumulation(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherDerivativesEngine()

    def test_monthly_hdd_index_accumulation(self):
        # 10 days of T_min = 20 F, T_max = 40 F -> T_mean = 30 F
        # Daily HDD = 65 - 30 = 35 -> total = 350.
        idx = self.engine.calculate_monthly_index(
            [(20.0, 40.0)] * 10, WeatherIndexType.HDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0
        )
        self.assertEqual(idx, 350.0)

    def test_monthly_cdd_index_accumulation(self):
        # 5 days of T_mean = 85 F -> daily CDD = 20 -> total = 100.
        idx = self.engine.calculate_monthly_index(
            [(75.0, 95.0)] * 5, WeatherIndexType.CDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0
        )
        self.assertEqual(idx, 100.0)

    def test_degree_days_floor_at_zero_on_the_wrong_side_of_the_base(self):
        # T_mean = 80 F is above the 65 F base, so HDD contributes 0 on every day.
        idx = self.engine.calculate_monthly_index(
            [(70.0, 90.0)] * 7, WeatherIndexType.HDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0
        )
        self.assertEqual(idx, 0.0)

    def test_european_hdd_uses_eighteen_celsius_base(self):
        # REGRESSION: v1.1.0 hard-coded ``base_temp_f=65.0`` and would have returned
        # 10 * (65 - 5) = 600 for this Celsius series instead of 10 * (18 - 5) = 130.
        idx = self.engine.calculate_monthly_index(
            [(0.0, 10.0)] * 10, WeatherIndexType.HDD, TemperatureUnit.CELSIUS, base_temperature=18.0
        )
        self.assertEqual(idx, 130.0)

    def test_cat_index_sums_daily_means_and_may_be_negative(self):
        # CAT has no base. 10 days at T_mean = -3 C accumulate to -30.
        idx = self.engine.calculate_monthly_index(
            [(-8.0, 2.0)] * 10, WeatherIndexType.CAT, TemperatureUnit.CELSIUS
        )
        self.assertEqual(idx, -30.0)

    def test_cat_rejects_a_base_temperature(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(10.0, 20.0)], WeatherIndexType.CAT, TemperatureUnit.CELSIUS, base_temperature=18.0
            )

    def test_cat_rejects_fahrenheit(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(50.0, 70.0)], WeatherIndexType.CAT, TemperatureUnit.FAHRENHEIT
            )

    def test_degree_days_require_an_explicit_base_temperature(self):
        # REGRESSION: a silent 65.0 default is wrong for every non-US contract.
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(0.0, 10.0)], WeatherIndexType.HDD, TemperatureUnit.CELSIUS
            )

    def test_nan_temperature_raises_instead_of_silently_scoring_zero(self):
        # REGRESSION: ``max(0.0, float('nan'))`` is 0.0 in Python, so v1.1.0 absorbed a
        # missing observation as a zero-degree-day day and understated the index.
        self.assertEqual(max(0.0, float("nan")), 0.0)   # the language behaviour being guarded
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(20.0, 40.0), (float("nan"), 40.0)],
                WeatherIndexType.HDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0,
            )

    def test_inverted_min_max_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(50.0, 20.0)], WeatherIndexType.HDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0
            )

    def test_empty_series_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [], WeatherIndexType.HDD, TemperatureUnit.FAHRENHEIT, base_temperature=65.0
            )

    def test_non_enum_index_type_raises_a_domain_error(self):
        # REGRESSION: v1.1.0 fell through its if/elif chain and raised UnboundLocalError.
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(20.0, 40.0)], "HDD", TemperatureUnit.FAHRENHEIT, base_temperature=65.0
            )

    def test_missing_temperature_unit_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index(
                [(20.0, 40.0)], WeatherIndexType.HDD, "FAHRENHEIT", base_temperature=65.0
            )


class TestSettlement(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherDerivativesEngine()

        self.us_hdd_futures = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-HDD-NY-JAN25",
            symbol="H1F25_NY",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.FUTURES,
            entry_index_price=880.0,
        )

        self.us_hdd_call = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-CALL-NY-800",
            symbol="H1F25_C800",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CALL_OPTION,
            strike_index=800.0,
        )

        self.us_hdd_put = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-PUT-NY-800",
            symbol="H1F25_P800",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.PUT_OPTION,
            strike_index=800.0,
        )

        self.capped_swap = WeatherDerivativeContract(
            contract_id="OTC-SWAP-CHI-1000",
            symbol="OTC_SWAP_ORD",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            currency="USD",
            strike_index=1000.0,
            max_payout=5000.0,
        )

    def test_futures_settlement_value_is_index_times_multiplier(self):
        # 900 HDD points * USD 20/point = USD 18,000 cash settlement value.
        self.assertEqual(self.engine.final_settlement_value(self.us_hdd_futures, 900.0), 18000.0)

    def test_futures_payoff_is_measured_from_the_entry_index_price(self):
        # REGRESSION: v1.1.0 returned index * multiplier (USD 18,000) as the futures
        # "payoff", overstating P&L by the entire entry notional. Bought at 880,
        # settled at 900 -> (900 - 880) * 20 = USD 400.
        payoff = self.engine.calculate_settlement_payoff(self.us_hdd_futures, 900.0)
        self.assertEqual(payoff.total_payoff, 400.0)
        self.assertNotEqual(payoff.total_payoff, 18000.0)

    def test_futures_payoff_is_negative_below_the_entry_price(self):
        # Settled at 850 against an 880 entry -> (850 - 880) * 20 = -USD 600.
        payoff = self.engine.calculate_settlement_payoff(self.us_hdd_futures, 850.0)
        self.assertEqual(payoff.total_payoff, -600.0)

    def test_futures_payoff_scales_with_quantity_and_direction(self):
        short_five = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-HDD-NY-SHORT5",
            symbol="H1F25_NY",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.FUTURES,
            entry_index_price=880.0,
            quantity=-5.0,
        )
        # Short 5 lots, index rises 20 points: -5 * 20 pts * USD 20 = -USD 2,000.
        self.assertEqual(self.engine.calculate_settlement_payoff(short_five, 900.0).total_payoff, -2000.0)

    def test_futures_payoff_without_entry_price_raises(self):
        no_entry = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-HDD-NOENTRY",
            symbol="H1F25_NY",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.FUTURES,
        )
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_settlement_payoff(no_entry, 900.0)

    def test_call_option_intrinsic_payoff(self):
        # Index 950, strike 800 -> 150 points * USD 20 = USD 3,000.
        self.assertEqual(self.engine.calculate_settlement_payoff(self.us_hdd_call, 950.0).total_payoff, 3000.0)
        # Out of the money.
        self.assertEqual(self.engine.calculate_settlement_payoff(self.us_hdd_call, 750.0).total_payoff, 0.0)

    def test_option_payoff_is_zero_exactly_at_the_strike(self):
        self.assertEqual(self.engine.calculate_settlement_payoff(self.us_hdd_call, 800.0).total_payoff, 0.0)
        self.assertEqual(self.engine.calculate_settlement_payoff(self.us_hdd_put, 800.0).total_payoff, 0.0)

    def test_put_option_intrinsic_payoff(self):
        # Index 700, strike 800 -> 100 points * USD 20 = USD 2,000.
        self.assertEqual(self.engine.calculate_settlement_payoff(self.us_hdd_put, 700.0).total_payoff, 2000.0)

    def test_capped_swap_gain_is_capped(self):
        # Uncapped: (1400 - 1000) * USD 20 = USD 8,000, capped to USD 5,000.
        payoff = self.engine.calculate_settlement_payoff(self.capped_swap, 1400.0)
        self.assertEqual(payoff.gross_payoff, 8000.0)
        self.assertEqual(payoff.total_payoff, 5000.0)
        self.assertTrue(payoff.is_capped)

    def test_capped_swap_loss_is_floored_symmetrically(self):
        # Uncapped: (600 - 1000) * USD 20 = -USD 8,000, floored to -USD 5,000.
        payoff = self.engine.calculate_settlement_payoff(self.capped_swap, 600.0)
        self.assertEqual(payoff.total_payoff, -5000.0)
        self.assertTrue(payoff.is_capped)

    def test_payoff_exactly_at_the_cap_is_not_flagged_capped(self):
        # (1250 - 1000) * USD 20 = USD 5,000 exactly -- reached, not truncated.
        payoff = self.engine.calculate_settlement_payoff(self.capped_swap, 1250.0)
        self.assertEqual(payoff.total_payoff, 5000.0)
        self.assertFalse(payoff.is_capped)

    def test_asymmetric_cap_and_floor(self):
        asymmetric = WeatherDerivativeContract(
            contract_id="OTC-SWAP-ASYM",
            symbol="OTC_ASYM",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            strike_index=1000.0,
            max_payout=5000.0,
            max_loss=2000.0,
        )
        self.assertEqual(self.engine.calculate_settlement_payoff(asymmetric, 1400.0).total_payoff, 5000.0)
        self.assertEqual(self.engine.calculate_settlement_payoff(asymmetric, 600.0).total_payoff, -2000.0)

    def test_uncapped_swap_is_uncapped(self):
        uncapped = WeatherDerivativeContract(
            contract_id="OTC-SWAP-UNCAPPED",
            symbol="OTC_UNCAPPED",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            strike_index=1000.0,
        )
        payoff = self.engine.calculate_settlement_payoff(uncapped, 1400.0)
        self.assertEqual(payoff.total_payoff, 8000.0)
        self.assertFalse(payoff.is_capped)

    def test_zero_max_payout_means_a_zero_cap_not_uncapped(self):
        # REGRESSION: v1.1.0 used ``max_payout_usd > 0.0`` as the "is there a cap"
        # test, so an explicit zero cap silently disabled capping. ``None`` now means
        # uncapped and 0.0 means a genuine zero cap.
        zero_cap = WeatherDerivativeContract(
            contract_id="OTC-SWAP-ZEROCAP",
            symbol="OTC_ZERO",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            strike_index=1000.0,
            max_payout=0.0,
        )
        self.assertEqual(self.engine.calculate_settlement_payoff(zero_cap, 1400.0).total_payoff, 0.0)

    def test_negative_cat_index_settles_rather_than_raising(self):
        # REGRESSION: v1.1.0 rejected every negative accumulated index, but a CAT
        # index is a sum of Celsius daily means and is legitimately negative.
        # (-30 - (-50)) * JPY 2,500 = JPY 50,000.
        tokyo_cat = WeatherDerivativeContract.from_spec(
            "CME_PACIFIC_RIM_CAT",
            contract_id="CME-CAT-TYO",
            symbol="TOKYO_CAT",
            location="TOKYO",
            index_type=WeatherIndexType.CAT,
            instrument_type=InstrumentType.CAPPED_SWAP,
            strike_index=-50.0,
        )
        payoff = self.engine.calculate_settlement_payoff(tokyo_cat, -30.0)
        self.assertEqual(payoff.total_payoff, 50000.0)
        self.assertEqual(payoff.currency, "JPY")

    def test_negative_degree_day_index_still_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_settlement_payoff(self.us_hdd_call, -50.0)

    def test_non_finite_index_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_settlement_payoff(self.us_hdd_call, float("nan"))

    def test_payoff_carries_the_contract_currency(self):
        # REGRESSION: v1.1.0 named every payoff field ``*_usd`` regardless of venue.
        london_cat = WeatherDerivativeContract.from_spec(
            "CME_EUROPEAN_CAT",
            contract_id="CME-CAT-LON",
            symbol="LONDON_CAT",
            location="LONDON_EGLL",
            index_type=WeatherIndexType.CAT,
            instrument_type=InstrumentType.CALL_OPTION,
            strike_index=500.0,
        )
        payoff = self.engine.calculate_settlement_payoff(london_cat, 560.0)
        self.assertEqual(payoff.currency, "EUR")
        self.assertEqual(payoff.total_payoff, 1200.0)   # 60 points * EUR 20
        self.assertIsInstance(payoff, SettlementPayoff)


class TestContractValidation(unittest.TestCase):
    def _contract(self, **overrides):
        params = dict(
            contract_id="C", symbol="S", location="L",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
        )
        params.update(overrides)
        return WeatherDerivativeContract(**params)

    def test_valid_contract_constructs(self):
        self.assertEqual(self._contract().tick_value, 20.0)

    def test_non_positive_tick_value_raises(self):
        for bad in (0.0, -20.0, float("nan")):
            with self.assertRaises(WeatherDerivativeError):
                self._contract(tick_value=bad)

    def test_zero_quantity_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self._contract(quantity=0.0)

    def test_negative_cap_magnitude_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self._contract(max_payout=-100.0)
        with self.assertRaises(WeatherDerivativeError):
            self._contract(max_loss=-100.0)

    def test_non_enum_types_raise(self):
        with self.assertRaises(WeatherDerivativeError):
            self._contract(index_type="HDD")
        with self.assertRaises(WeatherDerivativeError):
            self._contract(instrument_type="FUTURES")

    def test_end_date_before_start_date_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self._contract(
                start_date=datetime.date(2025, 2, 1),
                end_date=datetime.date(2025, 1, 1),
            )


class TestDetrending(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherDerivativesEngine()

    def test_perfectly_linear_series_collapses_to_the_target_level(self):
        # y_j = 1000 - 10j has slope exactly -10 and zero residuals, so detrending to
        # the last season (j = 4, fitted 960) must return 960 for every season.
        series = [1000.0, 990.0, 980.0, 970.0, 960.0]
        self.assertEqual(
            self.engine.detrend_historical_indexes(series),
            [960.0] * 5,
        )

    def test_detrending_shifts_the_mean_toward_the_recent_climate(self):
        # A warming record: HDD falls 10 points per season. The raw mean (980) sits at
        # the midpoint of the record and overstates today's expected winter HDD; the
        # detrended mean equals the fitted level at the final season, 960.
        series = [1000.0, 990.0, 980.0, 970.0, 960.0]
        self.assertEqual(sum(series) / len(series), 980.0)
        adjusted = self.engine.detrend_historical_indexes(series)
        self.assertEqual(sum(adjusted) / len(adjusted), 960.0)

    def test_detrended_series_equals_the_ols_residuals_shifted_to_the_target_fit(self):
        # OLS worked by hand for y = [1000, 985, 984, 966, 955] at j = 0..4:
        #   mean_x = 2, mean_y = 4890/5 = 978, Sxx = 4+1+0+1+4 = 10,
        #   Sxy = (-2)(22) + (-1)(7) + (0)(6) + (1)(-12) + (2)(-23) = -109,
        #   slope b = -10.9, intercept a = 978 + 10.9*2 = 999.8.
        #   fit = [999.8, 988.9, 978.0, 967.1, 956.2]
        #   residuals = [0.2, -3.9, 6.0, -1.1, -1.2]   (sum zero)
        # Detrending to j = 4 shifts every residual onto fit(4) = 956.2, so the
        # adjusted series is the residuals plus 956.2 -- the trend is removed while
        # each season's departure from the fitted climate is preserved exactly.
        series = [1000.0, 985.0, 984.0, 966.0, 955.0]
        expected = [956.4, 952.3, 962.2, 955.1, 955.0]
        adjusted = self.engine.detrend_historical_indexes(series)
        for got, want in zip(adjusted, expected):
            self.assertAlmostEqual(got, want, places=9)

    def test_target_season_can_project_forward(self):
        # Projecting one season past the record (j = 5) on a -10/season trend gives 950.
        series = [1000.0, 990.0, 980.0, 970.0, 960.0]
        self.assertEqual(
            self.engine.detrend_historical_indexes(series, target_season=5),
            [950.0] * 5,
        )

    def test_flat_series_is_unchanged(self):
        series = [900.0] * 6
        for value in self.engine.detrend_historical_indexes(series):
            self.assertAlmostEqual(value, 900.0)

    def test_too_few_seasons_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.detrend_historical_indexes([900.0, 910.0])

    def test_non_finite_history_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.detrend_historical_indexes([900.0, float("inf"), 910.0])


class TestBurnAnalysis(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherDerivativesEngine()
        self.us_hdd_call = WeatherDerivativeContract.from_spec(
            "CME_US_DEGREE_DAY",
            contract_id="CME-CALL-NY-800",
            symbol="H1F25_C800",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CALL_OPTION,
            strike_index=800.0,
        )

    def test_burn_analysis_expected_payoff(self):
        # Seasons [800, 850, 900, 950, 1000] against an 800 call at USD 20/point give
        # payoffs [0, 1000, 2000, 3000, 4000] -> mean USD 2,000.
        res = self.engine.run_burn_analysis(self.us_hdd_call, [800.0, 850.0, 900.0, 950.0, 1000.0])
        self.assertEqual(res.historical_seasons_analyzed, 5)
        self.assertEqual(res.mean_index_value, 900.0)
        self.assertEqual(res.expected_payoff, 2000.0)
        self.assertEqual(res.currency, "USD")

    def test_burn_analysis_sample_standard_deviations(self):
        # Indexes [800, 850, 900, 950, 1000]: deviations [-100,-50,0,50,100],
        # sum of squares 25,000, sample variance 25,000/4 = 6,250, sd = 79.0569...
        res = self.engine.run_burn_analysis(self.us_hdd_call, [800.0, 850.0, 900.0, 950.0, 1000.0])
        self.assertAlmostEqual(res.std_dev_index, round(math.sqrt(6250.0), 2), places=2)
        # Payoffs [0,1000,2000,3000,4000]: sample variance 10,000,000/4 = 2,500,000.
        self.assertAlmostEqual(res.std_dev_payoff, round(math.sqrt(2_500_000.0), 2), places=2)

    def test_burn_analysis_reports_best_and_worst_realised_payoff(self):
        # REGRESSION: v1.1.0 reported only the maximum payoff while SKILL.md claimed
        # "maximum historical drawdowns". A short swap's risk is the worst outcome.
        swap = WeatherDerivativeContract(
            contract_id="OTC-SWAP-BURN",
            symbol="OTC_BURN",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            strike_index=900.0,
        )
        res = self.engine.run_burn_analysis(swap, [800.0, 850.0, 900.0, 950.0, 1000.0])
        self.assertEqual(res.best_historical_payoff, 2000.0)     # (1000-900)*20
        self.assertEqual(res.worst_historical_payoff, -2000.0)   # (800-900)*20
        self.assertEqual(res.expected_payoff, 0.0)

    def test_fifth_percentile_is_linearly_interpolated(self):
        # Payoffs sorted [-2000,-1000,0,1000,2000]; rank = 0.05*4 = 0.2, so the value
        # is -2000 + 0.2*(-1000 - -2000) = -1800.
        swap = WeatherDerivativeContract(
            contract_id="OTC-SWAP-PCT",
            symbol="OTC_PCT",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            tick_value=20.0,
            strike_index=900.0,
        )
        res = self.engine.run_burn_analysis(swap, [800.0, 850.0, 900.0, 950.0, 1000.0])
        self.assertEqual(res.payoff_5th_percentile, -1800.0)

    def test_discount_factor_scales_the_expected_payoff_only(self):
        res = self.engine.run_burn_analysis(
            self.us_hdd_call, [800.0, 850.0, 900.0, 950.0, 1000.0], discount_factor=0.98
        )
        self.assertEqual(res.expected_payoff, 1960.0)          # 2000 * 0.98
        self.assertEqual(res.best_historical_payoff, 4000.0)   # risk figures undiscounted

    def test_detrended_burn_differs_from_raw_burn_on_a_warming_record(self):
        # REGRESSION-adjacent: v1.1.0 documented detrending as mandatory but offered no
        # way to do it. On a -10 HDD/season record the raw burn overprices an 800 call.
        series = [1000.0, 990.0, 980.0, 970.0, 960.0]
        raw = self.engine.run_burn_analysis(self.us_hdd_call, series)
        detrended = self.engine.run_burn_analysis(
            self.us_hdd_call, self.engine.detrend_historical_indexes(series)
        )
        # Raw payoffs: [200,190,180,170,160]*20 -> mean 180*20 = 3,600.
        self.assertEqual(raw.expected_payoff, 3600.0)
        # Detrended: every season sits at 960 -> (960-800)*20 = 3,200.
        self.assertEqual(detrended.expected_payoff, 3200.0)
        self.assertLess(detrended.expected_payoff, raw.expected_payoff)

    def test_empty_history_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.run_burn_analysis(self.us_hdd_call, [])

    def test_single_season_raises(self):
        # REGRESSION: v1.1.0 divided by ``max(1, n-1)`` and reported a 0.0 standard
        # deviation for a one-season sample, which reads as "no weather risk".
        with self.assertRaises(WeatherDerivativeError):
            self.engine.run_burn_analysis(self.us_hdd_call, [900.0])

    def test_non_positive_discount_factor_raises(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.run_burn_analysis(self.us_hdd_call, [800.0, 900.0], discount_factor=0.0)


if __name__ == "__main__":
    unittest.main()
