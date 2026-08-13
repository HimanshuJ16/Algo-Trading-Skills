import unittest

from algo_trading_disclosure_to_exchange_membership import (
    AlgoDisclosureComplianceEngine,
    AlgoRegistration,
    OutboundOrder,
)


class TestAlgoTradingDisclosure(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "VWAP_V2.0": "APPROVED",
            "STATARB_V1.1": "APPROVED",
            "SCOPED_V1": AlgoRegistration(
                status="approved", venues=frozenset({"XNYS"}), version="1.0"
            ),
            "TWAP_V1.0": "DEPRECATED",
            "NEW_ALGO_V1": "PENDING_EXCHANGE_APPROVAL",
        }
        self.engine = AlgoDisclosureComplianceEngine(self.registry)

    def test_compliant_algo_order(self):
        order = OutboundOrder("O-1", "AAPL", is_algorithmic=True, algo_id="VWAP_V2.0")
        result = self.engine.evaluate_order(order)
        self.assertTrue(result.is_compliant)
        self.assertEqual(result.reason_code, None)
        self.assertEqual(result.registry_status, "APPROVED")

    def test_missing_algo_id_blocked(self):
        order = OutboundOrder("O-2", "MSFT", is_algorithmic=True)
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "ALGO_ID_MISSING")
        self.assertIn("exchange-facing algo_id", result.rejection_reason)

    def test_unregistered_algo_id_blocked(self):
        order = OutboundOrder("O-3", "TSLA", is_algorithmic=True, algo_id="UNKNOWN")
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "ALGO_NOT_REGISTERED")

    def test_unapproved_algo_id_blocked(self):
        order = OutboundOrder("O-4", "TSLA", is_algorithmic=True, algo_id="TWAP_V1.0")
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "ALGO_NOT_APPROVED")
        self.assertIn("not an approved and disclosed algorithm", result.rejection_reason)

    def test_manual_order_passes(self):
        order = OutboundOrder("O-5", "GOOG", is_algorithmic=False, trader_id="TRADER_BOB")
        result = self.engine.evaluate_order(order)
        self.assertTrue(result.is_compliant)

    def test_manual_order_requires_trader_id(self):
        order = OutboundOrder("O-6", "GOOG", is_algorithmic=False)
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "MANUAL_TRADER_ID_MISSING")

    def test_manual_order_with_algo_id_is_blocked(self):
        order = OutboundOrder(
            "O-7", "GOOG", is_algorithmic=False, algo_id="VWAP_V2.0", trader_id="TRADER_BOB"
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "MANUAL_ORDER_HAS_ALGO_ID")

    def test_scoped_registration_requires_matching_venue_and_version(self):
        order = OutboundOrder(
            "O-8",
            "IBM",
            is_algorithmic=True,
            algo_id="SCOPED_V1",
            venue="xnys",
            algo_version="1.0",
        )
        result = self.engine.evaluate_order(order)
        self.assertTrue(result.is_compliant)

    def test_scoped_registration_rejects_missing_venue(self):
        order = OutboundOrder(
            "O-9", "IBM", is_algorithmic=True, algo_id="SCOPED_V1", algo_version="1.0"
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "VENUE_MISSING")

    def test_scoped_registration_rejects_wrong_venue(self):
        order = OutboundOrder(
            "O-10",
            "IBM",
            is_algorithmic=True,
            algo_id="SCOPED_V1",
            venue="XNAS",
            algo_version="1.0",
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "VENUE_NOT_APPROVED")

    def test_scoped_registration_rejects_wrong_version(self):
        order = OutboundOrder(
            "O-11",
            "IBM",
            is_algorithmic=True,
            algo_id="SCOPED_V1",
            venue="XNYS",
            algo_version="2.0",
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "ALGO_VERSION_MISMATCH")

    def test_child_order_must_inherit_parent_algo_id(self):
        order = OutboundOrder(
            "O-12",
            "IBM",
            is_algorithmic=True,
            algo_id="VWAP_V2.0",
            parent_algo_id="STATARB_V1.1",
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "PARENT_ALGO_ID_MISMATCH")

    def test_registry_is_snapshotted_at_initialization(self):
        self.registry["STATARB_V1.1"] = "DEPRECATED"
        order = OutboundOrder(
            "O-13", "IBM", is_algorithmic=True, algo_id="STATARB_V1.1"
        )
        result = self.engine.evaluate_order(order)
        self.assertTrue(result.is_compliant)

    def test_invalid_order_field_is_blocked(self):
        order = OutboundOrder("O-14", " ", is_algorithmic=True, algo_id="VWAP_V2.0")
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "INVALID_ORDER_FIELD")

    def test_malformed_registry_is_rejected(self):
        with self.assertRaises(TypeError):
            AlgoDisclosureComplianceEngine({"VWAP_V2.0": object()})


if __name__ == "__main__":
    unittest.main()
