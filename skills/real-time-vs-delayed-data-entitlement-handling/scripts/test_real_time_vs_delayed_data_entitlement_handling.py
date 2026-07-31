import unittest
from real_time_vs_delayed_data_entitlement_handling import (
    Config, Engine, RealTimeVsDelayedEntitlementEngine,
    UserEntitlement, MarketDataRequest, EntitlementAuditReport
)

class TestRealTimeVsDelayedDataEntitlementHandling(unittest.TestCase):

    def setUp(self):
        self.config = Config("test")
        self.legacy_engine = Engine(self.config)
        self.entitlement_engine = RealTimeVsDelayedEntitlementEngine(self.config)

    def test_legacy_init_and_process(self):
        self.assertEqual(self.legacy_engine.config.name, "test")
        self.assertEqual(self.legacy_engine.process(1), 1)

    def test_realtime_entitlement_approved(self):
        user = UserEntitlement("USER_PRO_01", "PROFESSIONAL", ["NASDAQ", "NYSE"], "REAL_TIME")
        req = MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True)

        report = self.entitlement_engine.evaluate_request(user, req)
        self.assertEqual(report.status, "REALTIME_STREAM_ENTITLED")
        self.assertTrue(report.is_permitted)
        self.assertFalse(report.is_delayed)
        self.assertEqual(report.delay_minutes, 0)
        self.assertTrue(report.trading_execution_allowed)

    def test_delayed_data_blocks_live_trading(self):
        user = UserEntitlement("USER_RETAIL_01", "NON_PROFESSIONAL", ["NASDAQ"], "DELAYED")
        req = MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True)

        report = self.entitlement_engine.evaluate_request(user, req)
        self.assertEqual(report.status, "LIVE_TRADING_BLOCKED_DELAYED_DATA")
        self.assertFalse(report.is_permitted)
        self.assertTrue(report.is_delayed)
        self.assertFalse(report.trading_execution_allowed)

    def test_unsubscribed_exchange_access_denied(self):
        user = UserEntitlement("USER_02", "NON_PROFESSIONAL", ["NASDAQ"], "REAL_TIME")
        req = MarketDataRequest("ES_FUT", "CME", is_trading_execution_request=False)

        report = self.entitlement_engine.evaluate_request(user, req)
        self.assertEqual(report.status, "EXCHANGE_NOT_SUBSCRIBED")
        self.assertFalse(report.is_permitted)

if __name__ == '__main__':
    unittest.main()
