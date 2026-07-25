import unittest
from bursa_malaysia_api_integration import (
    BursaMalaysiaFixEngine, 
    BursaConfig, 
    FIXOrder, 
    OrderType, 
    OrderSide, 
    OrderStatus
)

class TestBursaMalaysiaApiIntegration(unittest.TestCase):
    def setUp(self):
        self.config = BursaConfig(
            sender_comp_id="FIRM123",
            target_comp_id="FIXTRADER",
            host="192.168.1.10",
            port=443,
            protocol_version="FIX.5.0SP1"
        )
        self.engine = BursaMalaysiaFixEngine(self.config)

    def test_protocol_version_validation(self):
        with self.assertRaises(ValueError):
            bad_config = BursaConfig(
                sender_comp_id="FIRM123",
                target_comp_id="FIXTRADER",
                host="192.168.1.10",
                port=443,
                protocol_version="FIX.4.2" # Bursa requires 5.0 SP1
            )
            BursaMalaysiaFixEngine(bad_config)

    def test_order_lifecycle(self):
        self.engine.connect()
        
        order = FIXOrder(
            symbol="MAYBANK",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000,
            price=8.50
        )
        
        client_id = self.engine.submit_order(order)
        self.assertIn(client_id, self.engine.orders)
        
        # Partial fill
        self.engine.simulate_execution_report(client_id, 400, 8.50)
        self.assertEqual(self.engine.orders[client_id].status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(self.engine.orders[client_id].filled_quantity, 400)
        
        # Complete fill
        self.engine.simulate_execution_report(client_id, 600, 8.50)
        self.assertEqual(self.engine.orders[client_id].status, OrderStatus.FILLED)
        self.assertEqual(self.engine.orders[client_id].filled_quantity, 1000)

    def test_cancel_order(self):
        self.engine.connect()
        order = FIXOrder(
            symbol="TENAGA",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=500,
            price=10.00
        )
        client_id = self.engine.submit_order(order)
        
        success = self.engine.cancel_order(client_id)
        self.assertTrue(success)
        self.assertEqual(self.engine.orders[client_id].status, OrderStatus.CANCELED)

if __name__ == '__main__':
    unittest.main()
