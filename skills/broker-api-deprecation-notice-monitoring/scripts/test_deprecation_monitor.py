"""
Unit tests for broker-api-deprecation-notice-monitoring skill.
"""
import datetime
import unittest
from unittest.mock import MagicMock
from deprecation_monitor import BrokerDeprecationMonitor, DeprecationUrgency, DeprecationNotice


class TestBrokerDeprecationMonitor(unittest.TestCase):

    def setUp(self):
        # Mock today as 2026-11-01 00:00:00 UTC
        self.mock_today = datetime.datetime(2026, 11, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        self.mock_callback = MagicMock()
        self.monitor = BrokerDeprecationMonitor(
            now_fn=lambda: self.mock_today,
            alert_callback=self.mock_callback
        )

    def test_rfc8594_sunset_header_detection(self):
        # Sunset date: Nov 5, 2026 -> 4 days remaining -> CRITICAL_SUNSET_IMMINENT
        headers = {
            "Sunset": "Thu, 05 Nov 2026 00:00:00 GMT",
            "Deprecation": "true",
            "X-API-Deprecation-Warning": "V1 endpoint will be retired.",
            "Link": '<https://api.alpaca.markets/sunset>; rel="sunset"'
        }
        notice = self.monitor.inspect_http_headers("alpaca", "/v1/orders", headers)

        self.assertIsNotNone(notice)
        self.assertEqual(notice.days_remaining, 4)
        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)
        self.assertEqual(notice.reference_link, "https://api.alpaca.markets/sunset")
        self.mock_callback.assert_called_once_with(notice)

    def test_sunset_30_days_warning(self):
        # Sunset date: Nov 25, 2026 -> 24 days remaining -> WARNING_30_DAYS
        headers = {"Sunset": "2026-11-25"}
        notice = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        self.assertIsNotNone(notice)
        self.assertEqual(notice.days_remaining, 24)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)
        self.mock_callback.assert_called_once_with(notice)
        
    def test_link_header_extraction_only(self):
        headers = {
            "Link": '<https://api.exchange.com/docs/deprecation>; rel="deprecation", <https://api.exchange.com/docs/sunset>; rel="sunset"'
        }
        notice = self.monitor.inspect_http_headers("binance", "/v3/depth", headers)
        self.assertIsNotNone(notice)
        self.assertEqual(notice.reference_link, "https://api.exchange.com/docs/sunset")
        self.assertIsNone(notice.days_remaining)
        self.assertEqual(notice.urgency, DeprecationUrgency.NOTICE)

    def test_changelog_rss_parsing(self):
        title = "Deprecation Notice: Legacy WebSockets V1"
        content = "The legacy WebSockets V1 feed is deprecated and will sunset on 2026-11-20."
        notice = self.monitor.parse_changelog_entry("coinbase", title, content, self.mock_today, "https://blog.coinbase.com/update")

        self.assertIsNotNone(notice)
        self.assertEqual(notice.broker_name, "coinbase")
        self.assertEqual(notice.source, "CHANGELOG_FEED")
        self.assertEqual(notice.days_remaining, 19)
        self.assertEqual(notice.reference_link, "https://blog.coinbase.com/update")
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)
        
    def test_thread_safety_deduplication(self):
        headers = {"Sunset": "2026-11-25"}
        notice1 = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)
        notice2 = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)
        
        self.assertEqual(len(self.monitor.active_notices), 1)
        self.assertEqual(self.mock_callback.call_count, 1)

if __name__ == "__main__":
    unittest.main()
