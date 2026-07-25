"""
Unit tests for feed-handler-canary-deployment skill.
"""
import unittest
from canary_router import CanaryStatus, FeedHandlerCanaryRouter


class TestFeedHandlerCanaryRouter(unittest.TestCase):

    def setUp(self):
        self.router = FeedHandlerCanaryRouter(
            canary_percentage=20.0,
            canary_symbols=["AAPL", "MSFT"],
            max_allowed_error_rate=0.05,
        )

    def test_symbol_canary_routing(self):
        # Whitelisted symbols AAPL, MSFT route to V_canary
        d_aapl = self.router.route_symbol("AAPL")
        self.assertTrue(d_aapl.is_canary)
        self.assertEqual(d_aapl.version_tag, "V_canary")

        d_msft = self.router.route_symbol("MSFT")
        self.assertTrue(d_msft.is_canary)

    def test_auto_rollback_on_price_mismatch(self):
        # Process 10 ticks with 2 mismatches (>5% threshold)
        for i in range(8):
            self.assertTrue(self.router.audit_tick_pair("AAPL", 150.0, 150.0))

        # Inject 2 mismatch errors
        self.router.audit_tick_pair("AAPL", 150.0, 160.0)
        self.router.audit_tick_pair("AAPL", 150.0, 170.0)

        self.assertTrue(self.router.is_rolled_back)

        # Subsequent routing decisions revert to V_stable for all symbols
        d_aapl_after = self.router.route_symbol("AAPL")
        self.assertFalse(d_aapl_after.is_canary)
        self.assertEqual(d_aapl_after.version_tag, "V_stable")


if __name__ == "__main__":
    unittest.main()
