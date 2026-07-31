import unittest
from unittest.mock import Mock
from mt5_python_bridge_for_forex_bots import (
    MT5PythonBridgeEngine, MT5Config, MT5OrderRequest, MT5OrderReport, TRADE_RETCODE_DONE, TRADE_RETCODE_NO_MONEY
)

class TestMT5PythonBridgeEngine(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=123456, password="secret_password", server="DemoServer")
        self.engine = MT5PythonBridgeEngine(self.config)

    def test_valid_buy_order_execution(self):
        # EURUSD Buy 0.1 lots @ 1.0850, SL = 1.0800, TP = 1.0950 -> MT5_ORDER_EXECUTED_SUCCESS!
        order = MT5OrderRequest("EURUSD", "BUY", volume_lots=0.1, price=1.0850, sl_price=1.0800, tp_price=1.0950)

        report = self.engine.execute_forex_order(order)

        self.assertTrue(report.is_executed)
        self.assertEqual(report.status, "MT5_ORDER_EXECUTED_SUCCESS")
        self.assertEqual(report.retcode, TRADE_RETCODE_DONE)
        self.assertEqual(report.mql_trade_request["volume"], 0.1)
        self.assertEqual(report.mql_trade_request["action"], 1)

    def test_invalid_stop_loss_rejection(self):
        # EURUSD Buy with SL (1.0900) ABOVE entry price (1.0850) -> MT5_INVALID_STOPS!
        order = MT5OrderRequest("EURUSD", "BUY", volume_lots=0.1, price=1.0850, sl_price=1.0900, tp_price=1.0950)

        report = self.engine.execute_forex_order(order)

        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")

    def test_insufficient_funds_retcode_failure(self):
        # Mock IPC returning 10019 (NO_MONEY) -> MT5_EXECUTION_FAILED!
        mock_ipc = Mock()
        mock_ipc.order_send.return_value = {"retcode": TRADE_RETCODE_NO_MONEY, "comment": "No money for deal"}

        engine_failed = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=mock_ipc)
        order = MT5OrderRequest("GBPUSD", "SELL", volume_lots=10.0, price=1.2700, sl_price=1.2750)

        report = engine_failed.execute_forex_order(order)

        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_EXECUTION_FAILED")
        self.assertEqual(report.retcode, TRADE_RETCODE_NO_MONEY)

if __name__ == '__main__':
    unittest.main()
