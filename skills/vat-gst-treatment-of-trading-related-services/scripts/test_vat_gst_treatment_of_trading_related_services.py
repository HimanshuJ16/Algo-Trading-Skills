import unittest
import datetime
from vat_gst_treatment_of_trading_related_services import (
    ServiceCategory,
    VATTreatment,
    Jurisdiction,
    TradingExpenseInvoice,
    VATTaxAssessment,
    VATReturnSummary,
    VATGSTTradingServicesEngine,
    VATEngineError,
)


class TestVATGSTTradingServicesEngine(unittest.TestCase):
    def setUp(self):
        # Initialize engine with 20% partial exemption recovery ratio
        self.engine = VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=20.0)

    def test_exempt_financial_service_exchange_fee(self):
        inv = TradingExpenseInvoice(
            invoice_id="INV-001",
            vendor_name="London Stock Exchange",
            vendor_jurisdiction=Jurisdiction.UK_HMRC,
            entity_jurisdiction=Jurisdiction.UK_HMRC,
            service_category=ServiceCategory.EXCHANGE_EXECUTION_FEE,
            net_amount_usd=100_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.EXEMPT)
        self.assertEqual(ass.vat_rate_pct, 0.0)
        self.assertEqual(ass.input_vat_paid_usd, 0.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 0.0)

    def test_domestic_standard_rated_colocation(self):
        inv = TradingExpenseInvoice(
            invoice_id="INV-002",
            vendor_name="Equinix UK LD4",
            vendor_jurisdiction=Jurisdiction.UK_HMRC,
            entity_jurisdiction=Jurisdiction.UK_HMRC,
            service_category=ServiceCategory.COLOCATION_DATA_FEED,
            net_amount_usd=50_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.STANDARD_RATED)
        self.assertEqual(ass.vat_rate_pct, 20.0)
        self.assertEqual(ass.input_vat_paid_usd, 10_000.0)  # 20% of 50k
        # 20% recovery ratio -> $2,000 recoverable, $8,000 unrecoverable expense
        self.assertEqual(ass.recoverable_input_vat_usd, 2_000.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 8_000.0)

    def test_cross_border_reverse_charge_mechanism(self):
        # US software vendor invoicing UK trading entity
        inv = TradingExpenseInvoice(
            invoice_id="INV-003",
            vendor_name="Bloomberg LP US",
            vendor_jurisdiction=Jurisdiction.US_NON_VAT,
            entity_jurisdiction=Jurisdiction.UK_HMRC,
            service_category=ServiceCategory.SOFTWARE_LICENSE,
            net_amount_usd=200_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.REVERSE_CHARGE)
        self.assertEqual(ass.reverse_charge_vat_usd, 40_000.0)  # 20% UK RCM
        # 20% recovery ratio -> $8,000 recoverable, $32,000 unrecoverable expense
        self.assertEqual(ass.recoverable_input_vat_usd, 8_000.0)
        self.assertEqual(ass.unrecoverable_vat_expense_usd, 32_000.0)

    def test_partial_exemption_recovery_ratio_calculation(self):
        # Taxable supplies $200k, Exempt supplies $800k -> 20% recovery ratio
        ratio = self.engine.set_partial_exemption_ratio(
            taxable_supplies_usd=200_000.0, exempt_supplies_usd=800_000.0
        )
        self.assertEqual(ratio, 20.0)
        self.assertEqual(self.engine.recovery_ratio_pct, 20.0)

    def test_us_entity_out_of_scope(self):
        inv = TradingExpenseInvoice(
            invoice_id="INV-004",
            vendor_name="CME Group US",
            vendor_jurisdiction=Jurisdiction.US_NON_VAT,
            entity_jurisdiction=Jurisdiction.US_NON_VAT,
            service_category=ServiceCategory.COLOCATION_DATA_FEED,
            net_amount_usd=30_000.0,
        )
        ass = self.engine.assess_invoice_tax(inv)

        self.assertEqual(ass.vat_treatment, VATTreatment.OUT_OF_SCOPE)
        self.assertEqual(ass.input_vat_paid_usd, 0.0)

    def test_vat_return_summary_generation(self):
        invoices = [
            TradingExpenseInvoice(
                invoice_id="INV-A",
                vendor_name="LSE",
                vendor_jurisdiction=Jurisdiction.UK_HMRC,
                entity_jurisdiction=Jurisdiction.UK_HMRC,
                service_category=ServiceCategory.EXCHANGE_EXECUTION_FEE,
                net_amount_usd=100_000.0,
            ),
            TradingExpenseInvoice(
                invoice_id="INV-B",
                vendor_name="Equinix UK",
                vendor_jurisdiction=Jurisdiction.UK_HMRC,
                entity_jurisdiction=Jurisdiction.UK_HMRC,
                service_category=ServiceCategory.COLOCATION_DATA_FEED,
                net_amount_usd=50_000.0,
            ),
        ]
        summary = self.engine.generate_vat_return_summary(invoices)

        self.assertEqual(summary.total_net_expense_usd, 150_000.0)
        self.assertEqual(summary.total_input_vat_paid_usd, 10_000.0)
        self.assertEqual(summary.total_recoverable_input_vat_usd, 2_000.0)
        self.assertEqual(summary.total_unrecoverable_vat_expense_usd, 8_000.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(VATEngineError):
            VATGSTTradingServicesEngine(partial_exemption_recovery_ratio_pct=150.0)  # > 100%

        with self.assertRaises(VATEngineError):
            inv = TradingExpenseInvoice(
                invoice_id="INV-ERR",
                vendor_name="Test Vendor",
                vendor_jurisdiction=Jurisdiction.UK_HMRC,
                entity_jurisdiction=Jurisdiction.UK_HMRC,
                service_category=ServiceCategory.COLOCATION_DATA_FEED,
                net_amount_usd=-500.0,  # Negative net amount!
            )
            self.engine.assess_invoice_tax(inv)


if __name__ == "__main__":
    unittest.main()
