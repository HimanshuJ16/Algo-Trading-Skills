import unittest
import datetime
from us_reg_nms_order_protection_rule_compliance import (
    Rule611Status,
    OrderSide,
    ProtectedQuote,
    ExecutionRecord,
    RegNMSOrderProtectionEngine,
    RegNMSError,
)


class TestRegNMSOrderProtectionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RegNMSOrderProtectionEngine()
        self.now = datetime.datetime.utcnow()

        # Venue Protected NBBO quotes: NASDAQ NBB=$100.00, NYSE NBO=$100.05
        self.quotes = [
            ProtectedQuote(
                venue_id="NASDAQ",
                symbol="AAPL",
                nbb_price=100.00,
                nbb_size=500,
                nbo_price=100.10,
                nbo_size=500,
                is_automated=True,
                timestamp=self.now - datetime.timedelta(seconds=5),
            ),
            ProtectedQuote(
                venue_id="NYSE",
                symbol="AAPL",
                nbb_price=99.95,
                nbb_size=500,
                nbo_price=100.05,  # National Best Offer (NBO)
                nbo_size=500,
                is_automated=True,
                timestamp=self.now - datetime.timedelta(seconds=5),
            ),
        ]

    def test_compliant_buy_execution_at_nbo(self):
        exec_rec = ExecutionRecord(
            execution_id="EXEC-101",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.05,  # Executed at NBO ($100.05) -> Compliant!
            quantity=100,
            venue_id="NYSE",
            execution_timestamp=self.now,
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.COMPLIANT_NO_TRADE_THROUGH)

    def test_trade_through_buy_above_nbo(self):
        exec_rec = ExecutionRecord(
            execution_id="EXEC-102-BAD",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.08,  # Executed at $100.08 > NBO $100.05 -> Trade-Through Violation!
            quantity=100,
            venue_id="IEX",
            execution_timestamp=self.now,
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)
        self.assertTrue(res.trade_through_bps > 0)
        self.assertEqual(res.violating_venue_id, "NYSE")

    def test_trade_through_sell_below_nbb(self):
        exec_rec = ExecutionRecord(
            execution_id="EXEC-103-BAD",
            symbol="AAPL",
            side=OrderSide.SELL,
            price=99.90,  # Executed at $99.90 < NBB $100.00 -> Trade-Through Violation!
            quantity=100,
            venue_id="IEX",
            execution_timestamp=self.now,
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)
        self.assertEqual(res.violating_venue_id, "NASDAQ")

    def test_iso_tagged_execution_exemption(self):
        exec_rec = ExecutionRecord(
            execution_id="EXEC-104-ISO",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.10,
            quantity=100,
            venue_id="IEX",
            execution_timestamp=self.now,
            is_iso_tagged=True,  # Tagged as Intermarket Sweep Order (ISO)!
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_ISO)

    def test_benchmark_vwap_execution_exemption(self):
        exec_rec = ExecutionRecord(
            execution_id="EXEC-105-VWAP",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.10,
            quantity=100,
            venue_id="DARK_POOL",
            execution_timestamp=self.now,
            is_benchmark_vwap=True,  # Benchmark VWAP Exemption!
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_BENCHMARK)

    def test_self_help_exemption_bypasses_malfunctioning_venue(self):
        # Declare Self-Help against NYSE (which has the best NBO of $100.05)
        self.engine.declare_self_help("NYSE", "Latency Outage > 1 sec")
        self.assertTrue(self.engine.is_self_help_active("NYSE"))

        # Executing at $100.10 is now compliant because NYSE's $100.05 NBO is ignored under Self-Help!
        # Next best automated NBO is NASDAQ's $100.10.
        exec_rec = ExecutionRecord(
            execution_id="EXEC-106-SH",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.10,
            quantity=100,
            venue_id="NASDAQ",
            execution_timestamp=self.now,
        )
        res = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertTrue(res.is_compliant)

        # Revoke Self-Help and confirm trade-through triggers again
        self.engine.revoke_self_help("NYSE")
        res_after = self.engine.evaluate_execution(exec_rec, self.quotes)
        self.assertFalse(res_after.is_compliant)

    def test_flickering_quote_exemption(self):
        # Quote updated 0.5s ago
        recent_quotes = [
            ProtectedQuote(
                venue_id="NASDAQ", symbol="AAPL", nbb_price=100.00, nbb_size=100, nbo_price=100.10, nbo_size=100, timestamp=self.now
            ),
            ProtectedQuote(
                venue_id="NYSE", symbol="AAPL", nbb_price=99.95, nbb_size=100, nbo_price=100.05, nbo_size=100, timestamp=self.now
            ),
        ]
        exec_rec = ExecutionRecord(
            execution_id="EXEC-107-FLICKER",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.08,  # Inferior to $100.05, but quote updated within 1.0 sec!
            quantity=100,
            venue_id="IEX",
            execution_timestamp=self.now,
        )
        res = self.engine.evaluate_execution(exec_rec, recent_quotes)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_FLICKERING_QUOTE)


if __name__ == "__main__":
    unittest.main()

