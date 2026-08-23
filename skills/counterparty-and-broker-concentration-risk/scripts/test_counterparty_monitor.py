import unittest
from counterparty_monitor import (
    CounterpartyConcentrationMonitor, BrokerProfile, RoutingDecision
)

class TestCounterpartyConcentrationMonitor(unittest.TestCase):

    def setUp(self):
        self.b1 = BrokerProfile(
            broker_id="PB_ALPHA", name="Alpha Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=80.0, max_cds_bps_threshold=250.0,
            current_cash=150_000, current_margin=100_000, current_positions_value=50_000 # $300k (50% NAV)
        )
        self.b2 = BrokerProfile(
            broker_id="PB_BETA", name="Beta Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=90.0, max_cds_bps_threshold=250.0,
            current_cash=100_000, current_margin=50_000, current_positions_value=0.0    # $150k (25% NAV)
        )
        self.b3 = BrokerProfile(
            broker_id="PB_GAMMA", name="Gamma Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=300.0, max_cds_bps_threshold=250.0, # Distressed CDS!
            current_cash=150_000, current_margin=0.0, current_positions_value=0.0       # $150k (25% NAV)
        )
        # Total NAV = $300k + $150k + $150k = $600k
        self.monitor = CounterpartyConcentrationMonitor([self.b1, self.b2, self.b3])

    @staticmethod
    def _profile(broker_id: str, **overrides) -> BrokerProfile:
        defaults = dict(
            broker_id=broker_id, name=f"{broker_id} Prime", max_nav_pct_limit=0.35,
            cds_spread_bps=80.0, max_cds_bps_threshold=250.0,
            current_cash=100_000, current_margin=0.0, current_positions_value=0.0,
        )
        defaults.update(overrides)
        return BrokerProfile(**defaults)

    def test_approved_primary_routing(self):
        # Route small order $5,000 to PB_BETA ($150k + $5k = $155k / $600k = 25.8% <= 35%)
        decision = self.monitor.route_order("PB_BETA", proposed_order_value=5000.0)
        self.assertFalse(decision.is_rerouted)
        self.assertFalse(decision.blocked)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_failover_routing_on_limit_breach(self):
        # Attempt to route $10,000 to PB_ALPHA ($300k + $10k = $310k / $600k = 51.6% > 35%)
        # Breaches PB_ALPHA limit -> Re-routes to PB_BETA ($150k + $10k = $160k / $600k = 26.6% <= 35%)
        decision = self.monitor.route_order("PB_ALPHA", proposed_order_value=10000.0)
        self.assertTrue(decision.is_rerouted)
        self.assertFalse(decision.blocked)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_cds_distress_blocks_routing(self):
        # Attempt to route to PB_GAMMA (CDS 300 bps > 250 bps threshold)
        decision = self.monitor.route_order("PB_GAMMA", proposed_order_value=5000.0)
        # PB_GAMMA is distressed, so it re-routes to PB_BETA
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_broker_id, "PB_BETA")

    def test_compute_hhi(self):
        # Total = 600k. Weights: PB_ALPHA = 300k/600k = 0.5, PB_BETA = 150k/600k = 0.25, PB_GAMMA = 150k/600k = 0.25
        # HHI = 0.5^2 + 0.25^2 + 0.25^2 = 0.25 + 0.0625 + 0.0625 = 0.375
        hhi = self.monitor.compute_hhi()
        self.assertEqual(hhi, 0.375)

    # --- Capital-protection paths (previously untested) -----------------------

    def test_all_brokers_noncompliant_blocks_execution(self):
        # ALPHA at 50% NAV breaches; BETA distressed via CDS; GAMMA distressed
        # via CDS -> no compliant failover -> decision MUST be blocked=True and
        # must not name a routable alternative.
        self.b2.cds_spread_bps = 400.0
        decision = self.monitor.route_order("PB_ALPHA", proposed_order_value=10_000.0)
        self.assertTrue(decision.blocked)
        self.assertFalse(decision.is_rerouted)
        self.assertIn("blocked", decision.reason.lower())

    def test_zero_nav_fails_closed(self):
        # An empty book cannot be concentration-assessed: the old code
        # substituted max(NAV, order) as the denominator; the fix blocks.
        empty = CounterpartyConcentrationMonitor([self._profile("PB_SOLO", current_cash=0.0)])
        decision = empty.route_order("PB_SOLO", proposed_order_value=50_000.0)
        self.assertTrue(decision.blocked)
        self.assertIn("NAV", decision.reason)

    def test_unknown_broker_exposure_raises(self):
        # A typo'd broker id must not silently return 0.0 exposure.
        with self.assertRaises(ValueError):
            self.monitor.calculate_total_broker_exposure("PB_TYPO")
        with self.assertRaises(ValueError):
            self.monitor.route_order("PB_TYPO", proposed_order_value=1000.0)

    # --- Input validation: bad data must be loud, never approving -------------

    def test_non_finite_inputs_rejected(self):
        with self.assertRaises(ValueError):
            self.monitor.route_order("PB_BETA", proposed_order_value=float("nan"))
        with self.assertRaises(ValueError):
            self.monitor.route_order("PB_BETA", proposed_order_value=-5_000.0)
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", cds_spread_bps=float("nan"))
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", current_cash=float("inf"))

    def test_profile_parameter_validation(self):
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", max_nav_pct_limit=1.5)   # not a fraction
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", max_nav_pct_limit=-0.2)
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", max_nav_pct_limit=0.0)     # (0, 1], not [0, 1]
        with self.assertRaises(ValueError):
            self._profile("PB_BAD", cds_spread_bps=-10.0)
        with self.assertRaises(ValueError):
            self._profile("")                                  # empty broker id

    def test_negative_positions_value_allowed_but_finite(self):
        # Short market value is a legitimate signed balance.
        m = CounterpartyConcentrationMonitor(
            [self._profile("PB_A", current_cash=200_000, current_positions_value=-50_000)]
        )
        self.assertEqual(m.calculate_total_broker_exposure("PB_A"), 150_000.0)

    # --- Failover determinism and update semantics -----------------------------

    def test_failover_selects_lowest_projected_weight(self):
        # Two compliant secondaries with different exposure: BETA ($150k) and
        # a fresh PB_DELTA ($0). Both fit; DELTA has the lowest projected
        # weight and must win. Ties break on broker_id (deterministic).
        self.monitor.register_broker(self._profile("PB_DELTA", current_cash=0.0))
        decision = self.monitor.route_order("PB_ALPHA", proposed_order_value=10_000.0)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_broker_id, "PB_DELTA")

    def test_register_broker_overwrites_existing_profile(self):
        # Re-registration is the update mechanism (e.g. refreshed balances).
        self.monitor.register_broker(
            self._profile("PB_BETA", current_cash=50_000, current_margin=0.0)
        )
        self.assertEqual(self.monitor.calculate_total_broker_exposure("PB_BETA"), 50_000.0)

    def test_hhi_undefined_on_flat_book_raises(self):
        # Regression: compute_hhi used to return 0.0 - the value meaning
        # "perfectly diversified" - when the denominator was undefined, so a
        # downstream `hhi > threshold` alert silently passed. Fail loud.
        empty = CounterpartyConcentrationMonitor([self._profile("PB_X", current_cash=0.0)])
        with self.assertRaises(ValueError):
            empty.compute_hhi()

    # --- Signed balances must not read as concentration headroom -------------

    def test_negative_exposure_broker_is_not_a_failover_haven(self):
        # Regression: with signed exposures, PB_DEBIT's weight was
        # (-100k + 10k)/100k = -0.90, which passed the 35% cap and sorted
        # lowest, so every failover dumped into the broker the fund already
        # owed $100k. Measured on magnitude the projected weight is 0.90 and
        # no broker is compliant -> blocked.
        m = CounterpartyConcentrationMonitor([
            self._profile("PB_LONG", current_cash=200_000),
            self._profile("PB_DEBIT", current_cash=-100_000),
        ])
        self.assertEqual(m.calculate_total_broker_exposure("PB_DEBIT"), -100_000.0)
        self.assertEqual(m.calculate_concentration_exposure("PB_DEBIT"), 100_000.0)
        decision = m.route_order("PB_LONG", proposed_order_value=10_000.0)
        self.assertTrue(decision.blocked)
        self.assertNotEqual(decision.selected_broker_id, "PB_DEBIT")

    def test_projected_weight_never_reported_negative(self):
        m = CounterpartyConcentrationMonitor([
            self._profile("PB_LONG", current_cash=200_000),
            self._profile("PB_DEBIT", current_cash=-100_000),
        ])
        decision = m.route_order("PB_DEBIT", proposed_order_value=10_000.0)
        # |(-100k + 10k)| / 100k NAV = 90.0%, not -90.0%.
        self.assertEqual(decision.projected_nav_pct, 90.0)

    def test_hhi_bounded_with_negative_balances(self):
        # Regression: signed weights (2.0 and -1.0) gave HHI = 5.0, outside
        # the [1/n, 1] range the index is defined on. Magnitude shares
        # (200/300, 100/300) give 0.5556.
        m = CounterpartyConcentrationMonitor([
            self._profile("PB_LONG", current_cash=200_000),
            self._profile("PB_DEBIT", current_cash=-100_000),
        ])
        hhi = m.compute_hhi()
        self.assertAlmostEqual(hhi, 0.5556, places=4)
        self.assertLessEqual(hhi, 1.0)
        self.assertGreaterEqual(hhi, 0.5)   # >= 1/n


if __name__ == '__main__':
    unittest.main()
