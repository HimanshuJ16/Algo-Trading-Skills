import unittest
from unittest import mock

from vat_gst_treatment_of_trading_related_services import (
    Jurisdiction,
    PartialExemptionRounding,
    ServiceCategory,
    TradingExpenseInvoice,
    VATEngineError,
    VATGSTTradingServicesEngine,
    VATTreatment,
)


def make_invoice(**overrides) -> TradingExpenseInvoice:
    """Build an invoice from a domestic UK co-location default."""
    kwargs = dict(
        invoice_id="INV-000",
        vendor_name="Test Vendor",
        vendor_jurisdiction=Jurisdiction.UK_HMRC,
        entity_jurisdiction=Jurisdiction.UK_HMRC,
        service_category=ServiceCategory.COLOCATION_DATA_FEED,
        net_amount_usd=50_000.0,
    )
    kwargs.update(overrides)
    return TradingExpenseInvoice(**kwargs)


class TestVATGSTTradingServicesEngine(unittest.TestCase):
    def setUp(self):
        # Initialize engine with 20% partial exemption recovery ratio
        self.engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=20.0)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def test_exempt_financial_service_exchange_fee(self):
        inv = make_invoice(
            invoice_id="INV-001",
            vendor_name="London Stock Exchange",
            service_category=ServiceCategory.EXCHANGE_EXECUTION_FEE,
            net_amount_usd=100_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.EXEMPT)
        self.assertEqual(ass.vat_rate_pct, 0.0)
        self.assertEqual(ass.input_vat_paid_usd, 0.0)
        self.assertEqual(ass.output_vat_usd, 0.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 0.0)

    def test_exchange_execution_fee_warns_about_bundled_standard_rated_charges(self):
        """HMRC VAT Notice 701/49 para 6.9: exchange admission/membership charges
        are standard-rated even though the intermediation element is exempt."""
        ass = self.engine.assess_invoice_tax(
            make_invoice(service_category=ServiceCategory.EXCHANGE_EXECUTION_FEE)
        )
        self.assertTrue(any("701/49" in w for w in ass.warnings))

    def test_exchange_membership_fee_is_standard_rated_not_exempt(self):
        ass = self.engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-MEMB",
                service_category=ServiceCategory.EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE,
                net_amount_usd=10_000.0,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.STANDARD_RATED)
        self.assertEqual(ass.vat_rate_pct, 20.0)
        self.assertAlmostEqual(ass.input_vat_paid_usd, 2_000.0, places=6)

    def test_clearing_fee_from_foreign_vendor_stays_exempt_not_reverse_charged(self):
        """An exempt supply received from abroad carries no reverse charge."""
        ass = self.engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-CLR",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                service_category=ServiceCategory.CLEARING_FEE,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.EXEMPT)
        self.assertEqual(ass.reverse_charge_vat_usd, 0.0)

    def test_domestic_standard_rated_colocation(self):
        inv = make_invoice(invoice_id="INV-002", vendor_name="Equinix UK LD4")
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.STANDARD_RATED)
        self.assertEqual(ass.vat_rate_pct, 20.0)
        self.assertEqual(ass.input_vat_paid_usd, 10_000.0)  # 20% of 50k
        # 20% recovery ratio -> $2,000 recoverable, $8,000 unrecoverable expense
        self.assertEqual(ass.recoverable_input_vat_usd, 2_000.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 8_000.0)
        self.assertEqual(ass.output_vat_usd, 0.0)
        self.assertEqual(ass.warnings, ())

    # ------------------------------------------------------------------
    # Reverse charge
    # ------------------------------------------------------------------

    def test_cross_border_reverse_charge_mechanism(self):
        # US software vendor invoicing UK trading entity
        inv = make_invoice(
            invoice_id="INV-003",
            vendor_name="Bloomberg LP US",
            vendor_jurisdiction=Jurisdiction.US_NON_VAT,
            service_category=ServiceCategory.SOFTWARE_LICENSE,
            net_amount_usd=200_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.REVERSE_CHARGE)
        self.assertEqual(ass.reverse_charge_vat_usd, 40_000.0)  # 20% UK RCM
        # 20% recovery ratio -> $8,000 recoverable, $32,000 unrecoverable expense
        self.assertEqual(ass.recoverable_input_vat_usd, 8_000.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 32_000.0)

    def test_reverse_charge_declares_self_assessed_output_vat(self):
        """Regression: output VAT (UK return Box 1) used to be reported as zero,
        so the return summary omitted the self-assessed output tax leg."""
        ass = self.engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-RCM-OUT",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                service_category=ServiceCategory.SOFTWARE_LICENSE,
                net_amount_usd=200_000.0,
            )
        )
        self.assertEqual(ass.output_vat_usd, 40_000.0)
        self.assertEqual(ass.output_vat_usd, ass.reverse_charge_vat_usd)
        self.assertEqual(ass.input_vat_paid_usd, 0.0)  # supplier charges no VAT

    def test_reverse_charge_uses_recipient_rate_not_vendor_rate(self):
        """Irish vendor (23%) to a German entity (19%): the B2B general rule
        taxes the supply where the recipient belongs."""
        engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=25.0)
        ass = engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-DE",
                vendor_jurisdiction=Jurisdiction.EU_IRELAND,
                entity_jurisdiction=Jurisdiction.EU_GERMANY,
                service_category=ServiceCategory.SOFTWARE_LICENSE,
                net_amount_usd=100_000.0,
            )
        )
        self.assertEqual(ass.vat_rate_pct, 19.0)
        self.assertAlmostEqual(ass.reverse_charge_vat_usd, 19_000.0, places=6)
        self.assertAlmostEqual(ass.recoverable_input_vat_usd, 4_750.0, places=6)
        self.assertAlmostEqual(ass.unrecoverable_vat_expense_usd, 14_250.0, places=6)

    def test_cross_border_colocation_warns_about_immovable_property_risk(self):
        """CJEU C-215/19 A Oy: exclusive use of a defined space can make
        co-location a land-related supply taxed where the data centre sits."""
        ass = self.engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-COLO-XB",
                vendor_jurisdiction=Jurisdiction.EU_GERMANY,
                service_category=ServiceCategory.COLOCATION_DATA_FEED,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.REVERSE_CHARGE)
        self.assertTrue(any("C-215/19" in w for w in ass.warnings))

    def test_singapore_reverse_charge_skipped_when_fully_recoverable(self):
        """IRAS reverse charge for imported services applies only to a recipient
        that is not entitled to a full input tax credit."""
        engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=100.0)
        ass = engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-SG-FULL",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                entity_jurisdiction=Jurisdiction.SINGAPORE_IRAS,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.OUT_OF_SCOPE)
        self.assertEqual(ass.reverse_charge_vat_usd, 0.0)
        self.assertTrue(any("SINGAPORE_IRAS" in w for w in ass.warnings))

    def test_australia_reverse_charge_applies_when_partially_exempt(self):
        """GST Act Div 84 bites where the acquisition is not solely for a
        creditable purpose. 40% recovery -> 10% GST on 100k = 10,000."""
        engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=40.0)
        ass = engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-AU",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                entity_jurisdiction=Jurisdiction.AUSTRALIA_ATO,
                net_amount_usd=100_000.0,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.REVERSE_CHARGE)
        self.assertAlmostEqual(ass.reverse_charge_vat_usd, 10_000.0, places=6)
        self.assertAlmostEqual(ass.recoverable_input_vat_usd, 4_000.0, places=6)

    def test_uk_reverse_charge_applies_even_at_full_recovery(self):
        """VATA 1994 s.8 is not conditional on the recipient's recovery position."""
        engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=100.0)
        ass = engine.assess_invoice_tax(
            make_invoice(
                invoice_id="INV-UK-FULL",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                service_category=ServiceCategory.SOFTWARE_LICENSE,
                net_amount_usd=100_000.0,
            )
        )
        self.assertEqual(ass.vat_treatment, VATTreatment.REVERSE_CHARGE)
        self.assertAlmostEqual(ass.recoverable_input_vat_usd, 20_000.0, places=6)
        self.assertAlmostEqual(ass.unrecoverable_vat_expense_usd, 0.0, places=6)

    def test_us_entity_out_of_scope(self):
        inv = make_invoice(
            invoice_id="INV-004",
            vendor_name="CME Group US",
            vendor_jurisdiction=Jurisdiction.US_NON_VAT,
            entity_jurisdiction=Jurisdiction.US_NON_VAT,
            net_amount_usd=30_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.OUT_OF_SCOPE)
        self.assertEqual(ass.input_vat_paid_usd, 0.0)
        self.assertTrue(any("sales and use tax" in w for w in ass.warnings))

    # ------------------------------------------------------------------
    # Partial exemption ratio
    # ------------------------------------------------------------------

    def test_partial_exemption_recovery_ratio_calculation(self):
        # Taxable supplies $200k, Exempt supplies $800k -> 20% recovery ratio
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=200_000.0, exempt_supplies_usd=800_000.0
        )
        self.assertEqual(ratio, 20.0)
        self.assertEqual(self.engine.recovery_ratio_pct, 20.0)

    def test_rounding_up_to_whole_percent(self):
        """UK VAT Regs 1995 reg 101(4) / EU Directive Art 175(1): the pro-rata
        percentage is rounded UP to the next whole number. 20.4% -> 21%."""
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=204_000.0,
            exempt_supplies_usd=796_000.0,
            rounding=PartialExemptionRounding.UP_WHOLE_PERCENT,
        )
        self.assertEqual(ratio, 21.0)

    def test_rounding_up_does_not_inflate_an_exact_whole_percentage(self):
        """An exact 20.0% must stay 20.0%, not gain a point to float residue."""
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=200_000.0,
            exempt_supplies_usd=800_000.0,
            rounding=PartialExemptionRounding.UP_WHOLE_PERCENT,
        )
        self.assertEqual(ratio, 20.0)

    def test_rounding_up_to_two_decimals(self):
        """UK reg 101(5) where residual input tax exceeds GBP 400k/month:
        1/7 = 14.2857...% -> 14.29%."""
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=1_000_000.0,
            exempt_supplies_usd=6_000_000.0,
            rounding=PartialExemptionRounding.UP_TWO_DECIMALS,
        )
        self.assertEqual(ratio, 14.29)

    def test_rounding_up_is_capped_at_100_percent(self):
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=999_999.0,
            exempt_supplies_usd=1.0,
            rounding=PartialExemptionRounding.UP_WHOLE_PERCENT,
        )
        self.assertEqual(ratio, 100.0)

    def test_negative_supplies_rejected_rather_than_storing_a_negative_ratio(self):
        """Regression: the setter used to bypass the 0-100% check, so negative
        taxable supplies stored a negative ratio and produced negative
        recoverable input VAT."""
        with self.assertRaises(VATEngineError):
            self.engine.set_partial_exemption_ratio(
                taxable_supplies_usd=-100_000.0, exempt_supplies_usd=200_000.0
            )
        self.assertEqual(self.engine.recovery_ratio_pct, 20.0)  # unchanged

    def test_zero_total_supplies_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.set_partial_exemption_ratio(
                taxable_supplies_usd=0.0, exempt_supplies_usd=0.0
            )

    def test_non_finite_supplies_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.set_partial_exemption_ratio(
                taxable_supplies_usd=float("nan"), exempt_supplies_usd=100.0
            )

    def test_invalid_rounding_argument_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.set_partial_exemption_ratio(
                taxable_supplies_usd=1.0,
                exempt_supplies_usd=1.0,
                rounding="UP_WHOLE_PERCENT",
            )

    # ------------------------------------------------------------------
    # Fail-closed rate lookup
    # ------------------------------------------------------------------

    def test_unmapped_jurisdiction_raises_instead_of_assessing_zero_percent(self):
        """Regression: an unmapped jurisdiction used to fall back to a 0% rate,
        silently under-declaring a taxable supply."""
        rates = dict(VATGSTTradingServicesEngine.STANDARD_RATES)
        rates.pop(Jurisdiction.UK_HMRC)
        with mock.patch.object(VATGSTTradingServicesEngine, "STANDARD_RATES", rates):
            with self.assertRaises(VATEngineError) as ctx:
                self.engine.assess_invoice_tax(make_invoice(invoice_id="INV-NORATE"))
        self.assertIn("INV-NORATE", str(ctx.exception))

    def test_non_enum_jurisdiction_raises(self):
        """A raw string used to compare unequal to every enum member, silently
        routing the invoice to a 0% reverse charge."""
        with self.assertRaises(VATEngineError):
            self.engine.assess_invoice_tax(
                make_invoice(invoice_id="INV-STR", entity_jurisdiction="UK_HMRC")
            )

    def test_non_enum_service_category_raises(self):
        with self.assertRaises(VATEngineError):
            self.engine.assess_invoice_tax(
                make_invoice(invoice_id="INV-CAT", service_category="SOFTWARE_LICENSE")
            )

    # ------------------------------------------------------------------
    # Return summary
    # ------------------------------------------------------------------

    def test_vat_return_summary_generation(self):
        invoices = [
            make_invoice(
                invoice_id="INV-A",
                vendor_name="LSE",
                service_category=ServiceCategory.EXCHANGE_EXECUTION_FEE,
                net_amount_usd=100_000.0,
            ),
            make_invoice(invoice_id="INV-B", vendor_name="Equinix UK"),
        ]
        summary = self.engine.generate_vat_return_summary(invoices)

        self.assertEqual(summary.total_net_expense_usd, 150_000.0)
        self.assertEqual(summary.total_input_vat_paid_usd, 10_000.0)
        self.assertEqual(summary.total_recoverable_input_vat_usd, 2_000.0)
        self.assertEqual(summary.total_unrecoverable_vat_expense_usd, 8_000.0)

    def test_summary_reports_output_vat_and_net_payable(self):
        """Domestic 50k @20% (2,000 recoverable) plus a 200k US software import
        reverse-charged at 20% (40,000 output, 8,000 recoverable) leaves
        40,000 - 10,000 = 30,000 payable."""
        invoices = [
            make_invoice(invoice_id="INV-DOM"),
            make_invoice(
                invoice_id="INV-IMP",
                vendor_jurisdiction=Jurisdiction.US_NON_VAT,
                service_category=ServiceCategory.SOFTWARE_LICENSE,
                net_amount_usd=200_000.0,
            ),
        ]
        summary = self.engine.generate_vat_return_summary(invoices)

        self.assertEqual(summary.total_output_vat_usd, 40_000.0)
        self.assertEqual(summary.total_reverse_charge_vat_usd, 40_000.0)
        self.assertEqual(summary.total_recoverable_input_vat_usd, 10_000.0)
        self.assertEqual(summary.net_vat_payable_usd, 30_000.0)

    def test_summary_retains_per_invoice_assessments_in_order(self):
        invoices = [
            make_invoice(invoice_id="INV-1"),
            make_invoice(invoice_id="INV-2", net_amount_usd=1_000.0),
        ]
        summary = self.engine.generate_vat_return_summary(invoices)
        self.assertEqual([a.invoice_id for a in summary.assessments], ["INV-1", "INV-2"])

    def test_summary_rejects_a_non_iterable(self):
        with self.assertRaises(VATEngineError):
            self.engine.generate_vat_return_summary(make_invoice())

    def test_summary_accepts_a_generator(self):
        summary = self.engine.generate_vat_return_summary(
            make_invoice(invoice_id=f"INV-{i}") for i in range(3)
        )
        self.assertEqual(len(summary.assessments), 3)
        self.assertEqual(summary.total_net_expense_usd, 150_000.0)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_ratio_above_100_rejected_at_construction(self):
        with self.assertRaises(VATEngineError):
            VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=150.0)

    def test_negative_invoice_amount_rejected_with_invoice_id_in_message(self):
        inv = make_invoice(invoice_id="INV-ERR", net_amount_usd=-500.0)
        with self.assertRaises(VATEngineError) as ctx:
            self.engine.assess_invoice_tax(inv)
        self.assertIn("INV-ERR", str(ctx.exception))

    def test_zero_invoice_amount_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.assess_invoice_tax(make_invoice(net_amount_usd=0.0))

    def test_non_finite_invoice_amount_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.assess_invoice_tax(make_invoice(net_amount_usd=float("inf")))

    def test_blank_invoice_id_rejected(self):
        with self.assertRaises(VATEngineError):
            self.engine.assess_invoice_tax(make_invoice(invoice_id="   "))


if __name__ == "__main__":
    unittest.main()
