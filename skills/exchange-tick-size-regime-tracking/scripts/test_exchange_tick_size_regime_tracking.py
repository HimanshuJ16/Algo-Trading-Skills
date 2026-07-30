import unittest
from exchange_tick_size_regime_tracking import (
    ExchangeTickSizeRegimeEngine, TickRegimeAuditReport
)

class TestExchangeTickSizeRegimeEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_us_equities_sub_penny_regime(self):
        # Stock >= $1.00 -> $0.01 tick
        tick_1 = self.engine.get_active_tick_size("US_EQUITIES", 150.00)
        self.assertEqual(tick_1, 0.01)

        # Stock < $1.00 -> $0.0001 sub-penny tick
        tick_sub = self.engine.get_active_tick_size("US_EQUITIES", 0.50)
        self.assertEqual(tick_sub, 0.0001)

    def test_off_tick_price_alignment(self):
        # Proposed $150.005 on US Equities (Tick = $0.01) -> Aligned to $150.01!
        report = self.engine.audit_order_tick_compliance("US_EQUITIES", "AAPL", 150.005, auto_align=True)
        
        self.assertFalse(report.is_on_tick)
        self.assertEqual(report.active_tick_size, 0.01)
        self.assertEqual(report.aligned_price, 150.01)
        self.assertEqual(report.status, "OFF_TICK_ALIGNED")

    def test_eu_xetra_dynamic_price_bands(self):
        # Price = €5.00 (< €10) -> €0.001 tick
        self.assertEqual(self.engine.get_active_tick_size("EU_XETRA", 5.00), 0.001)
        # Price = €25.00 (€10 - €50) -> €0.005 tick
        self.assertEqual(self.engine.get_active_tick_size("EU_XETRA", 25.00), 0.005)

if __name__ == '__main__':
    unittest.main()
