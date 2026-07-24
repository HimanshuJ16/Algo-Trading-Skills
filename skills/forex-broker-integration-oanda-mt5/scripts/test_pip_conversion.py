"""
Unit tests for forex-broker-integration-oanda-mt5 skill.

Tests:
1. Pip size determination for standard (4-decimal), JPY (2-decimal), and Gold (1-decimal) pairs.
2. Price diff to pips calculation across pairs.
3. Pip value in base account currency.
4. Overnight swap/rollover calculation including Wednesday 3x triple-swap rule.
5. MT5 bridge terminal liveness monitoring.
6. Backward compatibility with standalone functions (pip_size, lots_to_units, etc.).
"""
import unittest
from pip_conversion import (
    ForexPipEngine,
    MT5BridgeMonitor,
    SwapCalculationResult,
    SwapRolloverCalculator,
    lots_to_units,
    pip_size,
    price_diff_to_pips,
    units_to_lots,
)


class TestForexBrokerIntegration(unittest.TestCase):

    def test_pip_size_precision(self):
        self.assertEqual(ForexPipEngine.pip_size("EUR/USD"), 0.0001)
        self.assertEqual(ForexPipEngine.pip_size("USD_JPY"), 0.01)
        self.assertEqual(ForexPipEngine.pip_size("XAU_USD"), 0.1)

    def test_price_diff_to_pips(self):
        # EUR/USD move from 1.0850 to 1.0875 = 25 pips
        pips_eur = ForexPipEngine.price_diff_to_pips("EURUSD", 0.0025)
        self.assertAlmostEqual(pips_eur, 25.0)

        # USD/JPY move from 150.00 to 150.50 = 50 pips
        pips_jpy = ForexPipEngine.price_diff_to_pips("USDJPY", 0.50)
        self.assertAlmostEqual(pips_jpy, 50.0)

    def test_pip_value_calculation(self):
        # 1 Standard Lot (100,000 units) EUR/USD: 100,000 * 0.0001 = $10 per pip
        pip_val = ForexPipEngine.calculate_pip_value(
            pair="EURUSD", units=100_000, account_currency="USD"
        )
        self.assertEqual(pip_val, 10.0)

    def test_swap_rollover_calculator(self):
        calc = SwapRolloverCalculator()
        # Normal 1-day swap
        res1 = calc.calculate_swap(
            pair="USDJPY", side="LONG", lots=1.0, hold_days=1, includes_wednesday=False
        )
        self.assertEqual(res1.total_swap_cost, 8.40)
        self.assertFalse(res1.is_triple_swap_applied)

        # Wednesday 3x swap (1 + 2 = 3 days)
        res3 = calc.calculate_swap(
            pair="USDJPY", side="LONG", lots=1.0, hold_days=1, includes_wednesday=True
        )
        self.assertAlmostEqual(res3.total_swap_cost, 25.20)
        self.assertTrue(res3.is_triple_swap_applied)

    def test_mt5_bridge_monitor(self):
        # Terminal connected
        mon_ok = MT5BridgeMonitor(terminal_connected_check_fn=lambda: True)
        is_ok, msg = mon_ok.is_terminal_connected()
        self.assertTrue(is_ok)

        # Terminal disconnected
        mon_fail = MT5BridgeMonitor(terminal_connected_check_fn=lambda: False)
        is_bad, msg_bad = mon_fail.is_terminal_connected()
        self.assertFalse(is_bad)
        self.assertIn("lost connection", msg_bad)

    def test_backward_compatibility(self):
        self.assertEqual(pip_size("USDJPY"), 0.01)
        self.assertEqual(lots_to_units(2.5, "standard"), 250_000.0)
        self.assertEqual(units_to_lots(50_000.0, "mini"), 5.0)


if __name__ == "__main__":
    unittest.main()
