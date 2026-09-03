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


    # --- Regression tests for registry-construction fail-open defects ---

    def test_venues_given_as_bare_string_is_rejected(self):
        """A bare string used to iterate per character and widen venue scope.

        ``venues="XNYS"`` previously normalized to {"X", "N", "Y", "S"}, which
        rejected the intended venue XNYS while approving an order routed to a
        venue literally named "X".
        """
        with self.assertRaises(TypeError):
            AlgoDisclosureComplianceEngine(
                {"VWAP_V2.0": AlgoRegistration(status="APPROVED", venues="XNYS")}
            )

    def test_correctly_declared_venue_scope_rejects_substring_venue(self):
        engine = AlgoDisclosureComplianceEngine(
            {"VWAP_V2.0": AlgoRegistration(status="APPROVED", venues=frozenset({"XNYS"}))}
        )
        result = engine.evaluate_order(
            OutboundOrder("O-20", "IBM", is_algorithmic=True, algo_id="VWAP_V2.0", venue="X")
        )
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "VENUE_NOT_APPROVED")

    def test_venues_may_be_any_non_string_collection(self):
        engine = AlgoDisclosureComplianceEngine(
            {"VWAP_V2.0": AlgoRegistration(status="APPROVED", venues=["xnys", "XNAS"])}
        )
        self.assertEqual(engine.registry["VWAP_V2.0"].venues, frozenset({"XNYS", "XNAS"}))

    def test_non_iterable_venues_is_rejected(self):
        with self.assertRaises(TypeError):
            AlgoDisclosureComplianceEngine(
                {"VWAP_V2.0": AlgoRegistration(status="APPROVED", venues=None)}
            )

    def test_duplicate_registry_keys_cannot_silently_promote_an_algo(self):
        """Keys differing only by whitespace previously collapsed, last-wins.

        A SUSPENDED entry could therefore be silently overwritten by an
        APPROVED one during a registry merge.
        """
        with self.assertRaises(ValueError):
            AlgoDisclosureComplianceEngine(
                {"VWAP_V2.0": "SUSPENDED", " VWAP_V2.0 ": "APPROVED"}
            )

    def test_blank_registered_version_is_rejected_at_construction(self):
        """A whitespace-only version used to become an unsatisfiable constraint."""
        with self.assertRaises(ValueError):
            AlgoDisclosureComplianceEngine(
                {"VWAP_V2.0": AlgoRegistration(status="APPROVED", version="  ")}
            )

    def test_registry_snapshot_is_read_only(self):
        with self.assertRaises(TypeError):
            self.engine.registry["INJECTED"] = AlgoRegistration(status="APPROVED")
        order = OutboundOrder("O-21", "IBM", is_algorithmic=True, algo_id="INJECTED")
        self.assertFalse(self.engine.evaluate_order(order).is_compliant)

    def test_manual_order_with_parent_algo_id_is_blocked(self):
        order = OutboundOrder(
            "O-22", "GOOG", is_algorithmic=False, trader_id="TRADER_BOB",
            parent_algo_id="VWAP_V2.0",
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "MANUAL_ORDER_HAS_ALGO_METADATA")

    def test_manual_order_with_algo_version_is_blocked(self):
        order = OutboundOrder(
            "O-23", "GOOG", is_algorithmic=False, trader_id="TRADER_BOB",
            algo_version="1.0",
        )
        result = self.engine.evaluate_order(order)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "MANUAL_ORDER_HAS_ALGO_METADATA")

    def test_venue_match_is_case_insensitive_but_version_match_is_exact(self):
        """Documented asymmetry: venue codes fold case, versions are literal."""
        engine = AlgoDisclosureComplianceEngine(
            {"A_V1": AlgoRegistration("APPROVED", frozenset({"XNYS"}), "1.0-RC")}
        )
        lowered_venue = OutboundOrder(
            "O-24", "IBM", is_algorithmic=True, algo_id="A_V1",
            venue="xnys", algo_version="1.0-RC",
        )
        self.assertTrue(engine.evaluate_order(lowered_venue).is_compliant)

        lowered_version = OutboundOrder(
            "O-25", "IBM", is_algorithmic=True, algo_id="A_V1",
            venue="XNYS", algo_version="1.0-rc",
        )
        result = engine.evaluate_order(lowered_version)
        self.assertFalse(result.is_compliant)
        self.assertEqual(result.reason_code, "ALGO_VERSION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
