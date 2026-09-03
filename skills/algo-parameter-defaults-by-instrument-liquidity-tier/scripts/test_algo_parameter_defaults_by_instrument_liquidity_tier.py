"""Behavioral tests for liquidity-tier execution defaults."""

import copy
import logging
import pickle
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
        self.assertEqual(self.manager.classify_tier(500_000.0), LiquidityTier.HIGH)
        self.assertEqual(self.manager.classify_tier(499_999.999), LiquidityTier.MEDIUM)
        self.assertEqual(self.manager.classify_tier(50_000.0), LiquidityTier.MEDIUM)
        self.assertEqual(self.manager.classify_tier(49_999.999), LiquidityTier.LOW)

    def test_invalid_adv_values_are_rejected(self):
        for adv in (-1.0, float("nan"), float("inf"), True, "1000"):
            with self.subTest(adv=adv):
                with self.assertRaises((TypeError, ValueError)):
                    self.manager.classify_tier(adv)

    def test_zero_adv_warns_rather_than_passing_silently_as_low(self):
        """A zero ADV is a data-quality failure, not a genuine LOW observation."""
        with self.assertLogs(
            "algo_parameter_defaults_by_instrument_liquidity_tier",
            level=logging.WARNING,
        ) as captured:
            tier = self.manager.classify_tier(0.0)

        self.assertEqual(tier, LiquidityTier.LOW)
        self.assertIn("ADV is zero", "".join(captured.output))

    def test_stale_adv_is_rejected_when_age_is_supplied(self):
        with self.assertRaises(ValueError):
            self.manager.get_profile(100_000.0, adv_age_days=31.0)

    def test_adv_age_boundary_is_inclusive_at_the_configured_maximum(self):
        manager = ExecutionParameterManager(max_adv_age_days=30.0)

        self.assertEqual(
            manager.classify_tier(20_000_000.0, adv_age_days=30.0),
            LiquidityTier.HIGH,
        )
        with self.assertRaises(ValueError):
            manager.classify_tier(20_000_000.0, adv_age_days=30.000001)

    def test_missing_adv_age_is_optional_by_default_and_required_on_request(self):
        """Freshness enforcement is opt-in; without it a stale ADV is unchecked."""
        self.assertEqual(
            self.manager.classify_tier(1_000_000.0), LiquidityTier.HIGH
        )

        strict_manager = ExecutionParameterManager(
            high_adv_threshold=500_000.0,
            medium_adv_threshold=50_000.0,
            require_adv_age=True,
        )
        with self.assertRaises(ValueError):
            strict_manager.get_profile(1_000_000.0)
        self.assertEqual(
            strict_manager.get_profile(1_000_000.0, adv_age_days=2.0).tier,
            LiquidityTier.HIGH,
        )

    def test_invalid_threshold_configuration_is_rejected(self):
        invalid_configurations = (
            {"high_adv_threshold": 0.0},
            {"medium_adv_threshold": -1.0},
            {
                "high_adv_threshold": 10.0,
                "medium_adv_threshold": 10.0,
            },
            {"max_adv_age_days": float("nan")},
            {"max_adv_age_days": -1.0},
            {"calibration_version": "   "},
            {"require_adv_age": "yes"},
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

    def test_profile_invariants_are_enforced_at_construction(self):
        """A profile built directly must carry the same guarantees as a managed one.

        Regression: validation used to live only in the manager, so
        ``ExecutionProfile(...)`` accepted a 150% participation rate, an unknown
        algorithm, or a negative passive buffer and only failed later -- if the
        profile ever reached a manager at all.
        """
        invalid_arguments = (
            (LiquidityTier.HIGH, 1.5, "VWAP", False, 2.0),
            (LiquidityTier.HIGH, 0.0, "VWAP", False, 2.0),
            (LiquidityTier.HIGH, -0.1, "VWAP", False, 2.0),
            (LiquidityTier.HIGH, float("nan"), "VWAP", False, 2.0),
            (LiquidityTier.HIGH, 0.05, "SNIPER", False, 2.0),
            (LiquidityTier.HIGH, 0.05, "VWAP", "yes", 2.0),
            (LiquidityTier.HIGH, 0.05, "VWAP", False, -1.0),
            (LiquidityTier.HIGH, 0.05, "VWAP", False, float("inf")),
            (LiquidityTier.HIGH, 0.05, "VWAP", False, 2.0, ""),
            ("HIGH", 0.05, "VWAP", False, 2.0),
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    ExecutionProfile(*arguments)

        # A participation rate of exactly 1.0 is the inclusive upper bound.
        self.assertEqual(
            ExecutionProfile(
                LiquidityTier.HIGH, 1.0, "VWAP", False, 0.0
            ).max_participation_rate,
            1.0,
        )

    def test_incomplete_or_mismatched_profile_mappings_are_rejected(self):
        partial_profiles = {
            LiquidityTier.HIGH: ExecutionProfile(
                LiquidityTier.HIGH, 0.03, "VWAP", False, 2.0
            )
        }
        with self.assertRaises(ValueError):
            ExecutionParameterManager(profiles=partial_profiles)

        mismatched_profiles = {
            tier: ExecutionProfile(LiquidityTier.HIGH, 0.03, "VWAP", False, 2.0)
            for tier in LiquidityTier
        }
        with self.assertRaises(ValueError):
            ExecutionParameterManager(profiles=mismatched_profiles)

        with self.assertRaises(TypeError):
            ExecutionParameterManager(
                profiles={tier: "TWAP" for tier in LiquidityTier}
            )

    def test_profiles_are_immutable(self):
        profile = self.manager.get_profile(1_000_000.0)

        with self.assertRaises(AttributeError):
            profile.max_participation_rate = 0.01

    def test_calibration_mapping_cannot_be_swapped_after_construction(self):
        """The approved calibration set must not be mutable in place."""
        replacement = ExecutionProfile(LiquidityTier.HIGH, 1.0, "VWAP", True, 0.0)

        with self.assertRaises(TypeError):
            self.manager.profiles[LiquidityTier.HIGH] = replacement

        self.assertEqual(
            self.manager.get_profile(1_000_000.0).max_participation_rate, 0.05
        )

    def test_source_mapping_mutation_does_not_leak_into_the_manager(self):
        profiles = ExecutionParameterManager._default_profiles("caller-1.0")
        manager = ExecutionParameterManager(profiles=profiles)

        profiles[LiquidityTier.HIGH] = ExecutionProfile(
            LiquidityTier.HIGH, 1.0, "VWAP", True, 0.0
        )

        self.assertEqual(
            manager.profiles[LiquidityTier.HIGH].max_participation_rate, 0.05
        )

    def test_manager_survives_pickling_and_deepcopy_with_the_proxy_intact(self):
        """A calibration must stay transferable to backtest worker processes.

        Regression: the read-only mapping proxy that protects the calibration is
        not itself picklable, so unwrapping it for transport must not also hand
        back a mutable calibration on the far side.
        """
        for label, clone in (
            ("pickle", pickle.loads(pickle.dumps(self.manager))),
            ("deepcopy", copy.deepcopy(self.manager)),
        ):
            with self.subTest(transport=label):
                self.assertEqual(clone.get_profile(1_000_000.0).tier, LiquidityTier.HIGH)
                self.assertEqual(clone.high_adv_threshold, 500_000.0)
                self.assertEqual(clone.calibration_version, "default-1.0")
                with self.assertRaises(TypeError):
                    clone.profiles[LiquidityTier.HIGH] = None

    def test_universe_defaults_do_not_authorize_crossing_without_live_checks(self):
        for tier in LiquidityTier:
            profile = self.manager.profiles[tier]
            self.assertTrue(profile.requires_live_market_check)

    def test_default_calibration_widens_participation_as_liquidity_falls(self):
        """Pin the documented -- and counter-intuitive -- direction of the defaults.

        The shipped ceiling rises as liquidity falls (5% / 10% / 20%). That is a
        fill-feasibility allowance, not a claim that participation is cheaper in a
        thin name; ``references/standards.md`` explains why. Pinned here so the
        ordering cannot be changed without an explicit calibration decision.
        """
        rates = {
            tier: self.manager.profiles[tier].max_participation_rate
            for tier in LiquidityTier
        }

        self.assertEqual(rates[LiquidityTier.HIGH], 0.05)
        self.assertEqual(rates[LiquidityTier.MEDIUM], 0.10)
        self.assertEqual(rates[LiquidityTier.LOW], 0.20)
        self.assertLess(rates[LiquidityTier.HIGH], rates[LiquidityTier.LOW])

        # Only the deepest tier may take liquidity by default, because crossing a
        # wide spread is the expensive half of the trade-off in a thin name.
        self.assertTrue(self.manager.profiles[LiquidityTier.HIGH].cross_spread_allowed)
        self.assertFalse(self.manager.profiles[LiquidityTier.LOW].cross_spread_allowed)


if __name__ == "__main__":
    unittest.main()
