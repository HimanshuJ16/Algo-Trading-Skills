"""
Unit tests for broker-api-deprecation-notice-monitoring skill.
"""
import datetime
import unittest
from deprecation_monitor import BrokerDeprecationMonitor, DeprecationUrgency


class TestBrokerDeprecationMonitor(unittest.TestCase):

    def setUp(self):
        # Mock today as 2026-11-01
        self.mock_today = datetime.date(2026, 11, 1)
        self.monitor = BrokerDeprecationMonitor(now_fn=lambda: self.mock_today)

    def test_rfc8594_sunset_header_detection(self):
        # Sunset date: Nov 5, 2026 -> 4 days remaining -> CRITICAL_SUNSET_IMMINENT
        headers = {
            "Sunset": "Wed, 05 Nov 2026 00:00:00 GMT",
            "Deprecation": "true",
            "X-API-Deprecation-Warning": "V1 endpoint will be retired.",
        }
        notice = self.monitor.inspect_http_headers("alpaca", "/v1/orders", headers)

        self.assertIsNotNone(notice)
        self.assertEqual(notice.days_remaining, 4)
        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)

    def test_sunset_30_days_warning(self):
        # Sunset date: Nov 25, 2026 -> 24 days remaining -> WARNING_30_DAYS
        headers = {"Sunset": "2026-11-25"}
        notice = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        self.assertEqual(notice.days_remaining, 24)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)

    def test_changelog_rss_parsing(self):
        title = "Deprecation Notice: Legacy WebSockets V1"
        content = "The legacy WebSockets V1 feed is deprecated and will sunset on 2026-11-20."
        notice = self.monitor.parse_changelog_entry("coinbase", title, content, self.mock_today)

        self.assertIsNotNone(notice)
        self.assertEqual(notice.broker_name, "coinbase")
        self.assertEqual(notice.source, "CHANGELOG_FEED")


if __name__ == "__main__":
    unittest.main()
