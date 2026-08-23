import unittest
from cross_jurisdiction_regulatory_conflict_resolution import (
    OBLIGATION_LEI_TAGGING,
    OBLIGATION_NATIONAL_CLIENT_ID,
    OBLIGATION_SHORT_PRICE_TEST,
    OBLIGATION_SHORT_REPORTING,
    CrossJurisdictionRegulatoryConflictEngine,
    JurisdictionRules,
    ShortSellingRestriction,
    TradeOrderRequest,
    is_valid_lei,
)

# Structurally valid ISO 17442 codes: 20 upper-case alphanumerics whose
# MOD 97-10 check digits verify. Used as ground truth for the checksum tests.
VALID_LEI = "549300E9PC51EN656011"
VALID_LEI_ALT = "213800WAVVOPS85N2205"
# Fixture used by this skill's original test suite. It is 20 characters but its
# check digits do NOT verify (MOD 97 remainder 4, not 1), i.e. it is not a
# well-formed LEI. The previous length-only check accepted it.
LENGTH_VALID_BUT_BAD_CHECKSUM_LEI = "5493001KJTIIGC8Y1S12"


def make_order(**overrides):
    base = dict(
        order_id="ORD_TEST", entity_jurisdiction="US_SEC", venue_jurisdiction="EU_MIFID_II",
        symbol="SAP", quantity=500.0, price=180.0, is_short=False,
        routed_via_pfof=False, lei_tag=VALID_LEI,
    )
    base.update(overrides)
    return TradeOrderRequest(**base)


class TestCrossJurisdictionRegulatoryConflictEngine(unittest.TestCase):

    def setUp(self):
        # US: PFOF permitted (SEC Rule 606 disclosure regime), Reg SHO Rule 201
        # price test. EU: PFOF banned (MiFIR Art. 39a), LEI mandatory (MiFIR
        # Art. 26), SSR net short position reporting. KR: illustrative total ban.
        self.engine = CrossJurisdictionRegulatoryConflictEngine([
            JurisdictionRules("US_SEC", is_pfof_allowed=True, is_lei_mandatory=False,
                              short_selling_restriction_level=ShortSellingRestriction.PRICE_TEST),
            JurisdictionRules("EU_MIFID_II", is_pfof_allowed=False, is_lei_mandatory=True,
                              short_selling_restriction_level=ShortSellingRestriction.REPORTING),
            JurisdictionRules("KR_FSC", is_pfof_allowed=False, is_lei_mandatory=True,
                              short_selling_restriction_level=ShortSellingRestriction.BAN),
        ])

    # --- Strictest Rule Primacy resolution -------------------------------

    def test_strictest_rule_resolution_for_us_to_eu_trade(self):
        # PFOF: US (True) vs EU (False) -> strictest = False
        # LEI:  US (False) vs EU (True) -> strictest = True
        pfof_allowed, lei_mandatory, short_lvl = self.engine.resolve_strictest_rules(
            {"US_SEC", "EU_MIFID_II"}
        )
        self.assertFalse(pfof_allowed)
        self.assertTrue(lei_mandatory)
        self.assertEqual(short_lvl, int(ShortSellingRestriction.PRICE_TEST))

    def test_price_test_outranks_disclosure_only_reporting(self):
        # Regression: the previous encoding ranked NET_SHORT_REPORTING (2) above
        # UPTICK (1), so a US price test combined with EU reporting resolved to
        # "reporting" and dropped the price-test obligation entirely.
        _, _, short_lvl = self.engine.resolve_strictest_rules({"US_SEC", "EU_MIFID_II"})
        self.assertGreater(
            short_lvl, int(ShortSellingRestriction.REPORTING),
            "A price-test regime must outrank a disclosure-only reporting regime",
        )
        decision = self.engine.evaluate_order(make_order(is_short=True))
        self.assertTrue(decision.is_approved)
        self.assertIn(OBLIGATION_SHORT_PRICE_TEST, decision.required_obligations)

    def test_reporting_only_regime_surfaces_reporting_obligation(self):
        engine = CrossJurisdictionRegulatoryConflictEngine([
            JurisdictionRules("EU_MIFID_II", is_pfof_allowed=False, is_lei_mandatory=True,
                              short_selling_restriction_level=ShortSellingRestriction.REPORTING),
        ])
        decision = engine.evaluate_order(make_order(
            entity_jurisdiction="EU_MIFID_II", venue_jurisdiction="EU_MIFID_II", is_short=True
        ))
        self.assertTrue(decision.is_approved)
        self.assertIn(OBLIGATION_SHORT_REPORTING, decision.required_obligations)
        self.assertNotIn(OBLIGATION_SHORT_PRICE_TEST, decision.required_obligations)

    def test_empty_jurisdiction_set_raises_instead_of_resolving_permissively(self):
        # An empty set would otherwise return (PFOF allowed, no LEI, no restriction).
        with self.assertRaises(ValueError):
            self.engine.resolve_strictest_rules(set())

    def test_unregistered_jurisdiction_fails_closed_on_every_dimension(self):
        pfof_allowed, lei_mandatory, short_lvl = self.engine.resolve_strictest_rules(
            {"XX_UNKNOWN"}
        )
        self.assertFalse(pfof_allowed)
        self.assertTrue(lei_mandatory)
        self.assertEqual(short_lvl, int(ShortSellingRestriction.BAN))

        decision = self.engine.evaluate_order(make_order(
            venue_jurisdiction="XX_UNKNOWN", is_short=True
        ))
        self.assertFalse(decision.is_approved)
        self.assertEqual(decision.unregistered_jurisdictions, ["XX_UNKNOWN"])

    def test_jurisdiction_codes_are_case_normalized(self):
        decision = self.engine.evaluate_order(make_order(
            entity_jurisdiction=" us_sec ", venue_jurisdiction="eu_mifid_ii"
        ))
        self.assertEqual(decision.applicable_jurisdictions, ["EU_MIFID_II", "US_SEC"])
        self.assertEqual(decision.unregistered_jurisdictions, [])
        self.assertTrue(decision.is_approved)

    # --- Order audit ------------------------------------------------------

    def test_pfof_and_lei_violations_rejected(self):
        decision = self.engine.evaluate_order(make_order(
            order_id="ORD_001", symbol="ASML", quantity=100.0, price=800.0,
            routed_via_pfof=True, lei_tag=None,
        ))
        self.assertFalse(decision.is_approved)
        self.assertEqual(len(decision.violations), 2)  # PFOF violation & LEI violation

    def test_compliant_cross_border_order_approved(self):
        decision = self.engine.evaluate_order(make_order(order_id="ORD_002"))
        self.assertTrue(decision.is_approved)
        self.assertEqual(decision.violations, [])
        self.assertIn(OBLIGATION_LEI_TAGGING, decision.required_obligations)

    def test_total_short_ban_rejects_short_order(self):
        decision = self.engine.evaluate_order(make_order(
            venue_jurisdiction="KR_FSC", is_short=True
        ))
        self.assertFalse(decision.is_approved)
        self.assertTrue(any("SHORT SELLING VIOLATION" in v for v in decision.violations))
        # A ban must not also emit a "may proceed" obligation.
        self.assertEqual(decision.required_obligations, [OBLIGATION_LEI_TAGGING])

    def test_long_order_unaffected_by_short_ban(self):
        decision = self.engine.evaluate_order(make_order(
            venue_jurisdiction="KR_FSC", is_short=False
        ))
        self.assertTrue(decision.is_approved)

    def test_audit_message_is_deterministic_regardless_of_field_order(self):
        a = self.engine.evaluate_order(make_order(
            entity_jurisdiction="US_SEC", venue_jurisdiction="EU_MIFID_II"))
        b = self.engine.evaluate_order(make_order(
            entity_jurisdiction="EU_MIFID_II", venue_jurisdiction="US_SEC"))
        self.assertEqual(a.applied_rules_summary, b.applied_rules_summary)
        self.assertIn("EU_MIFID_II, US_SEC", a.applied_rules_summary)

    # --- LEI validation ---------------------------------------------------

    def test_valid_lei_accepted(self):
        self.assertTrue(is_valid_lei(VALID_LEI))
        self.assertTrue(is_valid_lei(VALID_LEI_ALT))

    def test_lei_with_bad_check_digits_rejected(self):
        # Regression: length-only validation accepted this 20-character string.
        self.assertFalse(is_valid_lei(LENGTH_VALID_BUT_BAD_CHECKSUM_LEI))
        decision = self.engine.evaluate_order(
            make_order(lei_tag=LENGTH_VALID_BUT_BAD_CHECKSUM_LEI)
        )
        self.assertFalse(decision.is_approved)
        self.assertTrue(any("LEI VIOLATION" in v for v in decision.violations))

    def test_transposed_characters_break_the_checksum(self):
        transposed = VALID_LEI[1] + VALID_LEI[0] + VALID_LEI[2:]
        self.assertNotEqual(transposed, VALID_LEI)
        self.assertFalse(is_valid_lei(transposed))

    def test_lei_rejects_wrong_shape_and_case(self):
        self.assertFalse(is_valid_lei(None))
        self.assertFalse(is_valid_lei(""))
        self.assertFalse(is_valid_lei("A" * 20))              # no valid check digits
        self.assertFalse(is_valid_lei(VALID_LEI[:19]))        # too short
        self.assertFalse(is_valid_lei(VALID_LEI + "0"))       # too long
        self.assertFalse(is_valid_lei(VALID_LEI.lower()))     # lower case not accepted
        self.assertFalse(is_valid_lei(VALID_LEI[:18] + "AA"))  # non-numeric check digits

    # --- Natural-person clients ------------------------------------------

    def test_natural_person_client_uses_national_id_not_lei(self):
        approved = self.engine.evaluate_order(make_order(
            lei_tag=None, is_natural_person_client=True, national_client_id="GB19800101JOHN#SMITH"
        ))
        self.assertTrue(approved.is_approved)
        self.assertIn(OBLIGATION_NATIONAL_CLIENT_ID, approved.required_obligations)

    def test_natural_person_client_without_national_id_rejected(self):
        rejected = self.engine.evaluate_order(make_order(
            lei_tag=None, is_natural_person_client=True, national_client_id=None
        ))
        self.assertFalse(rejected.is_approved)
        self.assertTrue(any("CLIENT ID VIOLATION" in v for v in rejected.violations))

    # --- Audit trail ------------------------------------------------------

    def test_audit_trail_records_every_decision_and_resists_mutation(self):
        self.engine.evaluate_order(make_order(order_id="ORD_A"))
        self.engine.evaluate_order(make_order(order_id="ORD_B", routed_via_pfof=True))
        trail = self.engine.audit_trail
        self.assertEqual([d.order_id for d in trail], ["ORD_A", "ORD_B"])
        self.assertFalse(trail[1].is_approved)

        trail[1].is_approved = True
        trail[1].violations.clear()
        refetched = self.engine.audit_trail
        self.assertFalse(refetched[1].is_approved)
        self.assertTrue(refetched[1].violations)

    # --- Input validation -------------------------------------------------

    def test_invalid_restriction_level_rejected_at_configuration_time(self):
        with self.assertRaises(ValueError):
            JurisdictionRules("XX", is_pfof_allowed=True, is_lei_mandatory=False,
                              short_selling_restriction_level=7)
        with self.assertRaises(ValueError):
            JurisdictionRules("XX", is_pfof_allowed=True, is_lei_mandatory=False,
                              short_selling_restriction_level=-1)
        with self.assertRaises(ValueError):
            JurisdictionRules("  ", is_pfof_allowed=True, is_lei_mandatory=False,
                              short_selling_restriction_level=0)

    def test_malformed_order_raises_rather_than_being_approved(self):
        for bad in (
            {"entity_jurisdiction": ""},
            {"venue_jurisdiction": "   "},
            {"order_id": ""},
            {"symbol": ""},
            {"quantity": 0.0},
            {"quantity": -10.0},
            {"quantity": float("nan")},
            {"price": float("inf")},
            {"price": -1.0},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_order(make_order(**bad))

    def test_non_dataclass_inputs_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_order({"order_id": "ORD_X"})
        with self.assertRaises(TypeError):
            self.engine.register_jurisdiction_rules("US_SEC")


if __name__ == '__main__':
    unittest.main()
