import unittest
from alternative_data_vendor_due_diligence_checklist import (
    VendorDueDiligenceEvaluator,
    VendorDueDiligenceQuestionnaire
)

class TestVendorDueDiligenceEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = VendorDueDiligenceEvaluator()

    def test_clean_vendor_approved(self):
        # A perfectly compliant weather data vendor
        ddq = VendorDueDiligenceQuestionnaire(
            vendor_name="CleanWeather API",
            dataset_name="Global Temps",
            has_resell_rights=True,
            is_web_scraped=False,
            scrapes_behind_login=False,
            bypasses_captchas=False,
            contains_pii=False,
            is_gdpr_ccpa_compliant=True,
            has_robust_anonymization=True,
            is_material_non_public_information=False
        )
        report = self.evaluator.evaluate(ddq)
        self.assertTrue(report.is_approved)
        self.assertEqual(len(report.critical_flags), 0)

    def test_mnpi_rejection(self):
        # Vendor offering leaked corporate documents
        ddq = VendorDueDiligenceQuestionnaire(
            vendor_name="ShadyData",
            dataset_name="Board Minutes",
            has_resell_rights=True,
            is_web_scraped=False,
            scrapes_behind_login=False,
            bypasses_captchas=False,
            contains_pii=False,
            is_gdpr_ccpa_compliant=True,
            has_robust_anonymization=True,
            is_material_non_public_information=True # ILLEGAL
        )
        report = self.evaluator.evaluate(ddq)
        self.assertFalse(report.is_approved)
        self.assertIn("Material Non-Public Information", report.critical_flags[0])

    def test_cfaa_scraping_rejection(self):
        # Vendor scraping behind LinkedIn login without permission
        ddq = VendorDueDiligenceQuestionnaire(
            vendor_name="ScrapeHub",
            dataset_name="Employee Headcounts",
            has_resell_rights=True,
            is_web_scraped=True,
            scrapes_behind_login=True, # ILLEGAL / HIGH RISK
            bypasses_captchas=True,
            contains_pii=False,
            is_gdpr_ccpa_compliant=True,
            has_robust_anonymization=True,
            is_material_non_public_information=False
        )
        report = self.evaluator.evaluate(ddq)
        self.assertFalse(report.is_approved)
        self.assertIn("authenticated logins", report.critical_flags[0])
        self.assertIn("bypasses CAPTCHAs", report.warnings[0])

    def test_pii_without_gdpr_rejection(self):
        # Vendor selling credit card logs without anonymization
        ddq = VendorDueDiligenceQuestionnaire(
            vendor_name="CardLogs",
            dataset_name="Consumer Spend",
            has_resell_rights=True,
            is_web_scraped=False,
            scrapes_behind_login=False,
            bypasses_captchas=False,
            contains_pii=True, # PII present
            is_gdpr_ccpa_compliant=False, # BAD
            has_robust_anonymization=False, # BAD
            is_material_non_public_information=False
        )
        report = self.evaluator.evaluate(ddq)
        self.assertFalse(report.is_approved)
        self.assertEqual(len(report.critical_flags), 2)

if __name__ == '__main__':
    unittest.main()
