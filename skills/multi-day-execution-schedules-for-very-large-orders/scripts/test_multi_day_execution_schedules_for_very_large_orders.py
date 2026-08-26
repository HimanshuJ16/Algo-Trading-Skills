"""Behavioural tests for the multi-day parent-order scheduler.

Expected impact and risk figures are derived by hand from the published model
equations (see `references/standards.md`) and hard-coded as literals, so a test
fails if the implementation's formula drifts rather than agreeing with itself.
"""

import logging
import math
import unittest

import multi_day_execution_schedules_for_very_large_orders as scheduler
from multi_day_execution_schedules_for_very_large_orders import (
    MAX_HORIZON_SESSIONS,
    SUPPORTED_PROFILES,
    MultiDayExecutionSchedulerEngine,
    MultiDayOrderConfig,
    MultiDayScheduleReport,
)


# The engine warns whenever shares_outstanding is omitted, which most cases here
# do deliberately. Keep it out of the test output without disabling the logger,
# so assertLogs can still capture it where a test checks for it.
scheduler.logger.addHandler(logging.NullHandler())
scheduler.logger.propagate = False


def _config(**overrides) -> MultiDayOrderConfig:
    """A 500k-share order against 1M ADV at a 10% cap: exactly 5 sessions."""
    base = dict(
        symbol="AAPL",
        total_parent_quantity=500000.0,
        current_price=150.0,
        adv_shares=1000000.0,
        max_daily_participation_pct=0.10,
        schedule_profile="EQUAL_DAILY",
    )
    base.update(overrides)
    return MultiDayOrderConfig(**base)


class TestScheduleConstruction(unittest.TestCase):

    def setUp(self):
        self.engine = MultiDayExecutionSchedulerEngine()

    def test_horizon_and_equal_daily_slicing(self):
        # 500,000 shares, ADV 1,000,000, 10% cap -> 100,000/day over 5 sessions.
        report = self.engine.generate_multi_day_schedule(_config())

        self.assertIsInstance(report, MultiDayScheduleReport)
        self.assertEqual(report.status, "SCHEDULE_GENERATED_SUCCESS")
        self.assertEqual(report.execution_horizon_days, 5)
        self.assertEqual(report.min_feasible_horizon_days, 5)
        self.assertEqual(report.daily_participation_cap_shares, 100000.0)
        self.assertEqual(len(report.daily_schedules), 5)

        for slice_ in report.daily_schedules:
            self.assertEqual(slice_.target_quantity, 100000.0)
            self.assertEqual(slice_.participation_pct_adv, 10.0)

        self.assertEqual(
            [s.remaining_unexecuted_qty for s in report.daily_schedules],
            [400000.0, 300000.0, 200000.0, 100000.0, 0.0],
        )

    def test_exact_multiple_of_cap_does_not_add_a_spurious_session(self):
        # 3 * 0.07 * 1e6 = 210,000 exactly; float division must not round to 4.
        report = self.engine.generate_multi_day_schedule(
            _config(total_parent_quantity=210000.0, max_daily_participation_pct=0.07)
        )
        self.assertEqual(report.execution_horizon_days, 3)

    def test_partial_final_session_rounds_horizon_up(self):
        report = self.engine.generate_multi_day_schedule(
            _config(total_parent_quantity=250000.0)
        )
        self.assertEqual(report.execution_horizon_days, 3)
        self.assertLessEqual(
            max(s.target_quantity for s in report.daily_schedules), 100000.0
        )

    def test_order_smaller_than_one_days_cap_is_a_single_session(self):
        report = self.engine.generate_multi_day_schedule(
            _config(total_parent_quantity=1000.0)
        )
        self.assertEqual(report.execution_horizon_days, 1)
        self.assertEqual(report.daily_schedules[0].target_quantity, 1000.0)
        # Nothing is carried overnight, so there is no overnight risk.
        self.assertEqual(report.overnight_volatility_risk_usd, 0.0)
        self.assertEqual(report.overnight_volatility_risk_bps, 0.0)


class TestTrajectoryProfiles(unittest.TestCase):

    def setUp(self):
        self.engine = MultiDayExecutionSchedulerEngine()

    def _slices(self, **overrides):
        report = self.engine.generate_multi_day_schedule(_config(**overrides))
        return [s.target_quantity for s in report.daily_schedules]

    def test_front_loaded_is_monotonically_non_increasing(self):
        slices = self._slices(schedule_profile="FRONT_LOADED", target_horizon_days=10)
        self.assertEqual(len(slices), 10)
        for earlier, later in zip(slices, slices[1:]):
            self.assertGreaterEqual(earlier, later)
        self.assertGreater(slices[0], slices[-1])

    def test_back_loaded_is_monotonically_non_decreasing(self):
        slices = self._slices(schedule_profile="BACK_LOADED", target_horizon_days=10)
        self.assertEqual(len(slices), 10)
        for earlier, later in zip(slices, slices[1:]):
            self.assertLessEqual(earlier, later)
        self.assertLess(slices[0], slices[-1])

    def test_capped_back_loaded_schedule_stays_monotonic(self):
        # Regression: clipping slices at the cap and refilling the excess in
        # index order used to emit a schedule that rose, dipped, then rose again
        # (e.g. [100k x 12, 66_249.61, 83_750.39, 100k x 6]). Water-filling
        # preserves the requested shape under the cap.
        slices = self._slices(total_parent_quantity=1950000.0,
                              schedule_profile="BACK_LOADED")
        self.assertEqual(len(slices), 20)
        for earlier, later in zip(slices, slices[1:]):
            self.assertLessEqual(earlier, later)

    def test_capped_front_loaded_schedule_stays_monotonic(self):
        slices = self._slices(total_parent_quantity=1950000.0,
                              schedule_profile="FRONT_LOADED")
        self.assertEqual(len(slices), 20)
        for earlier, later in zip(slices, slices[1:]):
            self.assertGreaterEqual(earlier, later)

    def test_profiles_are_flat_at_the_minimum_feasible_horizon(self):
        # At the minimum horizon the order fills the available capacity, so the
        # cap -- not the profile -- determines every slice. Callers who want a
        # tilted schedule must ask for a longer horizon.
        for profile in SUPPORTED_PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(
                    self._slices(schedule_profile=profile), [100000.0] * 5
                )

    def test_zero_decay_reproduces_equal_daily(self):
        tilted = self._slices(schedule_profile="FRONT_LOADED",
                              target_horizon_days=8, profile_decay=0.0)
        self.assertEqual(tilted, self._slices(schedule_profile="EQUAL_DAILY",
                                              target_horizon_days=8))

    def test_no_session_exceeds_the_participation_cap(self):
        for profile in SUPPORTED_PROFILES:
            for horizon in (5, 6, 12, 40):
                with self.subTest(profile=profile, horizon=horizon):
                    for qty in self._slices(schedule_profile=profile,
                                            target_horizon_days=horizon):
                        self.assertLessEqual(qty, 100000.0)

    def test_slices_sum_exactly_to_the_parent_quantity(self):
        # An over- or under-allocated schedule silently mis-executes the parent.
        for qty in (500000.0, 250000.0, 333333.0, 1234567.89, 7.0):
            for profile in SUPPORTED_PROFILES:
                with self.subTest(qty=qty, profile=profile):
                    slices = self._slices(total_parent_quantity=qty,
                                          schedule_profile=profile)
                    self.assertAlmostEqual(sum(slices), qty, places=2)

    def test_rounding_residual_spreads_when_no_session_can_absorb_it(self):
        # 5,699.0046 shares over 57 sessions at a 100-share cap allocates
        # 99.982536 per session. Flooring to the reporting quantum loses 0.14
        # shares while every session has only 0.02 shares of headroom, so the
        # residual cannot legally be handed to any single session: it has to be
        # apportioned one quantum at a time.
        report = self.engine.generate_multi_day_schedule(
            _config(total_parent_quantity=5699.0046, adv_shares=10000.0,
                    max_daily_participation_pct=0.01)
        )
        slices = [s.target_quantity for s in report.daily_schedules]
        self.assertEqual(len(slices), 57)
        self.assertAlmostEqual(sum(slices), 5699.0046, places=2)
        self.assertLessEqual(max(slices), 100.0)
        self.assertEqual(set(slices), {99.98, 99.99})

    def test_apportionment_ties_resolve_in_the_profile_direction(self):
        # When the exact allocations are equal, the extra quanta must land at the
        # end of a back-loaded horizon and at the start of a front-loaded one,
        # otherwise the reported trajectory reverses at the quantum level.
        # profile_decay=0 makes every exact allocation identical, so the only
        # thing distinguishing the schedules is where the spare quanta land.
        tied = dict(total_parent_quantity=5699.0046, adv_shares=10000.0,
                    max_daily_participation_pct=0.01, profile_decay=0.0)
        back = self._slices(schedule_profile="BACK_LOADED", **tied)
        self.assertEqual(back[0], 99.98)
        self.assertEqual(back[-1], 99.99)
        front = self._slices(schedule_profile="FRONT_LOADED", **tied)
        self.assertEqual(front[0], 99.99)
        self.assertEqual(front[-1], 99.98)

    def test_no_slice_is_reported_as_negative_zero(self):
        # A steeply decayed tail allocates ~0; -0.0 in a share quantity is a
        # reporting artefact that reads as a sell in a buy programme.
        slices = self._slices(schedule_profile="FRONT_LOADED",
                              target_horizon_days=40, profile_decay=5.0)
        for qty in slices:
            self.assertGreaterEqual(qty, 0.0)
            self.assertEqual(math.copysign(1.0, qty), 1.0)

    def test_long_horizon_does_not_overflow_the_weights(self):
        # exp(+0.3 * 250) overflows a float; the anchored weights must not.
        slices = self._slices(schedule_profile="BACK_LOADED",
                              target_horizon_days=250)
        self.assertEqual(len(slices), 250)
        self.assertTrue(all(math.isfinite(q) for q in slices))
        self.assertAlmostEqual(sum(slices), 500000.0, places=2)


class TestImpactAndRisk(unittest.TestCase):

    def setUp(self):
        self.engine = MultiDayExecutionSchedulerEngine()

    def test_temporary_impact_matches_athl_closed_form(self):
        # ATHL (2005) Sec. 4.3: K = eta * sigma * participation^beta.
        # 0.142 * 0.02 * 0.10^0.6 = 7.13375747e-4 -> 7.1338 bps, and every
        # session trades at exactly 10% here so the parent-level figure matches.
        report = self.engine.generate_multi_day_schedule(_config())
        self.assertAlmostEqual(report.expected_temp_impact_bps, 7.1338, places=4)
        for slice_ in report.daily_schedules:
            self.assertAlmostEqual(slice_.expected_temp_impact_bps, 7.1338, places=4)

    def test_temporary_impact_falls_when_participation_halves(self):
        # 0.142 * 0.02 * 0.05^0.6 = 4.70652470e-4 -> 4.7065 bps.
        report = self.engine.generate_multi_day_schedule(
            _config(target_horizon_days=10)
        )
        self.assertAlmostEqual(report.expected_temp_impact_bps, 4.7065, places=4)

    def test_permanent_impact_matches_athl_closed_form(self):
        # I = gamma * sigma * (X/V) * (Theta/V)^(1/4)
        #   = 0.314 * 0.02 * 0.5 * 200^0.25 = 0.0118082937
        # AC (2000) Eq. (8): a completed program bears half the permanent move.
        report = self.engine.generate_multi_day_schedule(
            _config(shares_outstanding=200000000.0)
        )
        self.assertAlmostEqual(report.expected_perm_impact_bps, 59.0415, places=4)
        self.assertAlmostEqual(
            report.expected_total_impact_bps,
            report.expected_temp_impact_bps + report.expected_perm_impact_bps,
            places=3,
        )

    def test_permanent_impact_is_invariant_to_the_horizon(self):
        # Linear permanent impact depends on total size only. A schedule whose
        # permanent-impact estimate shrinks as the horizon lengthens is not
        # implementing the model it cites.
        estimates = {
            horizon: self.engine.generate_multi_day_schedule(
                _config(shares_outstanding=200000000.0, target_horizon_days=horizon)
            ).expected_perm_impact_bps
            for horizon in (5, 10, 25)
        }
        self.assertEqual(len(set(estimates.values())), 1)

    def test_permanent_impact_is_none_without_shares_outstanding(self):
        # Reporting an unidentified term as a number would invent a turnover
        # assumption the caller never made, so it is surfaced as None + a warning.
        with self.assertLogs(scheduler.logger, level="WARNING") as captured:
            report = self.engine.generate_multi_day_schedule(_config())
        self.assertIsNone(report.expected_perm_impact_bps)
        self.assertIsNone(report.expected_total_impact_bps)
        self.assertIn("n/a", report.audit_notes)
        self.assertIn("shares_outstanding", captured.output[0])

    def test_overnight_risk_matches_almgren_chriss_variance_term(self):
        # AC (2000) Eq. (5): V(x) = sigma^2 * sum_k tau * x_k^2.
        # Remaining inventory 400k/300k/200k/100k/0 shares at $150:
        # sqrt(sum x_k^2) = 547_722.5575 shares -> $82,158,383.63
        # x 0.02 = $1,643,167.67, i.e. 219.0890 bps of $75,000,000.
        report = self.engine.generate_multi_day_schedule(_config())
        self.assertAlmostEqual(
            report.overnight_volatility_risk_usd, 1643167.67, places=2
        )
        self.assertAlmostEqual(
            report.overnight_volatility_risk_bps, 219.0890, places=4
        )

    def test_zero_volatility_gives_zero_impact_and_zero_risk(self):
        report = self.engine.generate_multi_day_schedule(
            _config(volatility_daily_pct=0.0, shares_outstanding=200000000.0)
        )
        self.assertEqual(report.expected_temp_impact_bps, 0.0)
        self.assertEqual(report.expected_perm_impact_bps, 0.0)
        self.assertEqual(report.overnight_volatility_risk_usd, 0.0)

    def test_stretching_the_horizon_trades_impact_against_overnight_risk(self):
        # The whole point of the skill: impact falls, overnight risk rises.
        short = self.engine.generate_multi_day_schedule(
            _config(shares_outstanding=200000000.0, target_horizon_days=5)
        )
        long = self.engine.generate_multi_day_schedule(
            _config(shares_outstanding=200000000.0, target_horizon_days=20)
        )
        self.assertLess(long.expected_temp_impact_bps, short.expected_temp_impact_bps)
        self.assertLess(long.expected_total_impact_bps, short.expected_total_impact_bps)
        self.assertGreater(
            long.overnight_volatility_risk_usd, short.overnight_volatility_risk_usd
        )

    def test_front_loading_lowers_overnight_risk_and_raises_impact(self):
        # Front-loading clears inventory sooner (less overnight exposure) at the
        # cost of a higher peak participation rate (more temporary impact) than
        # the uniform schedule, which minimises a convex cost.
        equal = self.engine.generate_multi_day_schedule(
            _config(schedule_profile="EQUAL_DAILY", target_horizon_days=12)
        )
        front = self.engine.generate_multi_day_schedule(
            _config(schedule_profile="FRONT_LOADED", target_horizon_days=12)
        )
        back = self.engine.generate_multi_day_schedule(
            _config(schedule_profile="BACK_LOADED", target_horizon_days=12)
        )
        self.assertLess(
            front.overnight_volatility_risk_usd, equal.overnight_volatility_risk_usd
        )
        self.assertGreater(
            back.overnight_volatility_risk_usd, equal.overnight_volatility_risk_usd
        )
        self.assertGreater(
            front.expected_temp_impact_bps, equal.expected_temp_impact_bps
        )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiDayExecutionSchedulerEngine()

    def _assert_raises(self, **overrides):
        with self.assertRaises(ValueError):
            self.engine.generate_multi_day_schedule(_config(**overrides))

    def test_non_positive_core_inputs_raise(self):
        for field in ("total_parent_quantity", "current_price", "adv_shares"):
            for value in (0.0, -1.0):
                with self.subTest(field=field, value=value):
                    self._assert_raises(**{field: value})

    def test_non_finite_inputs_raise(self):
        for field in ("total_parent_quantity", "current_price", "adv_shares",
                      "volatility_daily_pct", "max_daily_participation_pct"):
            for value in (float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    self._assert_raises(**{field: value})

    def test_participation_above_one_hundred_percent_raises(self):
        # Previously accepted: a 5.0 "cap" scheduled 500% of ADV in one session.
        for value in (1.5, 5.0):
            with self.subTest(value=value):
                self._assert_raises(max_daily_participation_pct=value)

    def test_non_positive_participation_raises(self):
        for value in (0.0, -0.1):
            with self.subTest(value=value):
                self._assert_raises(max_daily_participation_pct=value)

    def test_full_adv_participation_is_allowed(self):
        report = self.engine.generate_multi_day_schedule(
            _config(max_daily_participation_pct=1.0)
        )
        self.assertEqual(report.execution_horizon_days, 1)

    def test_unknown_profile_raises_rather_than_silently_flattening(self):
        # A typo used to return an EQUAL_DAILY schedule with no signal at all.
        for profile in ("FRONT-LOADED", "frontloaded", "", "VWAP"):
            with self.subTest(profile=profile):
                self._assert_raises(schedule_profile=profile)

    def test_profile_matching_is_case_insensitive(self):
        report = self.engine.generate_multi_day_schedule(
            _config(schedule_profile="front_loaded", target_horizon_days=8)
        )
        slices = [s.target_quantity for s in report.daily_schedules]
        self.assertGreater(slices[0], slices[-1])

    def test_negative_model_parameters_raise(self):
        self._assert_raises(volatility_daily_pct=-0.01)
        self._assert_raises(temp_impact_coeff=-0.1)
        self._assert_raises(perm_impact_coeff=-0.1)
        self._assert_raises(temp_impact_exponent=0.0)
        self._assert_raises(profile_decay=-0.1)

    def test_infeasible_target_horizon_raises(self):
        # 500k shares at a 100k/session cap cannot complete in 4 sessions.
        self._assert_raises(target_horizon_days=4)
        self._assert_raises(target_horizon_days=0)
        self._assert_raises(target_horizon_days=-3)

    def test_target_horizon_at_the_minimum_is_accepted(self):
        report = self.engine.generate_multi_day_schedule(_config(target_horizon_days=5))
        self.assertEqual(report.execution_horizon_days, 5)

    def test_invalid_shares_outstanding_raises(self):
        self._assert_raises(shares_outstanding=0.0)
        self._assert_raises(shares_outstanding=-1.0)
        # Below ADV implies the whole float turns over more than once a day.
        self._assert_raises(shares_outstanding=999999.0)

    def test_fractional_target_horizon_raises(self):
        # int(5.9) silently truncated to 5, which is also the minimum feasible
        # horizon here -- the caller would have got a schedule they did not ask for.
        self._assert_raises(target_horizon_days=5.9)

    def test_horizon_beyond_the_session_ceiling_raises(self):
        # A quantity supplied as notional rather than shares implies a horizon of
        # millions of sessions; materialising it would exhaust memory.
        self._assert_raises(total_parent_quantity=1e13)
        self._assert_raises(target_horizon_days=MAX_HORIZON_SESSIONS + 1)

    def test_horizon_at_the_session_ceiling_is_accepted(self):
        report = self.engine.generate_multi_day_schedule(
            _config(target_horizon_days=MAX_HORIZON_SESSIONS)
        )
        self.assertEqual(report.execution_horizon_days, MAX_HORIZON_SESSIONS)
        self.assertAlmostEqual(
            sum(s.target_quantity for s in report.daily_schedules), 500000.0, places=2
        )

    def test_blank_symbol_raises(self):
        self._assert_raises(symbol="")
        self._assert_raises(symbol="   ")


if __name__ == "__main__":
    unittest.main()
