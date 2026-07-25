import unittest
from cboe_complex_order_engine import CboeComplexOrderEngine, OptionLeg, Side

class TestCboeComplexOrderEngine(unittest.TestCase):

    def test_ratio_normalization(self):
        # We want to buy 100 spreads of (Buy 10 A, Sell 20 B)
        # That is a 10:20 ratio. Cboe requires it to be 1:2.
        # Order quantity should scale up by GCD(10, 20) = 10.
        # So new qty = 100 * 10 = 1000. Ratios = 1 and 2.
        engine = CboeComplexOrderEngine(cl_ord_id="123", symbol="SPX", total_quantity=100, net_price=1.50)
        engine.add_leg(OptionLeg(symbol="SPX_C1", ratio=10, side=Side.BUY))
        engine.add_leg(OptionLeg(symbol="SPX_C2", ratio=20, side=Side.SELL))
        
        norm_qty, norm_legs = engine._normalize_ratios()
        
        self.assertEqual(norm_qty, 1000)
        self.assertEqual(norm_legs[0].ratio, 1)
        self.assertEqual(norm_legs[1].ratio, 2)

    def test_fix_message_formatting(self):
        # Simple 1:1 spread
        engine = CboeComplexOrderEngine(cl_ord_id="ORDER1", symbol="SPY", total_quantity=50, net_price=-0.25)
        engine.add_leg(OptionLeg(symbol="SPY240119C400", ratio=1, side=Side.BUY))
        engine.add_leg(OptionLeg(symbol="SPY240119C410", ratio=1, side=Side.SELL))
        
        fix_msg = engine.build_fix_message()
        
        # Check standard headers
        self.assertIn("35=AB|", fix_msg)
        self.assertIn("11=ORDER1|", fix_msg)
        self.assertIn("38=50|", fix_msg)
        self.assertIn("44=-0.25|", fix_msg)
        self.assertIn("555=2|", fix_msg) # NoLegs
        
        # Check Leg 1
        self.assertIn("600=SPY240119C400|", fix_msg)
        self.assertIn("623=1|", fix_msg)
        self.assertIn("624=1|", fix_msg) # Buy
        
        # Check Leg 2
        self.assertIn("600=SPY240119C410|", fix_msg)
        self.assertIn("624=2|", fix_msg) # Sell

    def test_max_leg_limit(self):
        engine = CboeComplexOrderEngine(cl_ord_id="1", symbol="SPX", total_quantity=1, net_price=1.0)
        for i in range(16):
            engine.add_leg(OptionLeg(symbol=f"L{i}", ratio=1, side=Side.BUY))
            
        with self.assertRaises(ValueError):
            engine.add_leg(OptionLeg(symbol="L17", ratio=1, side=Side.BUY))

if __name__ == '__main__':
    unittest.main()
