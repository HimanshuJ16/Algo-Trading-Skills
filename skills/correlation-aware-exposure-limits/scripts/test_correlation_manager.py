"""
Unit tests for correlation-aware-exposure-limits skill.

Tests:
1. Pearson correlation matrix calculation.
2. Hierarchical correlation cluster grouping.
3. Cluster exposure validation & limit breach rejection.
"""
import unittest
from correlation_manager import (
    CorrelationExposureManager,
    CorrelationLimitBreachError,
    OrderValidationResult,
)


class TestCorrelationExposureManager(unittest.TestCase):

    def setUp(self):
        self.manager = CorrelationExposureManager(
            correlation_threshold=0.70, max_cluster_exposure_pct=0.30
        )

        # Highly correlated Tech stocks (NVDA & AMD)
        nvda_ret = [0.02, 0.03, -0.01, 0.04, -0.02, 0.03] * 5
        amd_ret = [0.018, 0.028, -0.011, 0.038, -0.019, 0.029] * 5
        # Uncorrelated Gold asset (GLD)
        gld_ret = [-0.005, 0.002, 0.01, -0.004, 0.008, -0.002] * 5

        self.returns_dict = {"NVDA": nvda_ret, "AMD": amd_ret, "GLD": gld_ret}

    def test_correlation_matrix_and_clustering(self):
        clusters = self.manager.form_clusters(self.returns_dict)

        self.assertEqual(len(clusters), 2)
        # NVDA and AMD should be grouped together into 1 cluster
        tech_cluster = [c for c in clusters if "NVDA" in c][0]
        self.assertIn("AMD", tech_cluster)
        self.assertNotIn("GLD", tech_cluster)

    def test_cluster_exposure_limit_breach(self):
        nav = 100000.0
        current_positions = {"NVDA": 100.0}  # 100 * 200 = $20,000 USD (20% NAV)
        current_prices = {"NVDA": 200.0, "AMD": 100.0, "GLD": 180.0}

        # Proposed AMD order of $15,000 would push tech cluster (NVDA + AMD) exposure to $35,000 (35% NAV > 30% limit)
        res = self.manager.validate_order_exposure(
            symbol="AMD",
            proposed_value_usd=15000.0,
            current_positions=current_positions,
            current_prices=current_prices,
            returns_dict=self.returns_dict,
            portfolio_nav=nav,
        )

        self.assertFalse(res.approved)
        self.assertIn("CORRELATION CLUSTER BREACH", res.rejection_reason)
        self.assertAlmostEqual(res.projected_cluster_exposure_pct, 0.35, delta=0.01)

    def test_order_within_cluster_limit_approved(self):
        nav = 100000.0
        current_positions = {"NVDA": 50.0}  # $10,000 USD (10% NAV)
        current_prices = {"NVDA": 200.0, "AMD": 100.0}

        # Proposed AMD order of $10,000 pushes tech cluster to $20,000 (20% NAV <= 30% limit)
        res = self.manager.validate_order_exposure(
            symbol="AMD",
            proposed_value_usd=10000.0,
            current_positions=current_positions,
            current_prices=current_prices,
            returns_dict=self.returns_dict,
            portfolio_nav=nav,
        )

        self.assertTrue(res.approved)
        self.assertAlmostEqual(res.projected_cluster_exposure_pct, 0.20, delta=0.01)


if __name__ == "__main__":
    unittest.main()
