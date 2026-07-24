"""
Unit tests for multi-exchange-feed-normalization skill.

Tests:
1. Normalizing Binance tick payload to UnifiedTick.
2. Normalizing Coinbase tick payload with ISO 8601 timestamp.
3. Normalizing Zerodha Kite tick payload.
4. Symbol mapping from venue-specific ticker to canonical symbol.
"""
import unittest
from feed_normalizer import NormalizedSide, TickNormalizerRegistry, UnifiedTick


class TestMultiExchangeFeedNormalizer(unittest.TestCase):

    def setUp(self):
        self.normalizer = TickNormalizerRegistry()
        self.normalizer.register_symbol_mapping("binance", "BTCUSDT", "BTC/USD")
        self.normalizer.register_symbol_mapping("coinbase", "BTC-USD", "BTC/USD")

    def test_binance_normalization(self):
        binance_payload = {"s": "BTCUSDT", "p": "65000.50", "q": "0.15", "T": 1700000000000, "m": False}
        tick = self.normalizer.normalize("binance", binance_payload)

        self.assertEqual(tick.symbol, "BTC/USD")
        self.assertEqual(tick.venue, "binance")
        self.assertEqual(tick.price, 65000.50)
        self.assertEqual(tick.quantity, 0.15)
        self.assertEqual(tick.side, NormalizedSide.BUY)
        self.assertEqual(tick.exchange_timestamp, 1700000000.0)

    def test_coinbase_normalization(self):
        coinbase_payload = {
            "product_id": "BTC-USD",
            "price": "65000.50",
            "size": "0.15",
            "side": "SELL",
            "time": "2026-07-24T12:00:00Z",
        }
        tick = self.normalizer.normalize("coinbase", coinbase_payload)

        self.assertEqual(tick.symbol, "BTC/USD")
        self.assertEqual(tick.venue, "coinbase")
        self.assertEqual(tick.price, 65000.50)
        self.assertEqual(tick.side, NormalizedSide.SELL)

    def test_zerodha_normalization(self):
        zerodha_payload = {
            "tradingsymbol": "INFY",
            "last_price": 1500.25,
            "last_quantity": 50,
            "last_trade_time": "2026-07-24 10:30:00",
        }
        tick = self.normalizer.normalize("zerodha", zerodha_payload)

        self.assertEqual(tick.symbol, "INFY")
        self.assertEqual(tick.venue, "zerodha")
        self.assertEqual(tick.price, 1500.25)
        self.assertEqual(tick.quantity, 50.0)


if __name__ == "__main__":
    unittest.main()
