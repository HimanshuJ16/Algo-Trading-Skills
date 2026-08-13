import unittest
from dataclasses import FrozenInstanceError

import numpy as np

from ml_tca_backtester import MlTcaBacktester, MlTcaBacktesterConfig


class TestMlTcaBacktester(unittest.TestCase):

    def test_high_turnover_cost_drag(self):
        # Model is perfect, but flips direction every day.
        # Predictions always exceed threshold.
        preds = np.array([0.05, -0.05, 0.05, -0.05, 0.05])
        actuals = np.array([0.05, -0.05, 0.05, -0.05, 0.05])

        # 100 bps cost per half-turn (1%).
        config = MlTcaBacktesterConfig(bps_cost_half_turn=100.0, signal_threshold=0.01)
        tester = MlTcaBacktester(config)

        res = tester.process(preds, actuals)

        # Turnover calculation:
        # T0: flat to +1 (Turnover 1) -> Cost 1%
        # T1: +1 to -1 (Turnover 2) -> Cost 2%
        # T2: -1 to +1 (Turnover 2) -> Cost 2%
        # T3: +1 to -1 (Turnover 2) -> Cost 2%
        # T4: -1 to +1 (Turnover 2) + terminal liquidation (1) -> Cost 3%
        # Total Turnover = 10 units, total cost = 10%
        self.assertEqual(res["Total Turnover (Units)"], 10.0)
        self.assertEqual(res["Total Trade Count"], 5)
        self.assertAlmostEqual(res["Total Cost Paid"], 0.10, places=12)

        # Independently derived: gross is 5% every bar; net is 4%, 3%, 3%, 3%, 2%.
        self.assertAlmostEqual(res["Total Gross Return"], 1.05 ** 5 - 1.0, places=12)
        self.assertAlmostEqual(
            res["Total Net Return"],
            1.04 * 1.03 * 1.03 * 1.03 * 1.02 - 1.0,
            places=12,
        )
        self.assertAlmostEqual(
            res["Cost Drag (%)"],
            (res["Total Gross Return"] - res["Total Net Return"]) * 100.0,
            places=12,
        )

    def test_perfect_but_churning_model_is_net_negative(self):
        # SKILL.md Verification scenario: a model that perfectly predicts a 10%
        # move but flips every bar must lose money at 6% (600 bps) per half-turn.
        preds = np.array([0.10, -0.10, 0.10, -0.10, 0.10])
        actuals = np.array([0.10, -0.10, 0.10, -0.10, 0.10])

        config = MlTcaBacktesterConfig(bps_cost_half_turn=600.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process(preds, actuals)

        # Gross: +10% every bar. Net: +4%, -2%, -2%, -2%, -8% (last bar liquidates).
        self.assertAlmostEqual(res["Total Gross Return"], 1.10 ** 5 - 1.0, places=12)
        self.assertAlmostEqual(
            res["Total Net Return"], 1.04 * 0.98 * 0.98 * 0.98 * 0.92 - 1.0, places=12
        )
        self.assertGreater(res["Total Gross Return"], 0.6)
        self.assertLess(res["Total Net Return"], -0.09)

    def test_thresholding_prevents_trades(self):
        # Model predictions are very weak
        preds = np.array([0.0001, -0.0001, 0.0001])
        actuals = np.array([0.05, -0.05, 0.05])

        # Threshold requires 0.001 (10 bps) prediction to trade
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.001)
        tester = MlTcaBacktester(config)

        res = tester.process(preds, actuals)

        # Predictions never exceed threshold -> 0 turnover, 0 gross, 0 net.
        self.assertEqual(res["Total Turnover (Units)"], 0.0)
        self.assertEqual(res["Total Gross Return"], 0.0)
        self.assertEqual(res["Total Net Return"], 0.0)
        self.assertEqual(res["Positions Series"], [0.0, 0.0, 0.0])

    def test_cost_charged_only_on_position_change(self):
        # "Ignoring State Persistence" pitfall: holding a position across bars
        # must not be re-charged. Two identical long signals = one entry only.
        preds = np.array([0.02, 0.02])
        actuals = np.array([0.10, 0.10])
        config = MlTcaBacktesterConfig(
            bps_cost_half_turn=100.0, signal_threshold=0.01, liquidate_at_end=False
        )
        res = MlTcaBacktester(config).process(preds, actuals)

        self.assertEqual(res["Total Turnover (Units)"], 1.0)
        self.assertEqual(res["Total Trade Count"], 1)
        # Bar 0 pays the 1% entry; bar 1 holds and pays nothing.
        self.assertAlmostEqual(res["Net Returns Series"][0], 0.09, places=12)
        self.assertAlmostEqual(res["Net Returns Series"][1], 0.10, places=12)

    def test_terminal_position_pays_exit_cost(self):
        # A single entry that is never closed still owes the exit half-turn.
        preds = np.array([0.02])
        actuals = np.array([0.0])
        config = MlTcaBacktesterConfig(bps_cost_half_turn=100.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process(preds, actuals)

        self.assertEqual(res["Total Turnover (Units)"], 2.0)  # entry + liquidation
        self.assertAlmostEqual(res["Total Net Return"], -0.02, places=12)

        # Opt-out reproduces the un-liquidated (cost-understating) convention.
        open_cfg = MlTcaBacktesterConfig(
            bps_cost_half_turn=100.0, signal_threshold=0.01, liquidate_at_end=False
        )
        open_res = MlTcaBacktester(open_cfg).process(preds, actuals)
        self.assertEqual(open_res["Total Turnover (Units)"], 1.0)
        self.assertAlmostEqual(open_res["Total Net Return"], -0.01, places=12)

    def test_flat_predictions_never_become_short(self):
        # Behaviour contract asserted in SKILL.md Verification: a neutral prediction
        # is flat, never short. (The overlap defect this guards against only fired
        # at signal_threshold <= 0, which is now rejected outright — see
        # test_non_positive_threshold_rejected for that regression.)
        preds = np.array([0.0, 0.0, 0.02])
        actuals = np.array([0.01, 0.01, 0.01])
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process(preds, actuals)

        self.assertEqual(res["Positions Series"], [0.0, 0.0, 1.0])
        self.assertEqual(res["Gross Returns Series"], [0.0, 0.0, 0.01])

    def test_exact_threshold_enters_symmetrically(self):
        # Boundary: |prediction| == signal_threshold is a trade, in both directions.
        preds = np.array([0.01, -0.01, 0.00999999])
        actuals = np.zeros(3)
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process(preds, actuals)

        self.assertEqual(res["Positions Series"], [1.0, -1.0, 0.0])

    def test_config_is_immutable(self):
        # Mutating a validated config would bypass __post_init__ and leave the
        # backtester's cached cost_rate stale.
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        with self.assertRaises(FrozenInstanceError):
            config.bps_cost_half_turn = -100.0

    def test_non_positive_threshold_rejected(self):
        # A zero/negative entry threshold makes the long and short regions overlap:
        # at threshold 0.0, `pred <= -0.0` is true for a 0.0 prediction, so a
        # neutral signal was silently classified SHORT. Rejected at construction.
        with self.assertRaises(ValueError):
            MlTcaBacktesterConfig(signal_threshold=0.0)
        with self.assertRaises(ValueError):
            MlTcaBacktesterConfig(signal_threshold=-0.01)

    def test_negative_cost_rejected(self):
        # A negative cost would pay the strategy to churn.
        with self.assertRaises(ValueError):
            MlTcaBacktesterConfig(bps_cost_half_turn=-100.0)

    def test_exit_threshold_bounds_validated(self):
        with self.assertRaises(ValueError):
            MlTcaBacktesterConfig(signal_threshold=0.01, exit_threshold=0.02)
        with self.assertRaises(ValueError):
            MlTcaBacktesterConfig(signal_threshold=0.01, exit_threshold=-0.001)

    def test_buy_hold_spread_reduces_turnover(self):
        # Novy-Marx & Velikov (2016): holding positions you would not actively
        # re-enter is the most effective simple cost mitigation.
        preds = np.array([0.02, 0.005, 0.02, 0.005, 0.02])
        actuals = np.zeros(5)

        stateless = MlTcaBacktester(
            MlTcaBacktesterConfig(bps_cost_half_turn=100.0, signal_threshold=0.01)
        ).process(preds, actuals)
        self.assertEqual(stateless["Positions Series"], [1.0, 0.0, 1.0, 0.0, 1.0])
        self.assertEqual(stateless["Total Turnover (Units)"], 6.0)

        hysteresis = MlTcaBacktester(
            MlTcaBacktesterConfig(
                bps_cost_half_turn=100.0, signal_threshold=0.01, exit_threshold=0.002
            )
        ).process(preds, actuals)
        self.assertEqual(hysteresis["Positions Series"], [1.0, 1.0, 1.0, 1.0, 1.0])
        self.assertEqual(hysteresis["Total Turnover (Units)"], 2.0)
        self.assertGreater(hysteresis["Total Net Return"], stateless["Total Net Return"])

    def test_exit_threshold_equal_to_entry_matches_stateless(self):
        preds = np.array([0.02, 0.005, -0.02, -0.004, 0.0])
        actuals = np.array([0.01, -0.01, 0.02, 0.0, 0.03])
        base = MlTcaBacktester(
            MlTcaBacktesterConfig(bps_cost_half_turn=10.0, signal_threshold=0.01)
        ).process(preds, actuals)
        same = MlTcaBacktester(
            MlTcaBacktesterConfig(
                bps_cost_half_turn=10.0, signal_threshold=0.01, exit_threshold=0.01
            )
        ).process(preds, actuals)
        self.assertEqual(base["Positions Series"], same["Positions Series"])
        self.assertEqual(base["Total Turnover (Units)"], same["Total Turnover (Units)"])

    def test_hysteresis_allows_direct_flip(self):
        # A conviction reversal must still flip, not stall in the hold band.
        preds = np.array([0.02, -0.02])
        actuals = np.zeros(2)
        res = MlTcaBacktester(
            MlTcaBacktesterConfig(
                bps_cost_half_turn=100.0, signal_threshold=0.01, exit_threshold=0.001
            )
        ).process(preds, actuals)
        self.assertEqual(res["Positions Series"], [1.0, -1.0])
        self.assertEqual(res["Total Turnover (Units)"], 4.0)  # 1 entry + 2 flip + 1 exit

    def test_nan_and_inf_inputs_rejected(self):
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        tester = MlTcaBacktester(config)
        with self.assertRaises(ValueError):
            tester.process(np.array([np.nan, 0.02]), np.array([0.01, 0.01]))
        with self.assertRaises(ValueError):
            tester.process(np.array([0.02, 0.02]), np.array([0.01, np.inf]))

    def test_shape_validation(self):
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        tester = MlTcaBacktester(config)
        with self.assertRaises(ValueError):
            tester.process(np.array([0.02, 0.02]), np.array([0.01]))
        with self.assertRaises(ValueError):
            tester.process(np.array([[0.02, 0.02]]), np.array([[0.01, 0.01]]))

    def test_accepts_plain_python_sequences(self):
        config = MlTcaBacktesterConfig(bps_cost_half_turn=100.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process([0.02, 0.02], [0.10, 0.10])
        self.assertEqual(res["Total Turnover (Units)"], 2.0)

    def test_empty_input_returns_zeroed_metrics(self):
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.01)
        res = MlTcaBacktester(config).process(np.array([]), np.array([]))

        self.assertEqual(res["Total Gross Return"], 0.0)
        self.assertEqual(res["Total Net Return"], 0.0)
        self.assertEqual(res["Total Turnover (Units)"], 0.0)
        self.assertEqual(res["Total Trade Count"], 0)
        self.assertEqual(res["Net Returns Series"], [])

    def test_capital_wipeout_reported_as_total_loss(self):
        # Costs exceeding 100% in a period must not sign-flip through np.prod.
        preds = np.array([0.05, -0.05])
        actuals = np.zeros(2)
        config = MlTcaBacktesterConfig(
            bps_cost_half_turn=6000.0, signal_threshold=0.01, liquidate_at_end=False
        )
        res = MlTcaBacktester(config).process(preds, actuals)

        self.assertEqual(res["Net Returns Series"], [-0.6, -1.2])
        self.assertEqual(res["Total Net Return"], -1.0)


if __name__ == '__main__':
    unittest.main()
