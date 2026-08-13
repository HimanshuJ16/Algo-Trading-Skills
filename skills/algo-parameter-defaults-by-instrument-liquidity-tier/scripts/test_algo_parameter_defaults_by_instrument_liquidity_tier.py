"""Behavioral tests for liquidity-tier execution defaults."""

import unittest

from algo_parameter_defaults_by_instrument_liquidity_tier import (
    ExecutionParameterManager,
    ExecutionProfile,
    LiquidityTier,
)


class TestExecutionParameterManager(unittest.TestCase):
    def setUp(self):
        self.manager = ExecutionParameterManager(
            high_adv_threshold=500_000.0,
            medium_adv_threshold=50_000.0,
        )

    def test_high_liquidity_assignment(self):
        profile = self.manager.get_profile(1_000_000.0, adv_age_days=1.0)

        self.assertEqual(profile.tier, LiquidityTier.HIGH)
        self.assertEqual(profile.default_algo_type, "TWAP")
        self.assertTrue(profile.cross_spread_allowed)
        self.assertTrue(profile.requires_live_market_check)
        self.assertEqual(profile.max_participation_rate, 0.05)

    def test_medium_liquidity_assignment(self):
        profile = self.manager.get_profile(100_000.0)

        self.assertEqual(profile.tier, LiquidityTier.MEDIUM)
        self.assertEqual(profile.default_algo_type, "VWAP")
        self.assertFalse(profile.cross_spread_allowed)

    def test_low_liquidity_assignment(self):
        profile = self.manager.get_profile(10_000.0)

        self.assertEqual(profile.tier, LiquidityTier.LOW)
        self.assertEqual(profile.default_algo_type, "IS")
        self.assertFalse(profile.cross_spread_allowed)
        self.assertEqual(profile.passive_buffer_bps, 20.0)

    def test_threshold_boundaries_are_deterministic(self):
        self.assertEqual(
            self.manager.classify_tier(500_000.0), LiquidityTier.HIGH
        )
        self.assertEqual(
            self.manager.classify_tier(50_000.0), LiquidityTier.MEDIUM
        )
        self.assertEqual(
            self.manager.classify_tier(49_999.999), LiquidityTier.LOW
        )

    def test_invalid_adv_values_are_rejected(self):
        for adv in (-1.0, float("nan"), float("inf"), True, "1000"):
            with self.subTest(adv=adv):
                with self.assertRaises((TypeError, ValueError)):
                    self.manager.classify_tier(adv)

    def test_stale_adv_is_rejected_when_age_is_supplied(self):
        with self.assertRaises(ValueError):
            self.manager.get_profile(100_000.0, adv_age_days=31.0)

    def test_invalid_threshold_configuration_is_rejected(self):
        invalid_configurations = (
            {"high_adv_threshold": 0.0},
            {"medium_adv_threshold": -1.0},
            {
                "high_adv_threshold": 10.0,
                "medium_adv_threshold": 10.0,
            },
            {"max_adv_age_days": float("nan")},
        )
        for overrides in invalid_configurations:
            with self.subTest(overrides=overrides):
                with self.assertRaises((TypeError, ValueError)):
                    ExecutionParameterManager(**overrides)

    def test_custom_profiles_are_validated_and_versioned(self):
        profiles = {
            LiquidityTier.HIGH: ExecutionProfile(
                LiquidityTier.HIGH, 0.03, "VWAP", False, 2.0, "research-2026-08"
            ),
            LiquidityTier.MEDIUM: ExecutionProfile(
                LiquidityTier.MEDIUM, 0.06, "VWAP", False, 4.0, "research-2026-08"
            ),
            LiquidityTier.LOW: ExecutionProfile(
                LiquidityTier.LOW, 0.08, "IS", False, 10.0, "research-2026-08"
            ),
        }
        manager = ExecutionParameterManager(
            high_adv_threshold=500.0,
            medium_adv_threshold=100.0,
            profiles=profiles,
            calibration_version="research-2026-08",
        )

        profile = manager.get_profile(600.0)

        self.assertEqual(profile.max_participation_rate, 0.03)
        self.assertEqual(profile.calibration_version, "research-2026-08")

    def test_incomplete_or_invalid_profiles_are_rejected(self):
        profiles = {
            LiquidityTier.HIGH: ExecutionProfile(
                LiquidityTier.HIGH, 0.03, "VWAP", False, 2.0
            )
        }
        with self.assertRaises(ValueError):
            ExecutionParameterManager(profiles=profiles)

        invalid_profiles = {
            tier: ExecutionProfile(tier, 1.5, "VWAP", False, 2.0)
            for tier in LiquidityTier
        }
        with self.assertRaises(ValueError):
            ExecutionParameterManager(profiles=invalid_profiles)

    def test_profiles_are_immutable(self):
        profile = self.manager.get_profile(1_000_000.0)

        with self.assertRaises(AttributeError):
            profile.max_participation_rate = 0.01

    def test_universe_defaults_do_not_authorize_crossing_without_live_checks(self):
        for tier in LiquidityTier:
            profile = self.manager._profiles[tier]
            self.assertTrue(profile.requires_live_market_check)


if __name__ == "__main__":
    unittest.main()