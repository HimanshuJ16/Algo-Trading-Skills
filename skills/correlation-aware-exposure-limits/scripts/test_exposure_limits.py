"""
Unit tests for correlation-aware-exposure-limits skill.

Tests:
1. Rolling Pearson correlation matrix computation.
2. Connected component clustering of correlated stocks.
3. Position approval, scaling down, and cluster exposure capping.
4. Options delta factor aggregation.
5. Fail-closed behavior on missing / stale correlation matrices.
6. Input validation (NaN / non-positive prices, bad notionals, bad params).
7. Exact post-trade exposure: risk-reducing orders are not vetoed.
8. Return-series alignment for histories of different lengths.
9. Sector-override clustering and backward compatibility functions.
"""
import datetime
import math
import unittest
from exposure_limits import (
    CorrelationExposureManager,
    CorrelationMatrixUnavailableError,
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

    @staticmethod
    def _bank_pharma_history():
        n_days = 60
        bank_factor = [math.sin(i / 5.0) for i in range(n_days)]
        pharma_factor = [math.cos(i / 2.0) for i in range(n_days)]
        return {
            "HDFCBANK": [100.0 + 10.0 * f for f in bank_factor],
            "ICICIBANK": [50.0 + 5.0 * f for f in bank_factor],
            "SBIN": [200.0 + 20.0 * f for f in bank_factor],
            "SUNPHARMA": [300.0 + 15.0 * f for f in pharma_factor],
        }

    def test_correlation_matrix_computation_and_clustering(self):
        self.manager.update_correlation_matrix(self._bank_pharma_history())

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

    # --- Fail-closed behavior -------------------------------------------------

    def test_missing_matrix_fails_closed(self):
        # A manager with no correlation matrix must refuse to approve orders:
        # every symbol would otherwise be a singleton and cluster limits
        # would be silently bypassed.
        with self.assertRaises(CorrelationMatrixUnavailableError):
            self.manager.evaluate_proposed_position(
                symbol="SBIN", proposed_notional=100.0, current_positions={}
            )

    def test_stale_matrix_block_policy(self):
        self.manager.clusters = [{"HDFCBANK", "ICICIBANK"}]
        self.manager.matrix_timestamp = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        )
        # default policy 'warn' still evaluates
        res = self.manager.evaluate_proposed_position(
            symbol="HDFCBANK", proposed_notional=1_000.0, current_positions={}
        )
        self.assertTrue(res.approved)

        blocking = CorrelationExposureManager(
            max_cluster_notional=500_000.0, stale_matrix_policy="block"
        )
        blocking.clusters = [{"HDFCBANK"}]
        blocking.matrix_timestamp = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        )
        with self.assertRaises(CorrelationMatrixUnavailableError):
            blocking.evaluate_proposed_position(
                symbol="HDFCBANK", proposed_notional=1_000.0, current_positions={}
            )

    # --- Input validation -----------------------------------------------------

    def test_constructor_parameter_validation(self):
        with self.assertRaises(ValueError):
            CorrelationExposureManager(correlation_threshold=1.5)
        with self.assertRaises(ValueError):
            CorrelationExposureManager(max_cluster_notional=0)
        with self.assertRaises(ValueError):
            CorrelationExposureManager(max_portfolio_notional=-1.0)
        with self.assertRaises(ValueError):
            CorrelationExposureManager(max_matrix_age_days=0)
        with self.assertRaises(ValueError):
            CorrelationExposureManager(stale_matrix_policy="ignore")

    def test_invalid_price_history_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.update_correlation_matrix({})
        with self.assertRaises(ValueError):
            self.manager.update_correlation_matrix({"A": [100.0]})          # < 2 prices
        with self.assertRaises(ValueError):
            self.manager.update_correlation_matrix({"A": [100.0, 0.0]})     # zero price
        with self.assertRaises(ValueError):
            self.manager.update_correlation_matrix({"A": [100.0, float("nan")]})
        # naive timestamp would break staleness arithmetic later
        with self.assertRaises(ValueError):
            self.manager.update_correlation_matrix(
                {"A": [100.0, 101.0, 102.0]},
                timestamp=datetime.datetime(2025, 1, 1),
            )

    def test_invalid_notional_and_weights_rejected(self):
        self.manager.update_correlation_matrix(self._bank_pharma_history())
        with self.assertRaises(ValueError):
            self.manager.evaluate_proposed_position(
                symbol="SBIN", proposed_notional=float("nan"), current_positions={}
            )
        with self.assertRaises(ValueError):
            self.manager.evaluate_proposed_position(
                symbol="SBIN", proposed_notional=100.0,
                current_positions={"SBIN": float("inf")},
            )
        with self.assertRaises(ValueError):
            self.manager.evaluate_proposed_position(
                symbol="SBIN", proposed_notional=100.0, current_positions={},
                underlying_delta_weights={"SBIN": 1.5},
            )

    # --- Exact post-trade exposure --------------------------------------------

    def test_reduction_order_not_vetoed(self):
        # Cluster at 495k/500k; reducing an existing 480k position by 100k
        # lowers exposure to 395k and must be approved. (The old abs-increment
        # model computed 495k + 100k and vetoed risk-REDUCING orders.)
        self.manager.clusters = [{"HDFCBANK", "ICICIBANK"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        res = self.manager.evaluate_proposed_position(
            symbol="HDFCBANK",
            proposed_notional=-100_000.0,
            current_positions={"HDFCBANK": 480_000.0, "ICICIBANK": 15_000.0},
        )
        self.assertTrue(res.approved)

    def test_partial_reduction_at_breach_approved_but_flagged(self):
        self.manager.clusters = [{"HDFCBANK"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        # Cluster already over cap (600k > 500k)
        res = self.manager.evaluate_proposed_position(
            symbol="HDFCBANK",
            proposed_notional=-50_000.0,
            current_positions={"HDFCBANK": 600_000.0},
        )
        self.assertTrue(res.approved)
        self.assertIn("risk-reducing", res.reason)

        # An increase at breach is still vetoed
        res2 = self.manager.evaluate_proposed_position(
            symbol="HDFCBANK",
            proposed_notional=10_000.0,
            current_positions={"HDFCBANK": 600_000.0},
        )
        self.assertFalse(res2.approved)

    def test_portfolio_cap_reduction_allowed(self):
        self.manager.clusters = [{"A"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        res = self.manager.evaluate_proposed_position(
            symbol="A",
            proposed_notional=-200_000.0,
            current_positions={"A": 1_600_000.0},  # over the 1.5M portfolio cap
        )
        self.assertTrue(res.approved)

    def test_delta_weights_apply_to_existing_positions(self):
        # Symmetric delta treatment: the existing CE position must also be
        # delta-adjusted (400k * 0.5 = 200k), not just the proposed leg.
        # Old behavior counted the existing position at full 400k.
        self.manager.clusters = [{"NIFTY_CE", "NIFTY_PE"}]
        self.manager.matrix_timestamp = datetime.datetime.now(datetime.timezone.utc)

        res = self.manager.evaluate_proposed_position(
            symbol="NIFTY_PE",
            proposed_notional=250_000.0,
            current_positions={"NIFTY_CE": 400_000.0},
            underlying_delta_weights={"NIFTY_CE": 0.5, "NIFTY_PE": 0.5},
        )
        # Effective cluster: 200k + 125k = 325k <= 500k
        self.assertTrue(res.approved)

    # --- Correlation estimation edge cases ------------------------------------

    def test_different_length_histories_align_on_recent_returns(self):
        # A has 60 factor-driven returns; B has 29 returns, the last 25 of
        # which follow the same factor moves that A made in ITS last 25
        # returns. Correlation must use the most recent overlapping returns,
        # so B lands in A's cluster. (Truncating at the oldest end compared
        # returns from different periods and missed the cluster.)
        shared = [math.sin(i / 3.0) * 0.01 for i in range(25)]
        a_early = [math.cos(i / 2.0) * 0.01 for i in range(34)]

        def to_prices(start, rets):
            prices = [start]
            for r in rets:
                prices.append(prices[-1] * (1.0 + r))
            return prices

        history = {
            "LONG": to_prices(100.0, a_early + shared),
            "NEW": to_prices(200.0, [0.0, 0.0, 0.0, 0.0] + shared),
        }
        self.manager.update_correlation_matrix(history)

        corr = self.manager.corr_matrix[("LONG", "NEW")]
        self.assertGreaterEqual(corr, 0.7)
        clusters = [c for c in self.manager.clusters if "LONG" in c]
        self.assertIn("NEW", clusters[0])

    def test_zero_variance_series_is_uncorrelated_not_crashing(self):
        # A pegged/constant price series has undefined correlation; it must
        # not crash and must not be clustered with a volatile name.
        factor = [math.sin(i / 5.0) for i in range(60)]
        history = {
            "MOVES": [100.0 + 10.0 * f for f in factor],
            "PEGGED": [200.0] * 60,
        }
        self.manager.update_correlation_matrix(history)
        self.assertEqual(self.manager.corr_matrix[("MOVES", "PEGGED")], 0.0)
        for cl in self.manager.clusters:
            self.assertNotEqual(cl, {"MOVES", "PEGGED"})

    def test_unknown_symbol_treated_as_own_cluster(self):
        self.manager.update_correlation_matrix(self._bank_pharma_history())
        res = self.manager.evaluate_proposed_position(
            symbol="NEWLISTING", proposed_notional=1_000.0, current_positions={}
        )
        self.assertTrue(res.approved)
        self.assertEqual(res.cluster_id, "NEWLISTING")

    # --- Sector override and clustering semantics ------------------------------

    def test_sector_override_clustering(self):
        # X and Y are uncorrelated by returns but share a sector label, so
        # they form one risk pocket; Z (different sector) stays separate.
        n = 60
        x_factor = [math.sin(i / 3.0) for i in range(n)]
        y_factor = [math.cos(i / 7.0) for i in range(n)]
        z_factor = [math.sin(i / 11.0) for i in range(n)]
        manager = CorrelationExposureManager(
            sector_mapping={"X": "TECH", "Y": "TECH", "Z": "PHARMA"}
        )
        manager.update_correlation_matrix({
            "X": [100.0 + 5.0 * f for f in x_factor],
            "Y": [100.0 + 5.0 * f for f in y_factor],
            "Z": [100.0 + 5.0 * f for f in z_factor],
        })
        xy_cluster = next(c for c in manager.clusters if "X" in c)
        self.assertIn("Y", xy_cluster)
        self.assertNotIn("Z", xy_cluster)

    def test_none_sector_labels_do_not_cluster(self):
        # Symbols explicitly mapped to a None sector must not be forced
        # together (previously None == None evaluated True).
        n = 60
        x_factor = [math.sin(i / 3.0) for i in range(n)]
        y_factor = [math.cos(i / 7.0) for i in range(n)]
        manager = CorrelationExposureManager(
            sector_mapping={"X": None, "Y": None}
        )
        manager.update_correlation_matrix({
            "X": [100.0 + 5.0 * f for f in x_factor],
            "Y": [100.0 + 5.0 * f for f in y_factor],
        })
        x_cluster = next(c for c in manager.clusters if "X" in c)
        self.assertEqual(x_cluster, {"X"})

    def test_cluster_by_correlation_is_transitive(self):
        # A-B and B-C at/above threshold with A-C below: connected components
        # put all three in one cluster (the old greedy pass split them).
        corr_matrix = {("A", "B"): 0.8, ("B", "C"): 0.75, ("A", "C"): 0.2}
        clusters = cluster_by_correlation(corr_matrix, threshold=0.7)
        self.assertIn({"A", "B", "C"}, [set(c) for c in clusters])


if __name__ == "__main__":
    unittest.main()
