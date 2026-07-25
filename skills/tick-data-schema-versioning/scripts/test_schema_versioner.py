"""
Unit tests for tick-data-schema-versioning skill.
"""
import unittest
from schema_versioner import TickSchemaVersioner


class TestTickSchemaVersioner(unittest.TestCase):

    def setUp(self):
        self.versioner = TickSchemaVersioner()

    def test_upgrade_v1_to_v2(self):
        v1_payload = {
            "schema_version": 1,
            "symbol": "AAPL",
            "timestamp_sec": 1784948000.0,
            "price": 150.50,
            "volume": 100.0,
        }

        v2_norm = self.versioner.normalize_to_target_version(v1_payload, target_version=2)

        self.assertEqual(v2_norm["schema_version"], 2)
        self.assertEqual(v2_norm["symbol"], "AAPL")
        self.assertEqual(v2_norm["timestamp_ns"], 1784948000000000000)
        self.assertEqual(v2_norm["bid"], 150.50)
        self.assertEqual(v2_norm["exchange_id"], "UNKNOWN")

    def test_downgrade_v2_to_v1(self):
        v2_payload = {
            "schema_version": 2,
            "symbol": "BTCUSDT",
            "timestamp_ns": 1784948000000000000,
            "bid": 60000.0,
            "ask": 60010.0,
            "volume": 1.5,
            "exchange_id": "BINANCE",
        }

        v1_norm = self.versioner.normalize_to_target_version(v2_payload, target_version=1)

        self.assertEqual(v1_norm["schema_version"], 1)
        self.assertEqual(v1_norm["symbol"], "BTCUSDT")
        self.assertEqual(v1_norm["timestamp_sec"], 1784948000.0)
        self.assertAlmostEqual(v1_norm["price"], 60005.0)


if __name__ == "__main__":
    unittest.main()
