import unittest
import time
from unittest.mock import MagicMock
from api_migrator import BrokerAPIVersionMigrator, MigrationPhase, OrderPayload

class TestBrokerAPIVersionMigrator(unittest.TestCase):

    def setUp(self):
        self.migrator = BrokerAPIVersionMigrator(canary_v2_percentage=0.0)

    def test_payload_translation_v1_to_v2_market(self):
        order = OrderPayload(symbol="BTCUSD", action="buy", quantity=1.5, order_type="MARKET")
        v2_json = self.migrator.translate_payload_v1_to_v2(order)
        self.assertEqual(v2_json["instrument_id"], "BTCUSD")
        self.assertEqual(v2_json["side"], "BUY")
        self.assertEqual(v2_json["size"], 1.5)
        self.assertIn("market_market_ioc", v2_json["order_configuration"])

    def test_payload_translation_v1_to_v2_limit(self):
        order = OrderPayload(symbol="BTCUSD", action="sell", quantity=2.0, order_type="LIMIT", limit_price=65000.0)
        v2_json = self.migrator.translate_payload_v1_to_v2(order)
        self.assertIn("limit_limit_gtc", v2_json["order_configuration"])
        self.assertEqual(v2_json["order_configuration"]["limit_limit_gtc"]["limit_price"], "65000.0")

    def test_shadow_schema_audit(self):
        v1_res = {"status": "ok", "order_id": "123", "price": 100.0, "qty": 10}
        v2_res_good = {"status": "ok", "order_id": "123", "price": 100.0, "qty": 10, "v2_extra": True}
        v2_res_bad = {"status": "ok", "id": "123", "price": "100.0", "qty": 10}

        diff_good = self.migrator.audit_shadow_response("/v1/orders", v1_res, v2_res_good, 10.0, 12.0)
        self.assertTrue(diff_good.is_equivalent)
        self.assertEqual(diff_good.latency_diff_ms, 2.0)

        diff_bad = self.migrator.audit_shadow_response("/v1/orders", v1_res, v2_res_bad, 10.0, 15.0)
        self.assertFalse(diff_bad.is_equivalent)
        self.assertIn("order_id", diff_bad.missing_in_v2)
        self.assertIn("price", diff_bad.type_mismatches)

    def test_canary_traffic_routing(self):
        order = OrderPayload(symbol="AAPL", action="buy", quantity=10)

        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, canary_percentage=0.0)
        for _ in range(100):
            self.assertEqual(self.migrator.route_order_version(order), "V1")

        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, canary_percentage=1.0)
        for _ in range(100):
            self.assertEqual(self.migrator.route_order_version(order), "V2")

        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, canary_percentage=0.5)
        v1_count = 0
        v2_count = 0
        for _ in range(1000):
            res = self.migrator.route_order_version(order)
            if res == "V1": v1_count += 1
            if res == "V2": v2_count += 1
        
        self.assertTrue(400 <= v1_count <= 600)
        self.assertTrue(400 <= v2_count <= 600)

    def test_emergency_rollback(self):
        order = OrderPayload(symbol="AAPL", action="buy", quantity=10)
        self.migrator.set_phase(MigrationPhase.V2_ONLY)
        self.assertEqual(self.migrator.route_order_version(order), "V2")

        self.migrator.set_phase(MigrationPhase.ROLLBACK_V1)
        self.assertEqual(self.migrator.route_order_version(order), "V1")

    def test_shadow_read_execution(self):
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        v1_mock = MagicMock(return_value={"id": 1, "status": "ok"})
        v2_mock = MagicMock(return_value={"id": 1, "status": "ok"})
        
        res = self.migrator.execute_read_shadowing(v1_mock, v2_mock, "/positions")
        self.assertEqual(res, {"id": 1, "status": "ok"})
        v1_mock.assert_called_once()
        # V2 should also be called via shadow
        time.sleep(0.1) # Wait for thread pool
        v2_mock.assert_called_once()

if __name__ == "__main__":
    unittest.main()
