import unittest
from conflict_of_interest_disclosure_for_prop_vs_client_flow import (
    PropVsClientConflictEngine, ClientOrder, PropOrder
)

class TestPropVsClientConflictEngine(unittest.TestCase):

    def test_finra_rule_5320_violation(self):
        # Pending retail client order: Buy 500 AAPL @ $150.00 (Barrier "DESK_A")
        client_order = ClientOrder(
            order_id="C_101", symbol="AAPL", side="BUY",
            quantity=500, limit_price=150.00, info_barrier_id="DESK_A",
            is_institutional=False, opted_out_5320=False
        )
        engine = PropVsClientConflictEngine(pending_client_orders=[client_order])

        # Prop order: Buy 1,000 AAPL @ $150.00 from same DESK_A
        prop_order = PropOrder(
            order_id="P_201", symbol="AAPL", side="BUY",
            quantity=1000, price=150.00, info_barrier_id="DESK_A"
        )
        
        res = engine.evaluate_prop_order(prop_order)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, "FINRA_RULE_5320_FRONT_RUNNING")

    def test_no_knowledge_information_barrier_exception(self):
        # Client order on Agency desk ("BARRIER_AGENCY")
        client_order = ClientOrder(
            order_id="C_102", symbol="AAPL", side="BUY",
            quantity=500, limit_price=150.00, info_barrier_id="BARRIER_AGENCY"
        )
        engine = PropVsClientConflictEngine(pending_client_orders=[client_order])

        # Prop order on distinct Prop desk ("BARRIER_PROP_HFT")
        prop_order = PropOrder(
            order_id="P_202", symbol="AAPL", side="BUY",
            quantity=1000, price=150.00, info_barrier_id="BARRIER_PROP_HFT"
        )
        
        res = engine.evaluate_prop_order(prop_order)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exception_applied, "NO_KNOWLEDGE_BARRIER")

    def test_institutional_opt_out_exception(self):
        # Institutional client order (10,000 shares @ $100 = $1M) with 5320 opt-out signed
        client_order = ClientOrder(
            order_id="C_103", symbol="MSFT", side="SELL",
            quantity=10000, limit_price=400.00, info_barrier_id="DESK_A",
            is_institutional=True, opted_out_5320=True
        )
        engine = PropVsClientConflictEngine(pending_client_orders=[client_order])

        # Prop order selling at $399.50 (better/equal price)
        prop_order = PropOrder(
            order_id="P_203", symbol="MSFT", side="SELL",
            quantity=2000, price=399.50, info_barrier_id="DESK_A"
        )
        
        res = engine.evaluate_prop_order(prop_order)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exception_applied, "INSTITUTIONAL_OPT_OUT")

    def test_non_conflicting_price_passes(self):
        # Client wants to buy AAPL @ $145.00
        client_order = ClientOrder(
            order_id="C_104", symbol="AAPL", side="BUY",
            quantity=500, limit_price=145.00, info_barrier_id="DESK_A"
        )
        engine = PropVsClientConflictEngine(pending_client_orders=[client_order])

        # Prop wants to buy AAPL @ $140.00 (below client limit price -> no conflict)
        prop_order = PropOrder(
            order_id="P_204", symbol="AAPL", side="BUY",
            quantity=1000, price=140.00, info_barrier_id="DESK_A"
        )
        
        res = engine.evaluate_prop_order(prop_order)
        self.assertTrue(res.is_approved)
        self.assertIsNone(res.violation_type)

if __name__ == '__main__':
    unittest.main()
