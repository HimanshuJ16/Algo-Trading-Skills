import unittest
from cross_jurisdiction_regulatory_conflict_resolution import (
    CrossJurisdictionRegulatoryConflictEngine, JurisdictionRules, TradeOrderRequest
)

class TestCrossJurisdictionRegulatoryConflictEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CrossJurisdictionRegulatoryConflictEngine([
            JurisdictionRules("US_SEC", is_pfof_allowed=True, is_lei_mandatory=False, short_selling_restriction_level=1),
            JurisdictionRules("EU_MIFID_II", is_pfof_allowed=False, is_lei_mandatory=True, short_selling_restriction_level=2),
            JurisdictionRules("KR_FSC", is_pfof_allowed=False, is_lei_mandatory=True, short_selling_restriction_level=3) # Total short ban
        ])

    def test_strictest_rule_resolution_for_us_to_eu_trade(self):
        # US Entity trading on EU Venue:
        # PFOF: US (True) vs EU (False) -> Strictest = False
        # LEI: US (False) vs EU (True) -> Strictest = True
        pfof_allowed, lei_mandatory, short_lvl = self.engine.resolve_strictest_rules({"US_SEC", "EU_MIFID_II"})
        
        self.assertFalse(pfof_allowed)
        self.assertTrue(lei_mandatory)
        self.assertEqual(short_lvl, 2)

    def test_pfof_and_lei_violations_rejected(self):
        # Order with PFOF routed = True and missing LEI tag
        req = TradeOrderRequest(
            order_id="ORD_001", entity_jurisdiction="US_SEC", venue_jurisdiction="EU_MIFID_II",
            symbol="ASML", quantity=100.0, price=800.0, is_short=False,
            routed_via_pfof=True, lei_tag=None
        )
        decision = self.engine.evaluate_order(req)
        
        self.assertFalse(decision.is_approved)
        self.assertEqual(len(decision.violations), 2) # PFOF violation & LEI violation

    def test_compliant_cross_border_order_approved(self):
        # Compliant order: No PFOF, Valid 20-char LEI tag
        req = TradeOrderRequest(
            order_id="ORD_002", entity_jurisdiction="US_SEC", venue_jurisdiction="EU_MIFID_II",
            symbol="SAP", quantity=500.0, price=180.0, is_short=False,
            routed_via_pfof=False, lei_tag="5493001KJTIIGC8Y1S12"
        )
        decision = self.engine.evaluate_order(req)
        
        self.assertTrue(decision.is_approved)
        self.assertEqual(len(decision.violations), 0)

if __name__ == '__main__':
    unittest.main()
