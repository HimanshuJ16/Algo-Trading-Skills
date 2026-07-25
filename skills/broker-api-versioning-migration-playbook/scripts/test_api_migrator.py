"""
Unit tests for broker-api-versioning-migration-playbook skill.
"""
import unittest
from api_migrator import BrokerAPIVersionMigrator, MigrationPhase, OrderPayload


class TestBrokerAPIVersionMigrator(unittest.TestCase):

    def setUp(self):
        self.migrator = BrokerAPIVersionMigrator()

    def test_payload_translation_v1_to_v2(self):
        order = OrderPayload(symbol="BTCUSD", action="buy", quantity=1.5, limit_price=65000.0)
        v2_json = self.migrator.translate_payload_v1_to_v2(order)

        self.assertEqual(v2_json["instrument_id"], "BTCUSD")
        self.assertEqual(v2_json["side"], "BUY")
        self.assertEqual(v2_json["size"], 1.5)
        self.assertIn("limit_limit_gtc", v2_json["order_configuration"])

    def test_shadow_schema_audit(self):
        v1_res = {"status": "ok", "order_id": "123", "price": 100.0}
        v2_res_good = {"status": "ok", "order_id": "123", "price": 100.0, "v2_extra": True}
        v2_res_bad = {"status": "ok", "id": "123"}

        diff_good = self.migrator.audit_shadow_response("/v1/orders", v1_res, v2_res_good)
        self.assertTrue(diff_good.is_equivalent)

        diff_bad = self.migrator.audit_shadow_response("/v1/orders", v1_res, v2_res_bad)
        self.assertFalse(diff_bad.is_equivalent)
        self.assertIn("order_id", diff_bad.missing_in_v2)

    def test_canary_traffic_routing(self):
        order = OrderPayload(symbol="AAPL", action="buy", quantity=10)

        # 0% V2 -> All V1
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, canary_percentage=0.0)
        for _ in range(10):
            target = self.migrator.route_order_version(order)
            self.assertEqual(target, "V1")

        # 100% V2 -> All V2
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, canary_percentage=1.0)
        for _ in range(10):
            target = self.migrator.route_order_version(order)
            self.assertEqual(target, "V2")

    def test_emergency_rollback(self):
        order = OrderPayload(symbol="AAPL", action="buy", quantity=10)

        # Cutover to 100% V2
        self.migrator.set_phase(MigrationPhase.V2_ONLY)
        self.assertEqual(self.migrator.route_order_version(order), "V2")

        # Emergency rollback to V1
        self.migrator.set_phase(MigrationPhase.ROLLBACK_V1)
        self.assertEqual(self.migrator.route_order_version(order), "V1")


if __name__ == "__main__":
    unittest.main()
