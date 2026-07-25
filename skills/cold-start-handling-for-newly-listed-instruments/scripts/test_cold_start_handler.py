"""Unit tests for cold-start-handling-for-newly-listed-instruments."""
import unittest
from cold_start_handler import ColdStartHandler


class TestColdStartHandler(unittest.TestCase):
    def setUp(self):
        self.handler = ColdStartHandler(min_required_bars=30, cold_start_size_scale=0.25)

    def test_new_listing_triggers_cold_start(self):
        native = {"volatility": float("nan")}
        proxy = {"volatility": 0.20, "momentum": 0.05}

        eval_res = self.handler.evaluate_instrument("NEW_IPO", bars_available=5, native_features=native, sector_peer_proxy_features=proxy)

        self.assertTrue(eval_res.is_cold_start)
        self.assertEqual(eval_res.size_scaling_factor, 0.25)
        self.assertEqual(eval_res.features_used["volatility"], 0.20)
        self.assertIn("COLD START ACTIVE", eval_res.status_message)

    def test_mature_listing_uses_native_features(self):
        native = {"volatility": 0.15, "momentum": 0.02}
        proxy = {"volatility": 0.20, "momentum": 0.05}

        eval_res = self.handler.evaluate_instrument("AAPL", bars_available=100, native_features=native, sector_peer_proxy_features=proxy)

        self.assertFalse(eval_res.is_cold_start)
        self.assertEqual(eval_res.size_scaling_factor, 1.0)
        self.assertEqual(eval_res.features_used["volatility"], 0.15)


if __name__ == "__main__":
    unittest.main()
