import unittest
from conditional_order_logic_for_execution_triggers import (
    PriceCondition, VolumeCondition, AndCondition, OrCondition,
    ConditionalOrderTrigger, ChildOrderPayload
)

class TestConditionalOrderLogic(unittest.TestCase):

    def test_price_condition_evaluation(self):
        cond = PriceCondition("AAPL", "last", ">=", 150.00)
        
        self.assertFalse(cond.evaluate({"AAPL": {"last": 149.99}}))
        self.assertTrue(cond.evaluate({"AAPL": {"last": 150.00}}))
        self.assertTrue(cond.evaluate({"AAPL": {"last": 151.00}}))

    def test_composite_and_condition(self):
        # Trigger when AAPL >= 150.00 AND SPY >= 500.00 (Cross-Asset)
        cond1 = PriceCondition("AAPL", "last", ">=", 150.00)
        cond2 = PriceCondition("SPY", "last", ">=", 500.00)
        and_cond = AndCondition([cond1, cond2])
        
        # Only AAPL meets condition -> False
        state1 = {"AAPL": {"last": 152.00}, "SPY": {"last": 499.50}}
        self.assertFalse(and_cond.evaluate(state1))
        
        # Both meet condition -> True
        state2 = {"AAPL": {"last": 152.00}, "SPY": {"last": 500.25}}
        self.assertTrue(and_cond.evaluate(state2))

    def test_trigger_single_fire_behavior(self):
        cond = PriceCondition("TSLA", "last", "<=", 200.00)
        child = ChildOrderPayload("TSLA", "BUY", 100, "LIMIT", 199.50)
        trigger = ConditionalOrderTrigger("TRIG_1", cond, child)
        
        market_state = {"TSLA": {"last": 198.00}}
        
        # First tick: Should fire and return child order
        order1 = trigger.process_tick(market_state)
        self.assertIsNotNone(order1)
        self.assertEqual(order1.symbol, "TSLA")
        self.assertEqual(trigger.status, "TRIGGERED")
        
        # Second tick: Should return None because status is already TRIGGERED
        order2 = trigger.process_tick(market_state)
        self.assertIsNone(order2)

    def test_or_condition_evaluation(self):
        # Trigger if VIX >= 30 OR Volume >= 1000000
        cond1 = PriceCondition("VIX", "last", ">=", 30.0)
        cond2 = VolumeCondition("AAPL", "volume", 1000000)
        or_cond = OrCondition([cond1, cond2])
        
        self.assertTrue(or_cond.evaluate({"VIX": {"last": 31.0}, "AAPL": {"volume": 500000}}))
        self.assertTrue(or_cond.evaluate({"VIX": {"last": 20.0}, "AAPL": {"volume": 2000000}}))
        self.assertFalse(or_cond.evaluate({"VIX": {"last": 20.0}, "AAPL": {"volume": 500000}}))

if __name__ == '__main__':
    unittest.main()
