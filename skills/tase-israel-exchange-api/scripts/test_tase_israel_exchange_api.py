import unittest
import datetime
from tase_israel_exchange_api import (
    TASEConfig,
    TASESecurity,
    TASEOrder,
    TASEIntegrationEngine,
    OrderSide,
    OrderType,
    OrderStatus,
    PriceDenomination,
    InstrumentType,
    MarketPhase,
    TASEConnectionError,
    TASEValidationError,
    TASERiskLimitError,
)


class TestTASEIntegrationEngine(unittest.TestCase):
    def setUp(self):
        self.config = TASEConfig(
            sender_comp_id="SENDER_TEST",
            target_comp_id="TASE_GATEWAY",
            host="127.0.0.1",
            port=9876,
            max_order_value_ils=500_000.0,
            max_order_qty=50_000.0,
            max_price_collar_pct=10.0,
        )
        self.engine = TASEIntegrationEngine(self.config)
        self.teva_security = TASESecurity(
            symbol="TEVA.TA",
            security_id="1082511",
            isin="IL0001082511",
            instrument_type=InstrumentType.EQUITY,
            price_denomination=PriceDenomination.AGOROT,
            reference_price_ils=35.0,  # 3500 Agorot
        )
        self.engine.register_security(self.teva_security)

    def test_connect_and_disconnect(self):
        self.assertFalse(self.engine.is_connected)
        self.assertTrue(self.engine.connect())
        self.assertTrue(self.engine.is_connected)
        self.assertTrue(self.engine.disconnect())
        self.assertFalse(self.engine.is_connected)

    def test_invalid_config_raises_error(self):
        bad_config = TASEConfig(
            sender_comp_id="", target_comp_id="TASE", host="", port=0
        )
        engine = TASEIntegrationEngine(bad_config)
        with self.assertRaises(TASEValidationError):
            engine.connect()

    def test_price_conversion_agorot_ils(self):
        self.assertEqual(TASEIntegrationEngine.convert_price_agorot_to_ils(3500.0), 35.0)
        self.assertEqual(TASEIntegrationEngine.convert_price_ils_to_agorot(35.0), 3500.0)

    def test_order_value_calculation(self):
        order_agorot = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000,
            price=3500.0,  # 3500 Agorot = 35 ILS per share
            price_denomination=PriceDenomination.AGOROT,
        )
        # Total value: 1000 * 35 ILS = 35,000 ILS
        self.assertEqual(self.engine.calculate_order_value_ils(order_agorot), 35_000.0)

    def test_submit_valid_limit_order(self):
        self.engine.connect()
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000,
            price=3500.0,
            price_denomination=PriceDenomination.AGOROT,
        )
        order_id = self.engine.submit_order(order)
        self.assertIsNotNone(order_id)
        self.assertIn(order_id, self.engine.orders)
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.NEW)

    def test_submit_disconnected_raises_connection_error(self):
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=3500.0,
        )
        with self.assertRaises(TASEConnectionError):
            self.engine.submit_order(order)

    def test_pretrade_risk_max_qty_breach(self):
        self.engine.connect()
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=60_000,  # Max allowed is 50,000
            price=3500.0,
        )
        with self.assertRaises(TASERiskLimitError):
            self.engine.submit_order(order)

    def test_pretrade_risk_max_value_breach(self):
        self.engine.connect()
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=20_000,
            price=3500.0,  # 20,000 * 35 ILS = 700,000 ILS (Max limit is 500,000 ILS)
            price_denomination=PriceDenomination.AGOROT,
        )
        with self.assertRaises(TASERiskLimitError):
            self.engine.submit_order(order)

    def test_pretrade_risk_price_collar_breach(self):
        self.engine.connect()
        # Reference price is 35 ILS (3500 Agorot). Limit set to 4500 Agorot (45 ILS, >28% deviation)
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100,
            price=4500.0,
            price_denomination=PriceDenomination.AGOROT,
        )
        with self.assertRaises(TASERiskLimitError):
            self.engine.submit_order(order)

    def test_iceberg_order_validation(self):
        self.engine.connect()
        invalid_iceberg = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.ICEBERG,
            quantity=1000,
            price=3500.0,
            display_qty=1500,  # display_qty > quantity
        )
        with self.assertRaises(TASEValidationError):
            self.engine.submit_order(invalid_iceberg)

    def test_cancel_order_lifecycle(self):
        self.engine.connect()
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=500,
            price=3500.0,
        )
        order_id = self.engine.submit_order(order)
        self.assertTrue(self.engine.cancel_order(order_id))
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.CANCELED)
        # Attempting to cancel again should return False
        self.assertFalse(self.engine.cancel_order(order_id))

    def test_execution_reports_partial_and_full_fill(self):
        self.engine.connect()
        order = TASEOrder(
            symbol="TEVA.TA",
            security_id="1082511",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1000,
            price=3500.0,
        )
        order_id = self.engine.submit_order(order)

        # 1st partial fill: 400 shares @ 3490 Agorot
        upd_order = self.engine.simulate_execution_report(order_id, 400, 3490.0)
        self.assertEqual(upd_order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(upd_order.filled_quantity, 400)
        self.assertEqual(upd_order.average_price, 3490.0)

        # 2nd partial fill: 600 shares @ 3510 Agorot -> Total fill
        upd_order = self.engine.simulate_execution_report(order_id, 600, 3510.0)
        self.assertEqual(upd_order.status, OrderStatus.FILLED)
        self.assertEqual(upd_order.filled_quantity, 1000)
        # Expected VWAP = (400 * 3490 + 600 * 3510) / 1000 = 3502.0
        self.assertEqual(upd_order.average_price, 3502.0)

    def test_market_phase_calendar(self):
        # Sunday 11:00 UTC (13:00 IST) -> Continuous Trading
        sun_trading = datetime.datetime(2026, 8, 9, 11, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.engine.get_market_phase(sun_trading), MarketPhase.CONTINUOUS_TRADING)

        # Friday 11:00 UTC (13:00 IST) -> Closed (Israel Weekend)
        fri_weekend = datetime.datetime(2026, 8, 7, 11, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.engine.get_market_phase(fri_weekend), MarketPhase.CLOSED)


if __name__ == "__main__":
    unittest.main()

