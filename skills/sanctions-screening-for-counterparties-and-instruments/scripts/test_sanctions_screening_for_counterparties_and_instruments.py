import unittest
from sanctions_screening_for_counterparties_and_instruments import (
    ComplianceResult, SanctionsScreeningForCounterpartiesAndInstrumentsEngine,
    ScreeningSubject, ScreeningEntityKind, SanctionsListType, SanctionedEntry
)

class TestSanctionsScreeningLegacy(unittest.TestCase):

    def setUp(self):
        self.engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine()

    def test_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.reason, "Valid")

    def test_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)

    def test_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

class TestSanctionsScreeningEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(fuzzy_match_threshold_pct=85.0)

    def test_cleared_counterparty(self):
        subject = ScreeningSubject(
            subject_id="LEI_CLEARED_001",
            name="APPLE INC",
            country_iso="US",
            entity_kind=ScreeningEntityKind.COUNTERPARTY
        )
        report = self.engine.screen_subject(subject)
        self.assertTrue(report.is_cleared)
        self.assertEqual(report.status, "CLEARED")
        self.assertFalse(report.has_sanctions_hit)
        self.assertFalse(report.has_embargo_violation)

    def test_ofac_sdn_fuzzy_match(self):
        # Transliterated / fuzzy variant of VTB BANK PJSC -> VTB BANK P.J.S.C.
        subject = ScreeningSubject(
            subject_id="LEI_TEST_999",
            name="VTB BANK PJSC",
            country_iso="RU",
            entity_kind=ScreeningEntityKind.COUNTERPARTY
        )
        report = self.engine.screen_subject(subject)
        self.assertFalse(report.is_cleared)
        self.assertTrue(report.has_sanctions_hit)
        self.assertEqual(report.status, "BLOCKED_SANCTIONS_HIT")
        self.assertGreater(len(report.hits), 0)

    def test_ofac_50_percent_ownership_rule(self):
        subject = ScreeningSubject(
            subject_id="SUBSIDIARY_001",
            name="UNLISTED SUBSIDIARY LLC",
            country_iso="DE",
            entity_kind=ScreeningEntityKind.COUNTERPARTY,
            ownership_pct_by_sanctioned=51.0  # > 50% owned by sanctioned entity
        )
        report = self.engine.screen_subject(subject)
        self.assertFalse(report.is_cleared)
        self.assertTrue(report.has_sanctions_hit)
        self.assertIn("OFAC 50% Rule", report.hits[0].reason)

    def test_embargoed_country_blocked(self):
        subject = ScreeningSubject(
            subject_id="NORTH_KOREA_ENTITY",
            name="PYONGYANG COMMERCIAL AG",
            country_iso="KP",  # North Korea
            entity_kind=ScreeningEntityKind.COUNTERPARTY
        )
        report = self.engine.screen_subject(subject)
        self.assertFalse(report.is_cleared)
        self.assertTrue(report.has_embargo_violation)
        self.assertEqual(report.status, "BLOCKED_EMBARGO")

if __name__ == '__main__':
    unittest.main()
