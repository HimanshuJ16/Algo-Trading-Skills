import unittest
from cold_start_handler import ColdStartHandler, InstrumentStatus

class TestColdStartHandler(unittest.TestCase):

    def setUp(self):
        self.handler = ColdStartHandler(warmup_period_days=30, base_max_position_pct=1.0)

    def test_zero_observations(self):
        # 0 days of history -> 100% peer prior
        status = self.handler.process_instrument(
            symbol="NEW_IPO", n_obs=0, observed_volatility=0.0, peer_prior_volatility=0.25
        )
        self.assertTrue(status.is_probationary)
        self.assertEqual(status.confidence_weight, 0.0)
        self.assertEqual(status.estimated_volatility, 0.25)
        self.assertEqual(status.max_position_cap_pct, 0.0)

    def test_partial_probation_shrinkage(self):
        # 15 days of history (50% weight)
        # Observed vol = 0.80, Peer prior vol = 0.20
        # Expected vol = 0.5 * 0.80 + 0.5 * 0.20 = 0.50
        status = self.handler.process_instrument(
            symbol="MID_IPO", n_obs=15, observed_volatility=0.80, peer_prior_volatility=0.20
        )
        self.assertTrue(status.is_probationary)
        self.assertEqual(status.confidence_weight, 0.5)
        self.assertEqual(status.estimated_volatility, 0.50)
        self.assertEqual(status.max_position_cap_pct, 0.5)

    def test_graduated_instrument(self):
        # 30+ days of history -> 100% observed vol
        status = self.handler.process_instrument(
            symbol="MATURE", n_obs=45, observed_volatility=0.18, peer_prior_volatility=0.25
        )
        self.assertFalse(status.is_probationary)
        self.assertEqual(status.confidence_weight, 1.0)
        self.assertEqual(status.estimated_volatility, 0.18)
        self.assertEqual(status.max_position_cap_pct, 1.0)

if __name__ == '__main__':
    unittest.main()
