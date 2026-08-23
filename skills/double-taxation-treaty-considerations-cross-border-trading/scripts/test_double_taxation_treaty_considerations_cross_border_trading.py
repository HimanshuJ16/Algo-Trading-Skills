import unittest

from double_taxation_treaty_considerations_cross_border_trading import (
    STATUS_DOCUMENTATION_MISSING,
    STATUS_REVIEW_REQUIRED,
    STATUS_STATUTORY_NO_TREATY,
    STATUS_TREATY_APPLIED,
    CrossBorderIncomePayment,
    DoubleTaxationTreatyEngine,
    DttTreatySpec,
)

# US-UK treaty: portfolio dividends 15% (Art. 10), US statutory FDAP 30%.
UK_US_DIVIDEND = DttTreatySpec(
    residence_country="UK",
    source_country="US",
    statutory_wht_pct=0.30,
    treaty_wht_pct=0.15,
    required_documentation="Form W-8BEN-E",
)
# US-UK treaty: interest is generally exempt at source (Art. 11).
UK_US_INTEREST = DttTreatySpec(
    residence_country="UK",
    source_country="US",
    statutory_wht_pct=0.30,
    treaty_wht_pct=0.00,
    required_documentation="Form W-8BEN-E",
    income_type="INTEREST",
)


def dividend_payment(**overrides):
    kwargs = dict(
        payment_id="PMT_01",
        residence_country="UK",
        source_country="US",
        income_type="EQUITY_DIVIDEND",
        gross_income_usd=100_000.0,
        has_valid_tax_documentation=True,
        resident_country_effective_tax_rate=0.25,
    )
    kwargs.update(overrides)
    return CrossBorderIncomePayment(**kwargs)


class TestTreatyApplication(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        self.engine.register_treaty(UK_US_DIVIDEND)

    def test_dtt_treaty_reduces_wht_and_saves_tax_leakage(self):
        report = self.engine.evaluate_cross_border_payment(dividend_payment())

        self.assertEqual(report.status, STATUS_TREATY_APPLIED)
        self.assertEqual(report.applied_wht_pct, 0.15)
        self.assertEqual(report.wht_tax_paid_usd, 15_000.0)
        self.assertEqual(report.statutory_wht_usd, 30_000.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 15_000.0)
        # Credit ceiling 100k * 25% = 25,000; creditable 15,000 is the binding figure.
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 15_000.0)
        self.assertEqual(report.non_creditable_wht_usd, 0.0)

    def test_missing_documentation_falls_back_to_statutory_wht(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(payment_id="PMT_02", has_valid_tax_documentation=False)
        )

        self.assertEqual(report.status, STATUS_DOCUMENTATION_MISSING)
        self.assertEqual(report.applied_wht_pct, 0.30)
        self.assertEqual(report.wht_tax_paid_usd, 30_000.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 0.0)
        self.assertIn("Missing Form W-8BEN-E", report.applied_treaty_notes)

    def test_over_withheld_tax_above_an_available_treaty_rate_is_not_creditable(self):
        """Tax withheld above a treaty rate that was available is a noncompulsory
        payment: the credit is figured at the treaty rate regardless of what was
        withheld. Crediting the full 30% overstates relief by $15,000."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(payment_id="PMT_03", has_valid_tax_documentation=False)
        )

        self.assertEqual(report.wht_tax_paid_usd, 30_000.0)
        # Creditable is capped at the 15% treaty rate, not the 25% ceiling.
        self.assertEqual(report.creditable_foreign_tax_usd, 15_000.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 15_000.0)
        # 30,000 withheld - 15,000 credited = 15,000 permanently lost.
        self.assertEqual(report.non_creditable_wht_usd, 15_000.0)
        self.assertIn("refund claim", report.required_action)

    def test_full_credit_is_available_when_the_residence_rule_permits_it(self):
        engine = DoubleTaxationTreatyEngine(limit_credit_to_treaty_rate=False)
        engine.register_treaty(UK_US_DIVIDEND)
        report = engine.evaluate_cross_border_payment(
            dividend_payment(has_valid_tax_documentation=False)
        )
        self.assertEqual(report.creditable_foreign_tax_usd, 30_000.0)
        # Still bounded by the 25,000 residence-country ceiling.
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 25_000.0)
        self.assertEqual(report.non_creditable_wht_usd, 5_000.0)


class TestIncomeTypeSeparation(unittest.TestCase):
    """Treaties rate dividends, interest and royalties under separate articles."""

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        self.engine.register_treaty(UK_US_DIVIDEND)
        self.engine.register_treaty(UK_US_INTEREST)

    def test_interest_uses_its_own_treaty_article_not_the_dividend_rate(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(payment_id="PMT_INT", income_type="INTEREST")
        )
        self.assertEqual(report.applied_wht_pct, 0.00)
        self.assertEqual(report.wht_tax_paid_usd, 0.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 30_000.0)

    def test_dividend_rate_is_not_borrowed_for_an_unregistered_income_type(self):
        """A registered dividend rate must not silently price a swap."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(payment_id="PMT_SWAP", income_type="SECTION_871M_SWAP")
        )
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertIsNone(report.applied_wht_pct)
        self.assertIsNone(report.wht_tax_paid_usd)

    def test_871m_swap_rate_is_used_once_registered(self):
        self.engine.register_statutory_rate("US", "SECTION_871M_SWAP", 0.30)
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(payment_id="PMT_SWAP2", income_type="SECTION_871M_SWAP")
        )
        self.assertEqual(report.status, STATUS_STATUTORY_NO_TREATY)
        self.assertEqual(report.wht_tax_paid_usd, 30_000.0)


class TestNoInventedRates(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()

    def test_unregistered_pair_does_not_assume_thirty_percent(self):
        """30% is the US FDAP rate, not a universal statutory rate: the UK
        levies no withholding tax on dividends paid to non-residents."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(residence_country="US", source_country="UK")
        )
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertIsNone(report.applied_wht_pct)
        self.assertIsNone(report.statutory_wht_usd)
        self.assertIsNone(report.eligible_foreign_tax_credit_usd)

    def test_registered_zero_statutory_rate_is_honoured(self):
        self.engine.register_statutory_rate("UK", "EQUITY_DIVIDEND", 0.00)
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(residence_country="US", source_country="UK")
        )
        self.assertEqual(report.status, STATUS_STATUTORY_NO_TREATY)
        self.assertEqual(report.wht_tax_paid_usd, 0.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 0.0)


class TestForeignTaxCreditCeiling(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        self.engine.register_treaty(UK_US_DIVIDEND)

    def test_zero_tax_residence_jurisdiction_gets_no_credit(self):
        """A Cayman entity has no residence-country liability to credit against,
        so the entire 15% withheld is unrecoverable leakage."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(residence_country="KY", resident_country_effective_tax_rate=0.0)
        )
        # No KY-US treaty registered, and no statutory rate either.
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)

        self.engine.register_statutory_rate("US", "EQUITY_DIVIDEND", 0.30)
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(residence_country="KY", resident_country_effective_tax_rate=0.0)
        )
        self.assertEqual(report.wht_tax_paid_usd, 30_000.0)
        self.assertEqual(report.ftc_limitation_usd, 0.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 0.0)
        self.assertEqual(report.non_creditable_wht_usd, 30_000.0)

    def test_unknown_residence_rate_suppresses_the_credit_figure(self):
        """The old default of 20% invented a credit ceiling for any caller who
        omitted the rate."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(resident_country_effective_tax_rate=None)
        )
        self.assertIsNone(report.ftc_limitation_usd)
        self.assertIsNone(report.eligible_foreign_tax_credit_usd)
        self.assertIsNone(report.non_creditable_wht_usd)
        self.assertIn("none is assumed", report.required_action)

    def test_explicit_limitation_overrides_the_gross_approximation(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(resident_country_effective_tax_rate=0.25,
                             ftc_limitation_usd=4_000.0)
        )
        self.assertEqual(report.ftc_limitation_usd, 4_000.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 4_000.0)
        self.assertEqual(report.non_creditable_wht_usd, 11_000.0)

    def test_credit_is_bounded_by_the_lower_of_creditable_tax_and_ceiling(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(resident_country_effective_tax_rate=0.10)
        )
        # 15,000 withheld vs a 10,000 ceiling -> 10,000 credited, 5,000 lost.
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 10_000.0)
        self.assertEqual(report.non_creditable_wht_usd, 5_000.0)


class TestRoundingAndArithmetic(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        self.engine.register_treaty(UK_US_DIVIDEND)

    def test_exact_half_cent_rounds_half_up(self):
        """1.50 * 0.15 is exactly 0.225. Half-even rounding gives 0.22, and
        rounding the binary float product (0.22499999999999998) gives 0.22 even
        under half-up -- only exact decimal multiplication yields 0.23."""
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(gross_income_usd=1.50)
        )
        self.assertEqual(report.wht_tax_paid_usd, 0.23)

    def test_sub_half_cent_still_rounds_down(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(gross_income_usd=0.83333333)
        )
        # 0.83333333 * 0.15 = 0.1249999995 -> 0.12
        self.assertEqual(report.wht_tax_paid_usd, 0.12)

    def test_large_notional_is_exact_to_the_cent(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(gross_income_usd=1_234_567.89)
        )
        # 1,234,567.89 * 0.15 = 185,185.1835 -> 185,185.18
        self.assertEqual(report.wht_tax_paid_usd, 185_185.18)
        # 1,234,567.89 * 0.30 = 370,370.367 -> 370,370.37
        self.assertEqual(report.statutory_wht_usd, 370_370.37)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 185_185.19)

    def test_zero_gross_income_is_handled(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(gross_income_usd=0.0)
        )
        self.assertEqual(report.wht_tax_paid_usd, 0.0)
        self.assertEqual(report.wht_tax_leakage_saved_usd, 0.0)
        self.assertEqual(report.eligible_foreign_tax_credit_usd, 0.0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = DoubleTaxationTreatyEngine()
        self.engine.register_treaty(UK_US_DIVIDEND)

    def test_treaty_rate_above_statutory_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_treaty(DttTreatySpec(
                residence_country="UK", source_country="US",
                statutory_wht_pct=0.10, treaty_wht_pct=0.15,
                required_documentation="Form W-8BEN-E",
            ))

    def test_percentage_style_rates_are_rejected(self):
        """15 is not 15%; a rate outside [0,1] would produce a 1500% withholding."""
        with self.assertRaises(ValueError):
            self.engine.register_treaty(DttTreatySpec(
                residence_country="UK", source_country="US",
                statutory_wht_pct=30.0, treaty_wht_pct=15.0,
                required_documentation="Form W-8BEN-E",
            ))

    def test_unknown_income_type_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_cross_border_payment(
                dividend_payment(income_type="CAPITAL_GAIN")
            )

    def test_negative_gross_income_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_cross_border_payment(
                dividend_payment(gross_income_usd=-100.0)
            )

    def test_non_finite_amounts_rejected_at_the_boundary(self):
        """NaN/Inf would otherwise surface as decimal.InvalidOperation from
        inside the money helpers."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_cross_border_payment(
                        dividend_payment(gross_income_usd=bad)
                    )
        with self.assertRaises(ValueError):
            self.engine.evaluate_cross_border_payment(
                dividend_payment(ftc_limitation_usd=float("inf"))
            )

    def test_blank_identifiers_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_cross_border_payment(dividend_payment(payment_id="  "))
        with self.assertRaises(ValueError):
            self.engine.evaluate_cross_border_payment(dividend_payment(residence_country=""))

    def test_wrong_types_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_cross_border_payment({"payment_id": "X"})
        with self.assertRaises(TypeError):
            self.engine.evaluate_cross_border_payment(
                dividend_payment(has_valid_tax_documentation="yes")
            )
        with self.assertRaises(TypeError):
            self.engine.register_treaty("not-a-spec")

    def test_country_codes_are_normalized(self):
        report = self.engine.evaluate_cross_border_payment(
            dividend_payment(residence_country=" uk ", source_country="us")
        )
        self.assertEqual(report.status, STATUS_TREATY_APPLIED)
        self.assertEqual(report.residence_country, "UK")
        self.assertEqual(report.source_country, "US")


if __name__ == '__main__':
    unittest.main()
