import unittest

from interest_rate_swap_exposure_in_multi_asset_portfolios import (
    BASIS_POINT,
    InterestRateSwapExposureEngine,
    InterestRateSwapExposureReport,
    IrsHedgeSpec,
    IrsPositionSpec,
    MultiAssetPortfolioRisk,
    swap_annuity_factor,
)


def brute_force_annuity(fixed_rate_pct: float, tenor_years: float, frequency: int = 1) -> float:
    """
    Independent reference annuity: an explicit sum of (accrual x discount factor)
    over every period. Deliberately NOT the closed form the engine uses, so a
    regression in the closed form cannot hide behind a matching expectation.
    """
    rate = fixed_rate_pct / 100.0
    periods = int(round(tenor_years * frequency))
    return sum((1.0 / frequency) * (1.0 + rate / frequency) ** -i for i in range(1, periods + 1))


class TestSwapAnnuityFactor(unittest.TestCase):

    def test_matches_explicit_cashflow_summation(self):
        for rate_pct, tenor, freq in [
            (4.25, 5.0, 1),
            (4.00, 5.0, 1),
            (4.00, 10.0, 1),
            (4.25, 5.0, 2),
            (3.00, 2.0, 4),
            (-0.50, 5.0, 1),  # Negative rates are real (EUR/JPY/CHF eras)
        ]:
            with self.subTest(rate_pct=rate_pct, tenor=tenor, freq=freq):
                self.assertAlmostEqual(
                    swap_annuity_factor(rate_pct, tenor, freq),
                    brute_force_annuity(rate_pct, tenor, freq),
                    places=10,
                )

    def test_zero_rate_limit_equals_tenor(self):
        # With no discounting the annuity is just the sum of accrual factors = tenor.
        self.assertAlmostEqual(swap_annuity_factor(0.0, 7.0, 1), 7.0, places=12)
        self.assertAlmostEqual(swap_annuity_factor(0.0, 7.0, 4), 7.0, places=12)

    def test_annuity_is_continuous_across_the_zero_rate_branch(self):
        # The closed form has a removable singularity at y = 0; the limit branch
        # must join smoothly rather than jump.
        near_zero = swap_annuity_factor(1e-8, 5.0, 1)
        at_zero = swap_annuity_factor(0.0, 5.0, 1)
        self.assertAlmostEqual(near_zero, at_zero, places=6)

    def test_annuity_is_well_below_tenor_and_far_above_half_tenor(self):
        # Regression guard against the previous "modified duration = tenor / 2"
        # approximation, which understated the 5Y annuity by roughly 43%.
        annuity_5y = swap_annuity_factor(4.25, 5.0, 1)
        self.assertLess(annuity_5y, 5.0)
        self.assertGreater(annuity_5y, 4.0)

    def test_annuity_increases_with_tenor_and_decreases_with_rate(self):
        self.assertGreater(swap_annuity_factor(4.0, 10.0), swap_annuity_factor(4.0, 5.0))
        self.assertLess(swap_annuity_factor(8.0, 5.0), swap_annuity_factor(2.0, 5.0))

    def test_rejects_non_positive_tenor(self):
        for bad_tenor in (0.0, -5.0):
            with self.subTest(tenor=bad_tenor):
                with self.assertRaises(ValueError):
                    swap_annuity_factor(4.25, bad_tenor)

    def test_rejects_non_finite_inputs(self):
        with self.assertRaises(ValueError):
            swap_annuity_factor(float("nan"), 5.0)
        with self.assertRaises(ValueError):
            swap_annuity_factor(4.25, float("inf"))

    def test_rejects_unsupported_payment_frequency(self):
        with self.assertRaises(ValueError):
            swap_annuity_factor(4.25, 5.0, 3)

    def test_rejects_rate_producing_non_positive_discount_factor(self):
        # y/f <= -100% would make the discount factor undefined.
        with self.assertRaises(ValueError):
            swap_annuity_factor(-100.0, 5.0, 1)


class TestInterestRateSwapExposureEngine(unittest.TestCase):

    def setUp(self):
        self.engine = InterestRateSwapExposureEngine()

    def _spec(self, side="PAY_FIXED", **overrides):
        kwargs = dict(
            swap_id="IRS_01",
            notional_usd=10_000_000.0,
            pay_receive_type=side,
            fixed_rate_pct=4.25,
            tenor_years=5.0,
        )
        kwargs.update(overrides)
        return IrsPositionSpec(**kwargs)

    def test_pay_fixed_swap_dv01_matches_annuity_times_notional(self):
        # 5Y annual @ 4.25%: annuity = 4.4207289459 (explicit cashflow sum).
        # DV01 = $10,000,000 * 4.4207289459 * 0.0001 = +$4,420.73 per bps.
        expected = 10_000_000.0 * brute_force_annuity(4.25, 5.0, 1) * BASIS_POINT
        self.assertAlmostEqual(expected, 4420.7289459, places=6)
        self.assertAlmostEqual(self.engine.calculate_swap_dv01(self._spec()), expected, places=6)

    def test_receive_fixed_dv01_is_exact_negative_of_pay_fixed(self):
        pay = self.engine.calculate_swap_dv01(self._spec("PAY_FIXED"))
        receive = self.engine.calculate_swap_dv01(self._spec("RECEIVE_FIXED"))
        self.assertEqual(receive, -pay)
        self.assertGreater(pay, 0.0)
        self.assertLess(receive, 0.0)

    def test_semi_annual_fixed_leg_has_larger_annuity_than_annual(self):
        # More frequent payments pull cashflows earlier -> higher annuity -> higher DV01.
        annual = self.engine.calculate_swap_dv01(self._spec(payment_frequency_per_year=1))
        semi = self.engine.calculate_swap_dv01(self._spec(payment_frequency_per_year=2))
        self.assertGreater(semi, annual)

    def test_side_string_is_normalised(self):
        self.assertAlmostEqual(
            self.engine.calculate_swap_dv01(self._spec("  pay_fixed ")),
            self.engine.calculate_swap_dv01(self._spec("PAY_FIXED")),
            places=10,
        )

    def test_zero_notional_swap_has_zero_dv01(self):
        self.assertEqual(self.engine.calculate_swap_dv01(self._spec(notional_usd=0.0)), 0.0)

    def test_rejects_invalid_swap_side(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_swap_dv01(self._spec("PAY_FLOATING"))

    def test_rejects_negative_notional(self):
        # Direction belongs to pay_receive_type; a negative notional would
        # silently flip the DV01 sign a second time.
        with self.assertRaises(ValueError):
            self.engine.calculate_swap_dv01(self._spec(notional_usd=-10_000_000.0))

    def test_rejects_nan_notional(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_swap_dv01(self._spec(notional_usd=float("nan")))

    def test_rejects_non_positive_tenor_instead_of_flooring_it(self):
        # The previous implementation floored duration at 0.5, silently pricing
        # an expired or malformed swap as a live 1Y position.
        for bad_tenor in (0.0, -5.0):
            with self.subTest(tenor=bad_tenor):
                with self.assertRaises(ValueError):
                    self.engine.calculate_swap_dv01(self._spec(tenor_years=bad_tenor))

    def test_rejects_unknown_floating_index(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_swap_dv01(self._spec(floating_rate_index="MIBOR"))

    def test_rejects_index_currency_mismatch(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_swap_dv01(self._spec(floating_rate_index="EURIBOR", currency="USD"))

    def test_rejects_non_usd_swap_rather_than_aggregating_it(self):
        # EUR DV01 is not FX-additive with USD DV01; the engine must refuse.
        with self.assertRaises(ValueError) as ctx:
            self.engine.calculate_swap_dv01(self._spec(floating_rate_index="EURIBOR", currency="EUR"))
        self.assertIn("USD", str(ctx.exception))


class TestPortfolioAggregationAndHedging(unittest.TestCase):

    def setUp(self):
        self.engine = InterestRateSwapExposureEngine()
        self.hedge = IrsHedgeSpec(tenor_years=5.0, fixed_rate_pct=4.0, payment_frequency_per_year=1)

    def test_aggregation_and_hedge_notional_against_independent_arithmetic(self):
        # Bonds: -$5,000/bps (long bond book loses when rates rise).
        # IRS:   $10M pay-fixed 5Y @ 4.25% -> +10M * 4.4207289459 * 1e-4 = +$4,420.73/bps.
        # Net:   -$579.27/bps. Hedge: 5Y par swap @ 4.00%, annuity 4.4518223310,
        #        DV01/$ = 4.4518223310e-4 -> notional = 579.27 / 4.4518223310e-4.
        swap_dv01 = 10_000_000.0 * brute_force_annuity(4.25, 5.0, 1) * BASIS_POINT
        net_dv01 = -5_000.0 + swap_dv01
        hedge_dv01_per_dollar = brute_force_annuity(4.0, 5.0, 1) * BASIS_POINT
        expected_hedge_notional = -net_dv01 / hedge_dv01_per_dollar

        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=5_000_000.0,
            irs_positions=[
                IrsPositionSpec("IRS_01", 10_000_000.0, "PAY_FIXED", 4.25, 5.0)
            ],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertIsInstance(report, InterestRateSwapExposureReport)
        self.assertEqual(report.status, "IRS_RISK_AUDIT_SUCCESS")
        self.assertEqual(report.total_irs_notional_usd, 10_000_000.0)
        self.assertAlmostEqual(report.total_irs_dv01_usd, round(swap_dv01, 2), places=2)
        self.assertAlmostEqual(report.net_portfolio_dv01_usd, round(net_dv01, 2), places=2)
        self.assertAlmostEqual(report.pnl_impact_plus_10bps_usd, round(net_dv01 * 10.0, 2), places=2)
        self.assertAlmostEqual(
            report.required_hedge_irs_notional_usd, expected_hedge_notional, delta=0.01
        )
        # Net DV01 is still slightly negative, so more pay-fixed is required.
        self.assertEqual(report.required_hedge_side, "PAY_FIXED")
        self.assertGreater(report.required_hedge_notional_abs_usd, 0.0)
        self.assertFalse(report.hedge_rate_is_default)

    def test_bond_only_portfolio_requires_pay_fixed_hedge(self):
        # -$5,000/bps of bond risk hedged with a 5Y par swap @ 4.00%:
        # notional = 5,000 / (4.4518223310 * 1e-4) = $11,231,355.67.
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        expected = 5_000.0 / (brute_force_annuity(4.0, 5.0, 1) * BASIS_POINT)
        self.assertAlmostEqual(expected, 11_231_355.67, delta=0.01)
        self.assertEqual(report.total_irs_notional_usd, 0.0)
        self.assertEqual(report.total_irs_dv01_usd, 0.0)
        self.assertEqual(report.required_hedge_side, "PAY_FIXED")
        self.assertAlmostEqual(report.required_hedge_irs_notional_usd, expected, delta=0.01)

    def test_short_bond_book_requires_receive_fixed_hedge(self):
        # Positive net DV01 (gains when rates rise) must be offset by receiving fixed.
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=+5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertEqual(report.required_hedge_side, "RECEIVE_FIXED")
        self.assertLess(report.required_hedge_irs_notional_usd, 0.0)
        self.assertAlmostEqual(
            report.required_hedge_notional_abs_usd,
            abs(report.required_hedge_irs_notional_usd),
            places=6,
        )

    def test_applying_the_reported_hedge_flattens_net_dv01(self):
        # End-to-end closure: book the recommended hedge and re-run; the residual
        # DV01 must round to zero and no further hedge should be required.
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        first = self.engine.analyze_portfolio_irs_exposure(portfolio)

        hedged = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[
                IrsPositionSpec(
                    "HEDGE_01",
                    first.required_hedge_notional_abs_usd,
                    first.required_hedge_side,
                    self.hedge.fixed_rate_pct,
                    self.hedge.tenor_years,
                )
            ],
            hedge_instrument=self.hedge,
        )
        second = self.engine.analyze_portfolio_irs_exposure(hedged)

        self.assertAlmostEqual(second.net_portfolio_dv01_usd, 0.0, places=2)
        self.assertLess(abs(second.required_hedge_irs_notional_usd), 1.0)
        # A residual too small to round into a notional must not be reported as a
        # directional trade: "trade RECEIVE_FIXED $0.00" is an actionable-looking
        # instruction with no meaning.
        if second.required_hedge_irs_notional_usd == 0.0:
            self.assertEqual(second.required_hedge_side, "NONE")

    def test_hedge_side_and_zero_notional_never_disagree(self):
        # Residual DV01 above the neutrality tolerance but far too small to round
        # into a notional at 2 dp.
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-2e-6,
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertEqual(report.required_hedge_irs_notional_usd, 0.0)
        self.assertEqual(report.required_hedge_side, "NONE")
        self.assertEqual(report.required_hedge_notional_abs_usd, 0.0)

    def test_already_neutral_portfolio_reports_no_hedge(self):
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=0.0,
            equities_notional_usd=1_000_000.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertEqual(report.net_portfolio_dv01_usd, 0.0)
        self.assertEqual(report.required_hedge_side, "NONE")
        self.assertEqual(report.required_hedge_irs_notional_usd, 0.0)
        self.assertEqual(report.required_hedge_notional_abs_usd, 0.0)

    def test_offsetting_swaps_net_to_zero_dv01_but_gross_notional_is_preserved(self):
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=0.0,
            equities_notional_usd=0.0,
            irs_positions=[
                IrsPositionSpec("IRS_PAY", 10_000_000.0, "PAY_FIXED", 4.25, 5.0),
                IrsPositionSpec("IRS_REC", 10_000_000.0, "RECEIVE_FIXED", 4.25, 5.0),
            ],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertEqual(report.total_irs_dv01_usd, 0.0)
        # Gross notional stays at $20M: DV01 neutrality is not counterparty-risk neutrality.
        self.assertEqual(report.total_irs_notional_usd, 20_000_000.0)
        self.assertEqual(report.required_hedge_side, "NONE")

    def test_missing_hedge_instrument_flags_the_default_par_rate(self):
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[],
        )
        with self.assertLogs(
            "interest_rate_swap_exposure_in_multi_asset_portfolios", level="WARNING"
        ):
            report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertTrue(report.hedge_rate_is_default)
        self.assertEqual(report.hedge_par_rate_pct, 4.0)
        self.assertEqual(report.hedge_tenor_years, 5.0)
        self.assertAlmostEqual(
            report.hedge_annuity_factor, brute_force_annuity(4.0, 5.0, 1), places=10
        )

    def test_pnl_impact_is_exactly_linear_in_the_shock(self):
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)
        self.assertEqual(report.pnl_impact_plus_10bps_usd, report.net_portfolio_dv01_usd * 10.0)

    def test_rejects_non_finite_bond_dv01(self):
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=float("nan"),
            equities_notional_usd=0.0,
            irs_positions=[],
            hedge_instrument=self.hedge,
        )
        with self.assertRaises(ValueError):
            self.engine.analyze_portfolio_irs_exposure(portfolio)

    def test_one_bad_position_fails_the_whole_audit(self):
        # A risk report that silently drops an unparseable position is worse than
        # no report at all.
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5_000.0,
            equities_notional_usd=0.0,
            irs_positions=[
                IrsPositionSpec("IRS_OK", 10_000_000.0, "PAY_FIXED", 4.25, 5.0),
                IrsPositionSpec("IRS_BAD", 10_000_000.0, "PAY_FIXED", 4.25, -1.0),
            ],
            hedge_instrument=self.hedge,
        )
        with self.assertRaises(ValueError):
            self.engine.analyze_portfolio_irs_exposure(portfolio)

    def test_equities_notional_contributes_no_dv01(self):
        base = MultiAssetPortfolioRisk(-5_000.0, 0.0, [], hedge_instrument=self.hedge)
        with_equity = MultiAssetPortfolioRisk(
            -5_000.0, 500_000_000.0, [], hedge_instrument=self.hedge
        )
        self.assertEqual(
            self.engine.analyze_portfolio_irs_exposure(base).net_portfolio_dv01_usd,
            self.engine.analyze_portfolio_irs_exposure(with_equity).net_portfolio_dv01_usd,
        )

    def test_audit_notes_state_the_hedge_side(self):
        portfolio = MultiAssetPortfolioRisk(-5_000.0, 0.0, [], hedge_instrument=self.hedge)
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)
        self.assertIn("PAY_FIXED", report.audit_notes)
        self.assertIn("signed P&L per +1 bps rise", report.audit_notes)


if __name__ == '__main__':
    unittest.main()
