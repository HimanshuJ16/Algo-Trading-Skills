import unittest
from futures_contract_roll_automation import (
    FuturesContractRollEngine, FuturesContractState, FuturesRollAuditReport
)

class TestFuturesContractRollEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FuturesContractRollEngine(min_days_to_expiration=5, enable_volume_crossover=True)

    def test_volume_crossover_triggers_long_roll(self):
        # Front: ESH6 (Vol 50,000, DBE 10d, Price $5,000.00)
        front = FuturesContractState("ESH6", "2026-03-20", days_to_expiration=10, daily_volume=50000, open_interest=100000, last_price=5000.0)
        # Next: ESM6 (Vol 120,000, DBE 100d, Price $5,015.00 -> Contango +$15.00)
        next_contract = FuturesContractState("ESM6", "2026-06-19", days_to_expiration=100, daily_volume=120000, open_interest=150000, last_price=5015.0)

        report = self.engine.evaluate_and_build_roll_order("ES", position_side="LONG", position_qty=10, front_contract=front, next_contract=next_contract)

        self.assertTrue(report.is_roll_triggered)
        self.assertEqual(report.status, "ROLL_SIGNAL_ACTIVE")
        self.assertEqual(report.trigger_reason, "VOLUME_CROSSOVER")
        self.assertIsNotNone(report.calendar_spread_order)
        self.assertEqual(report.calendar_spread_order.front_leg_action, "SELL") # Long roll sells front
        self.assertEqual(report.calendar_spread_order.next_leg_action, "BUY")   # Long roll buys next
        self.assertEqual(report.calendar_spread_order.term_structure, "CONTANGO")
        self.assertEqual(report.calendar_spread_order.spread_price_diff, 15.0)

    def test_hold_front_when_no_trigger(self):
        # Front has higher volume (150,000 > 20,000) and DBE 15d > 5d
        front = FuturesContractState("ESH6", "2026-03-20", days_to_expiration=15, daily_volume=150000, open_interest=200000, last_price=5000.0)
        next_contract = FuturesContractState("ESM6", "2026-06-19", days_to_expiration=105, daily_volume=20000, open_interest=30000, last_price=5010.0)

        report = self.engine.evaluate_and_build_roll_order("ES", position_side="LONG", position_qty=10, front_contract=front, next_contract=next_contract)

        self.assertFalse(report.is_roll_triggered)
        self.assertEqual(report.status, "HOLD_FRONT_CONTRACT")
        self.assertIsNone(report.calendar_spread_order)

if __name__ == '__main__':
    unittest.main()
