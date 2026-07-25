import unittest
from borsa_istanbul_api_integration import BISTConfig, BISTIntegrationEngine, FIXOrder, OrderType, OrderSide, OrderStatus

class TestBISTIntegrationEngine(unittest.TestCase):
    def setUp(self):
        self.config = BISTConfig(
            sender_comp_id="MYFIRM",
            target_comp_id="BIST",
            host="192.168.1.100",
            port=9800
        )
        self.engine = BISTIntegrationEngine(self.config)
        
    def test_connection_sequence(self):
        self.assertFalse(self.engine.is_connected)
        self.assertTrue(self.engine.connect())
        self.assertTrue(self.engine.is_connected)
        self.assertTrue(self.engine.disconnect())
        self.assertFalse(self.engine.is_connected)
        
    def test_invalid_connection(self):
        bad_config = BISTConfig("A", "B", "", 0)
        bad_engine = BISTIntegrationEngine(bad_config)
        with self.assertRaises(ValueError):
            bad_engine.connect()
            
    def test_submit_order(self):
        self.engine.connect()
        order = FIXOrder(
            symbol="THYAO.E",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=250.50
        )
        order_id = self.engine.submit_order(order)
        self.assertIn(order_id, self.engine.orders)
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.NEW)
        
    def test_submit_order_disconnected(self):
        order = FIXOrder(symbol="GARAN.E", side=OrderSide.SELL, order_type=OrderType.MARKET, quantity=50)
        with self.assertRaises(ConnectionError):
            self.engine.submit_order(order)
            
    def test_invalid_limit_order(self):
        self.engine.connect()
        order = FIXOrder(symbol="GARAN.E", side=OrderSide.SELL, order_type=OrderType.LIMIT, quantity=50)
        with self.assertRaises(ValueError):
            self.engine.submit_order(order)
            
    def test_cancel_order(self):
        self.engine.connect()
        order = FIXOrder(symbol="AKBNK.E", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=200, price=30.0)
        order_id = self.engine.submit_order(order)
        
        result = self.engine.cancel_order(order_id)
        self.assertTrue(result)
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.CANCELED)
        
        # Try to cancel again
        result2 = self.engine.cancel_order(order_id)
        self.assertFalse(result2)
        
    def test_execution_report(self):
        self.engine.connect()
        order = FIXOrder(symbol="TUPRS.E", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=100, price=150.0)
        order_id = self.engine.submit_order(order)
        
        # Partial fill
        updated_order = self.engine.simulate_execution_report(order_id, 40, 149.5)
        self.assertIsNotNone(updated_order)
        if updated_order:
            self.assertEqual(updated_order.status, OrderStatus.PARTIALLY_FILLED)
            self.assertEqual(updated_order.filled_quantity, 40)
            
        # Complete fill
        updated_order2 = self.engine.simulate_execution_report(order_id, 60, 150.0)
        self.assertIsNotNone(updated_order2)
        if updated_order2:
            self.assertEqual(updated_order2.status, OrderStatus.FILLED)
            self.assertEqual(updated_order2.filled_quantity, 100)
            self.assertAlmostEqual(updated_order2.average_price, 149.8)
            
if __name__ == '__main__':
    unittest.main()
