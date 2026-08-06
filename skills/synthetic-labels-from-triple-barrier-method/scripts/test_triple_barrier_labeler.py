import unittest
import pandas as pd
import numpy as np
from triple_barrier_labeler import (
    Config, Engine, TripleBarrierLabeler,
    TripleBarrierLabelerEngine, BarrierTouched
)

class TestTripleBarrierLabelerLegacy(unittest.TestCase):
    def test_init(self):
        obj = TripleBarrierLabeler(Config())
        self.assertIsNotNone(obj)

    def test_process(self):
        obj = TripleBarrierLabeler(Config())
        self.assertTrue(obj.process())

    def test_engine_legacy(self):
        config = Config(name="test")
        eng = Engine(config)
        self.assertEqual(eng.config.name, "test")
        self.assertTrue(eng.run())

class TestTripleBarrierLabelerEngine(unittest.TestCase):
    def setUp(self):
        self.labeler = TripleBarrierLabelerEngine(
            pt_mult=1.0,
            sl_mult=1.0,
            vertical_bars=5,
            volatility_span=10
        )

    def test_generate_labels_take_profit(self):
        # Sharp price spike triggers take-profit
        prices = pd.Series([100.0, 101.0, 108.0, 109.0, 110.0, 111.0, 112.0])
        df_labels = self.labeler.generate_labels(prices)
        self.assertFalse(df_labels.empty)
        # First entry at index 0 should touch take profit (+1)
        self.assertEqual(df_labels.iloc[0]["barrier_touched"], BarrierTouched.TAKE_PROFIT.value)

    def test_generate_labels_stop_loss(self):
        # Sharp price drop triggers stop-loss
        prices = pd.Series([100.0, 99.0, 91.0, 90.0, 89.0, 88.0, 87.0])
        df_labels = self.labeler.generate_labels(prices)
        self.assertEqual(df_labels.iloc[0]["barrier_touched"], BarrierTouched.STOP_LOSS.value)

    def test_generate_labels_vertical_timeout(self):
        # Flat price movement reaches vertical expiration timeout
        prices = pd.Series([100.0, 100.01, 100.02, 100.01, 100.0, 100.01, 100.02])
        df_labels = self.labeler.generate_labels(prices)
        self.assertEqual(df_labels.iloc[0]["barrier_touched"], BarrierTouched.VERTICAL_TIMEOUT.value)

if __name__ == "__main__":
    unittest.main()
