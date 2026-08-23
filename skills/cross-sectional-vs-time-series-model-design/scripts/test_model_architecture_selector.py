import unittest

import numpy as np

from model_architecture_selector import (
    ModelArchitectureSelectorEngine, ArchitectureRecommendation, SignalTransformationResult
)


class TestArchitectureRecommendation(unittest.TestCase):

    def setUp(self):
        self.engine = ModelArchitectureSelectorEngine(target_volatility_annual=0.15)

    def test_architecture_recommendation(self):
        rec_cs = self.engine.recommend_architecture(
            universe_size=50, require_market_neutrality=True, is_single_asset_trend=False)
        self.assertIsInstance(rec_cs, ArchitectureRecommendation)
        self.assertEqual(rec_cs.selected_architecture, "CROSS_SECTIONAL")
        self.assertTrue(rec_cs.is_market_neutral_enforced)

        rec_ts = self.engine.recommend_architecture(
            universe_size=1, require_market_neutrality=False, is_single_asset_trend=True)
        self.assertEqual(rec_ts.selected_architecture, "TIME_SERIES")
        self.assertTrue(rec_ts.is_volatility_targeting_enforced)

    def test_wide_universe_without_neutrality_mandate_is_cross_sectional(self):
        rec = self.engine.recommend_architecture(
            universe_size=10, require_market_neutrality=False, is_single_asset_trend=False)
        self.assertEqual(rec.selected_architecture, "CROSS_SECTIONAL")

    def test_neutrality_over_degenerate_universe_raises(self):
        # Regression: previously recommended CROSS_SECTIONAL for K=1, an architecture
        # transform_cross_sectional then rejects outright.
        with self.assertRaises(ValueError):
            self.engine.recommend_architecture(
                universe_size=1, require_market_neutrality=True, is_single_asset_trend=False)

    def test_contradictory_mandate_raises(self):
        with self.assertRaises(ValueError):
            self.engine.recommend_architecture(
                universe_size=50, require_market_neutrality=True, is_single_asset_trend=True)

    def test_recommendation_does_not_claim_beta_neutrality(self):
        rec = self.engine.recommend_architecture(
            universe_size=50, require_market_neutrality=True, is_single_asset_trend=False)
        self.assertIn("does NOT imply beta", rec.rationale)


class TestCrossSectionalTransform(unittest.TestCase):

    def setUp(self):
        self.engine = ModelArchitectureSelectorEngine(target_volatility_annual=0.15)

    def test_cross_sectional_transformation(self):
        factors = np.array([10.0, 50.0, -20.0, 30.0, -10.0])
        res = self.engine.transform_cross_sectional(factors)

        self.assertIsInstance(res, SignalTransformationResult)
        self.assertEqual(res.architecture, "CROSS_SECTIONAL")
        self.assertAlmostEqual(res.net_exposure, 0.0, places=12)
        self.assertAlmostEqual(res.gross_exposure, 1.0, places=12)

    def test_weights_match_hand_computed_values(self):
        # Independently derived: mean([10,50,-20,30,-10]) = 12. Deviations are
        # [-2, 38, -32, 18, -22]; sum|dev| = 112. Weights = dev / 112.
        # std cancels out of the normalization, so no sigma appears here.
        factors = np.array([10.0, 50.0, -20.0, 30.0, -10.0])
        expected = np.array([-2.0, 38.0, -32.0, 18.0, -22.0]) / 112.0
        res = self.engine.transform_cross_sectional(factors)
        np.testing.assert_allclose(res.portfolio_weights, expected, rtol=0, atol=1e-15)

    def test_returned_weights_satisfy_dollar_neutrality_standard(self):
        # Regression: weights used to be rounded to 4dp, so the RETURNED array could
        # sum to ~4e-4 while net_exposure (computed pre-rounding) reported 0.0.
        # standards.md mandates |sum(w)| <= 1e-5.
        rng = np.random.default_rng(7)
        for _ in range(500):
            res = self.engine.transform_cross_sectional(rng.normal(0.0, 1.0, 13))
            self.assertLessEqual(abs(float(res.portfolio_weights.sum())), 1e-5)
            self.assertAlmostEqual(float(np.abs(res.portfolio_weights).sum()), 1.0, places=12)

    def test_reported_metrics_describe_returned_weights(self):
        rng = np.random.default_rng(11)
        res = self.engine.transform_cross_sectional(rng.normal(0.0, 1.0, 17))
        self.assertAlmostEqual(res.net_exposure, float(res.portfolio_weights.sum()), places=15)
        self.assertAlmostEqual(
            res.gross_exposure, float(np.abs(res.portfolio_weights).sum()), places=15)

    def test_zscore_weights_are_invariant_to_factor_scale(self):
        # std_cs cancels in the gross normalization, so a rescaled factor is identical.
        factors = np.array([10.0, 50.0, -20.0, 30.0, -10.0])
        base = self.engine.transform_cross_sectional(factors).portfolio_weights
        scaled = self.engine.transform_cross_sectional(factors * 1000.0).portfolio_weights
        np.testing.assert_allclose(base, scaled, rtol=0, atol=1e-15)

    def test_non_finite_input_raises_instead_of_emitting_nan_weights(self):
        # Regression: NaN/Inf used to propagate silently into portfolio_weights.
        for bad in (np.nan, np.inf, -np.inf):
            with self.assertRaises(ValueError):
                self.engine.transform_cross_sectional(np.array([1.0, 2.0, bad, 4.0]))

    def test_flat_cross_section_emits_flat_weights(self):
        res = self.engine.transform_cross_sectional(np.array([5.0, 5.0, 5.0]))
        np.testing.assert_allclose(res.portfolio_weights, np.zeros(3))
        np.testing.assert_allclose(res.standardized_z_scores, np.zeros(3))
        self.assertEqual(res.gross_exposure, 0.0)

    def test_single_asset_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.transform_cross_sectional(np.array([1.0]))

    def test_two_asset_universe_is_exactly_opposing(self):
        res = self.engine.transform_cross_sectional(np.array([1.0, 3.0]))
        np.testing.assert_allclose(res.portfolio_weights, np.array([-0.5, 0.5]))

    def test_unknown_weighting_scheme_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.transform_cross_sectional(np.array([1.0, 2.0]), weighting="softmax")


class TestWinsorization(unittest.TestCase):

    def test_sigma_winsorization_is_inert_for_small_universes(self):
        # For K assets the largest attainable |z| is (K-1)/sqrt(K) (population std),
        # which is 2.846 at K=10 -- below a 3.0 threshold. Documents the real limit.
        engine = ModelArchitectureSelectorEngine(windsorize_std=3.0)
        self.assertEqual(engine.max_inert_universe_size(), 10)
        for k in (5, 10):
            self.assertLess((k - 1) / np.sqrt(k), 3.0)
        self.assertGreater((11 - 1) / np.sqrt(11), 3.0)

        extreme = np.array([1.0, 2.0, 3.0, 4.0, 500.0])
        np.testing.assert_allclose(engine._windsorize(extreme), extreme)

    def test_max_inert_universe_size_matches_brute_force(self):
        # Closed form must agree with the defining inequality (K-1)/sqrt(K) <= c,
        # and must not iterate (a loop would hang for large thresholds).
        for c in (1.0, 2.0, 3.0, 3.5, 5.0, 10.0, 50.0):
            k = 2
            while (k - 1) / np.sqrt(k) <= c:
                k += 1
            self.assertEqual(ModelArchitectureSelectorEngine(windsorize_std=c).max_inert_universe_size(), k - 1)
        self.assertGreater(
            ModelArchitectureSelectorEngine(windsorize_std=1e6).max_inert_universe_size(), 0)

    def test_mad_winsorization_clips_what_sigma_cannot(self):
        engine = ModelArchitectureSelectorEngine(windsorize_std=3.0, winsorize_method="mad")
        extreme = np.array([1.0, 2.0, 3.0, 4.0, 500.0])
        # median = 3.0; |dev| = [2,1,0,1,497]; MAD = 1.0; scale = 1.4826;
        # upper bound = 3.0 + 3 * 1.4826 = 7.4478.
        clipped = engine._windsorize(extreme)
        self.assertAlmostEqual(float(clipped[-1]), 7.4478, places=4)
        np.testing.assert_allclose(clipped[:4], extreme[:4])

    def test_winsorize_leaves_flat_input_unchanged(self):
        # Regression: previously returned data - mean, silently recentring to zeros.
        engine = ModelArchitectureSelectorEngine()
        flat = np.array([5.0, 5.0, 5.0])
        np.testing.assert_allclose(engine._windsorize(flat), flat)

    def test_outlier_dominance_is_bounded_by_rank_weighting(self):
        # A single extreme value takes ~97% of gross book under z-score weighting
        # at K=5 (sigma winsorization cannot bind); rank weighting caps its share.
        engine = ModelArchitectureSelectorEngine()
        factors = np.array([1.0, 2.0, 3.0, 4.0, 5000.0])
        z_w = engine.transform_cross_sectional(factors, weighting="zscore").portfolio_weights
        r_w = engine.transform_cross_sectional(factors, weighting="rank").portfolio_weights
        self.assertGreater(abs(float(z_w[-1])), 0.49)
        self.assertLess(abs(float(r_w[-1])), 0.35)


class TestRankWeighting(unittest.TestCase):

    def setUp(self):
        self.engine = ModelArchitectureSelectorEngine()

    def test_rank_weights_match_amp_2013_formula(self):
        # AMP (2013) eq. 1: w_i proportional to rank(S_i) - mean(rank).
        # Ranks 0..4, mean 2 -> deviations [-2,-1,0,1,2], sum|dev| = 6.
        factors = np.array([10.0, 50.0, -20.0, 30.0, -10.0])
        # sorted order: -20 (rank 0), -10 (1), 10 (2), 30 (3), 50 (4)
        expected = np.array([2.0, 4.0, 0.0, 3.0, 1.0]) - 2.0
        expected = expected / 6.0
        res = self.engine.transform_cross_sectional(factors, weighting="rank")
        np.testing.assert_allclose(res.portfolio_weights, expected, rtol=0, atol=1e-15)
        self.assertAlmostEqual(res.net_exposure, 0.0, places=12)
        self.assertAlmostEqual(res.gross_exposure, 1.0, places=12)

    def test_rank_weighting_breaks_ties_symmetrically(self):
        # Tied factor values must receive identical weights, not an argsort-order split.
        res = self.engine.transform_cross_sectional(
            np.array([1.0, 5.0, 5.0, 9.0]), weighting="rank")
        self.assertAlmostEqual(float(res.portfolio_weights[1]), float(res.portfolio_weights[2]))
        self.assertAlmostEqual(res.net_exposure, 0.0, places=12)

    def test_rank_weighting_is_monotone_in_the_factor(self):
        rng = np.random.default_rng(3)
        factors = rng.normal(0.0, 1.0, 20)
        w = self.engine.transform_cross_sectional(factors, weighting="rank").portfolio_weights
        self.assertTrue(np.all(np.diff(w[np.argsort(factors)]) > 0))


class TestTimeSeriesTransform(unittest.TestCase):

    def setUp(self):
        self.engine = ModelArchitectureSelectorEngine(target_volatility_annual=0.15)
        # Deterministic history: previously np.random.normal with no seed.
        self.history = np.linspace(-0.02, 0.02, 50)

    def test_time_series_volatility_scaling(self):
        current_factor = 0.05  # above the history mean -> long

        # 30% realized vol -> 15/30 = 0.5x; 10% realized vol -> 15/10 = 1.5x.
        w_high_vol = self.engine.transform_time_series(
            self.history, current_factor, asset_realized_vol_annual=0.30)
        w_low_vol = self.engine.transform_time_series(
            self.history, current_factor, asset_realized_vol_annual=0.10)

        self.assertAlmostEqual(w_high_vol, 0.5, places=12)
        self.assertAlmostEqual(w_low_vol, 1.5, places=12)

    def test_negative_signal_flips_sign_but_not_magnitude(self):
        w = self.engine.transform_time_series(self.history, -0.05, asset_realized_vol_annual=0.30)
        self.assertAlmostEqual(w, -0.5, places=12)

    def test_leverage_cap_binds_for_very_low_vol(self):
        # 15% / 2% = 7.5x, capped at the configured 2.0x.
        w = self.engine.transform_time_series(self.history, 0.05, asset_realized_vol_annual=0.02)
        self.assertAlmostEqual(w, 2.0, places=12)

    def test_leverage_cap_is_configurable(self):
        # Raising the cap above the required 7.5x lets the full vol scalar through;
        # a cap below it still binds.
        loose = ModelArchitectureSelectorEngine(target_volatility_annual=0.15, max_leverage=10.0)
        self.assertAlmostEqual(
            loose.transform_time_series(self.history, 0.05, 0.02), 7.5, places=12)

        tight = ModelArchitectureSelectorEngine(target_volatility_annual=0.15, max_leverage=5.0)
        self.assertAlmostEqual(
            tight.transform_time_series(self.history, 0.05, 0.02), 5.0, places=12)

    def test_non_positive_volatility_raises(self):
        # Regression: vol <= 0 was floored to 0.01, producing MAXIMUM leverage from
        # nonsense input -- the most dangerous possible response to bad data.
        for bad_vol in (0.0, -0.5, np.nan, np.inf):
            with self.assertRaises(ValueError):
                self.engine.transform_time_series(self.history, 0.05, bad_vol)

    def test_insufficient_history_raises(self):
        # Regression: fewer than min_history observations used to fall back to
        # mean=0/std=1, sizing a full position off two data points.
        with self.assertRaises(ValueError):
            self.engine.transform_time_series(np.array([0.01, 0.02]), 0.03, 0.15)

    def test_non_finite_history_raises(self):
        history = np.append(self.history, np.nan)
        with self.assertRaises(ValueError):
            self.engine.transform_time_series(history, 0.05, 0.15)

    def test_non_finite_current_factor_raises(self):
        with self.assertRaises(ValueError):
            self.engine.transform_time_series(self.history, np.nan, 0.15)

    def test_flat_history_yields_zero_weight(self):
        # No dispersion -> no z-score -> no position, rather than an arbitrary sign.
        w = self.engine.transform_time_series(np.full(50, 0.01), 0.05, 0.15)
        self.assertEqual(w, 0.0)

    def test_factor_equal_to_history_mean_yields_zero_weight(self):
        w = self.engine.transform_time_series(self.history, float(np.mean(self.history)), 0.15)
        self.assertEqual(w, 0.0)


class TestEngineConfiguration(unittest.TestCase):

    def test_invalid_configuration_rejected(self):
        for kwargs in (
            {"windsorize_std": 0.0},
            {"windsorize_std": np.nan},
            {"target_volatility_annual": -0.1},
            {"winsorize_method": "iqr"},
            {"max_leverage": 0.0},
            {"min_history": 1},
        ):
            with self.assertRaises(ValueError):
                ModelArchitectureSelectorEngine(**kwargs)


if __name__ == '__main__':
    unittest.main()
