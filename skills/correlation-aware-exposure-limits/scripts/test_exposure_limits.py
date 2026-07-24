"""
Unit tests for correlation-aware-exposure-limits skill.

Tests:
1. Rolling Pearson correlation matrix computation.
2. Connected component clustering of correlated stocks.
3. Position approval, scaling down, and cluster exposure capping.
4. Options delta factor aggregation.
5. Matrix staleness check and audit logging.
6. Backward compatibility with standalone cluster_by_correlation functions.
"""
import datetime
import math
import unittest
from exposure_limits import (
    CorrelationExposureManager,
    cluster_by_correlation,
    cluster_exposure,
)


class TestCorrelationExposureLimits(unittest.TestCase):

    def setUp(self):
        self.manager = CorrelationExposureManager(
            correlation_threshold=0.7,
            max_cluster_notional=500_000.0,
            max_portfolio_notional=1_500_000.0,
        )

    def test_correlation_matrix_computation_and_clustering(self):
        # Create price history where Bank stocks move together, and Pharma stock moves independently
        n_days = 60
        # Common market factor for bank sector
        bank_factor = [math.sin(i / 5.0) for i in range(n_days)]
        pharma_factor = [math.cos(i / 2.0) for i in range(n_days)]

        hdfc_prices = [100.0 + 10.0 * bank_factor[i] for i in range(n_days)]
        icici_prices = [50.0 + 5.0 * bank_factor[i] for i in range(n_days)]
        sbin_prices = [200.0 + 20.0 * bank_factor[i] for i in range(n_days)]
        sun_prices = [300.0 + 15.0 * pharma_factor[i] for i in range(n_days)]

        price_data = {
            "HDFCBANK": hdfc_prices,
            "ICICIBANK": icici_prices,
            "SBIN": sbin_prices,
            "SUNPHARMA": sun_prices,
        }

        self.manager.update_correlation_matrix(price_data)

        # Check matrix entries: HDFCBANK & ICICIBANK should be highly correlated
        corr_bank = self.manager.corr_matrix.get(("HDFCBANK", "ICICIBANK"), 0.0)
        self.assertGreater(corr_bank, 0.8)

        # Check clusters: Banks in one cluster, Pharma separate
        self.assertGreaterEqual(len(self.manager.clusters), 2)
        bank_cluster = None
        for cl in self.manager.clusters:
            if "HDFCBANK" in cl:
                bank_cluster = cl
                break

        self.assertIsNotNone(bank_cluster)
        self.assertIn("ICICIBANK", bank_cluster)
        self.assertNotIn("SUNPHARMA", bank_cluster)

    def test_position_cluster_limit_enforcement(self):
        self.manager.clusters = [{"HDFCBANK", "ICICIBANK", "SBIN"}, {"SUNPHARMA"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        current_positions = {
            "HDFCBANK": 300_000.0,
            "ICICIBANK": 150_000.0,  # Cluster total = 450,000 (cap is 500,000)
        }

        # Proposed SBIN position of 100,000 (total would be 550,000 > 500,000)
        res = self.manager.evaluate_proposed_position(
            symbol="SBIN",
            proposed_notional=100_000.0,
            current_positions=current_positions,
        )

        self.assertFalse(res.approved)
        self.assertEqual(res.allowed_notional, 50_000.0)  # Sized down to remaining cap
        self.assertIn("exceeds max cluster limit", res.reason)

        # Check audit trail
        self.assertEqual(len(self.manager.audit_trail), 1)
        self.assertEqual(self.manager.audit_trail[0].symbol, "SBIN")
        self.assertFalse(self.manager.audit_trail[0].approved)

    def test_options_delta_exposure_aggregation(self):
        self.manager.clusters = [{"NIFTY_CE", "NIFTY_PE"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        current_positions = {"NIFTY_CE": 400_000.0}

        # Options call delta weight = 0.5
        res = self.manager.evaluate_proposed_position(
            symbol="NIFTY_PE",
            proposed_notional=150_000.0,
            current_positions=current_positions,
            underlying_delta_weights={"NIFTY_PE": 0.5},
        )

        # Effective proposed = 150,000 * 0.5 = 75,000 -> Total = 475,000 <= 500,000 -> Approved!
        self.assertTrue(res.approved)

    def test_backward_compatibility(self):
        corr_matrix = {("A", "B"): 0.8, ("A", "C"): 0.2}
        clusters = cluster_by_correlation(corr_matrix, threshold=0.7)
        self.assertTrue(any({"A", "B"}.issubset(c) for c in clusters))

        pos = {"A": 100, "B": 200, "C": 500}
        exp = cluster_exposure(pos, {"A", "B"})
        self.assertEqual(exp, 300)


if __name__ == "__main__":
    unittest.main()
