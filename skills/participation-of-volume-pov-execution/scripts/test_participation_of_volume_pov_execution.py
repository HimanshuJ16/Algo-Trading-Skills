"""
Unit tests for the Percentage-of-Volume (POV) scheduler.

Expected quantities are derived by hand from ``floor(R/(1-R) * V_away)`` and stated
in the test, never by re-running the implementation's own expression.
"""
import unittest

from participation_of_volume_pov_execution import (
    OrderSide,
    POVExecutionReport,
    POVParentOrder,
    POVStatus,
    ParticipationOfVolumePovExecutionConfig,
    ParticipationOfVolumePovExecutionEngine,
    VolumeBasis,
    away_target_quantity,
)


def _engine(**order_kwargs):
    """Engine with a 1,000-share AAPL BUY parent, overridable field by field."""
    defaults = dict(symbol="AAPL", total_qty=1000, side="BUY", target_rate=0.15,
                    max_rate=0.30, min_slice_qty=10, max_slice_qty=500)
    defaults.update(order_kwargs)
    return ParticipationOfVolumePovExecutionEngine(parent_order=POVParentOrder(**defaults))


class TestAwayTargetQuantity(unittest.TestCase):
    """The R/(1-R) conversion, checked against hand-computed values."""

    def test_documented_worked_examples(self):
        # 0.15/0.85 = 0.176470...; x 1000 = 176.47 -> 176; x 10000 = 1764.7 -> 1764.
        self.assertEqual(away_target_quantity(0.15, 1000), 176)
        self.assertEqual(away_target_quantity(0.15, 10000), 1764)

    def test_half_rate_matches_away_volume(self):
        # To be 50% of total volume you must match away volume share for share.
        self.assertEqual(away_target_quantity(0.5, 1000), 1000)

    def test_exact_ratio_rate(self):
        # 0.2/0.8 = 0.25 exactly; 0.25 x 100 = 25.
        self.assertEqual(away_target_quantity(0.2, 100), 25)

    def test_binary_representation_does_not_lose_a_share(self):
        # Regression: R = 1/3 gives R/(1-R) = 0.49999999999999994 in IEEE-754, so a
        # naive floor of 2 away shares returns 0 instead of the correct 1.
        self.assertEqual(away_target_quantity(1.0 / 3.0, 2), 1)

    def test_zero_away_volume(self):
        self.assertEqual(away_target_quantity(0.15, 0), 0)

    def test_invalid_inputs_raise(self):
        for bad_rate in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                away_target_quantity(bad_rate, 1000)
        with self.assertRaises(ValueError):
            away_target_quantity(0.15, -1)


class TestParentOrderValidation(unittest.TestCase):
    """Misconfiguration must raise, never be silently clamped into something else."""

    def test_rate_bounds(self):
        for bad in (0.0, 1.0, 1.5, -0.15):
            with self.assertRaises(ValueError):
                POVParentOrder("AAPL", 1000, "BUY", target_rate=bad, max_rate=0.99)

    def test_target_above_cap_raises_instead_of_clamping(self):
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000, "BUY", target_rate=0.40, max_rate=0.30)

    def test_unsatisfiable_slice_bounds_raise(self):
        # min > max would pause the algorithm forever rather than fail loudly.
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000, "BUY", min_slice_qty=1000, max_slice_qty=100)

    def test_other_invalid_fields(self):
        with self.assertRaises(ValueError):
            POVParentOrder("", 1000, "BUY")
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 0, "BUY")
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", -100, "BUY")
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000.5, "BUY")
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000, "LONG")
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000, "BUY", min_slice_qty=0)
        with self.assertRaises(ValueError):
            POVParentOrder("AAPL", 1000, "BUY", target_rate=float("nan"))

    def test_side_is_normalised_to_enum(self):
        self.assertIs(POVParentOrder("AAPL", 1000, "SELL").side, OrderSide.SELL)


class TestSliceScheduling(unittest.TestCase):

    def test_slice_tracks_cumulative_target(self):
        engine = _engine()

        # Interval 1: 1,000 away shares -> cumulative target floor(176.47) = 176.
        slice1, report1 = engine.process_volume_update(1000, 150.0)
        self.assertEqual(slice1, 176)
        self.assertEqual(report1.cum_target_qty, 176)
        self.assertEqual(report1.status, POVStatus.EXECUTING)

        # Interval 2: +5,000 away -> cum away 6,000, cum target floor(1058.82) = 1058.
        # Deficit 1058 - 176 working = 882, clamped to max_slice_qty 500.
        slice2, report2 = engine.process_volume_update(5000, 150.50)
        self.assertEqual(slice2, 500)
        self.assertEqual(report2.cum_target_qty, 1058)

    def test_sent_quantity_is_working_not_filled(self):
        """Regression: quantity sent must not be reported as quantity filled."""
        engine = _engine()
        slice1, report1 = engine.process_volume_update(1000, 150.0)

        self.assertEqual(slice1, 176)
        self.assertEqual(report1.filled_qty, 0)
        self.assertEqual(report1.working_qty, 176)
        self.assertEqual(report1.remaining_qty, 1000)
        # Nothing has printed, so participation is zero — not 15%.
        self.assertEqual(report1.realized_participation_rate, 0.0)

    def test_thin_market_deficit_is_recovered_not_abandoned(self):
        """
        Regression: with a per-interval-only slice, a min-clip larger than any single
        interval's target pauses the order forever. The cumulative target accrues.
        """
        engine = _engine(min_slice_qty=100, max_slice_qty=1000)

        # 100 away shares/interval -> per-interval target 17, below the 100 minimum.
        for interval in range(1, 6):
            qty, report = engine.process_volume_update(100, 10.0)
            self.assertEqual(qty, 0, f"interval {interval} should pause")
            self.assertEqual(report.status, POVStatus.VOLUME_PAUSED)

        # After 6 intervals cum away = 600 -> target floor(105.88) = 105 >= 100.
        qty, report = engine.process_volume_update(100, 10.0)
        self.assertEqual(qty, 105)
        self.assertEqual(report.status, POVStatus.EXECUTING)

    def test_zero_volume_interval_schedules_nothing(self):
        engine = _engine()
        qty, report = engine.process_volume_update(0, 150.0)
        self.assertEqual(qty, 0)
        self.assertEqual(report.status, POVStatus.VOLUME_PAUSED)
        self.assertEqual(report.cum_market_volume, 0)

    def test_odd_lot_tail_below_min_slice_is_still_sent(self):
        # Schedulable residual (5) is below min_slice_qty (100): the gate must not
        # strand it, or the parent order can never complete.
        engine = _engine(total_qty=5, min_slice_qty=100, max_slice_qty=1000)
        qty, _ = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty, 5)

    def test_slice_never_exceeds_schedulable_remainder(self):
        engine = _engine(total_qty=100, target_rate=0.5, max_rate=0.5,
                         min_slice_qty=1, max_slice_qty=1000)
        qty, _ = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty, 100)  # target 1000, capped by the 100-share parent
        self.assertEqual(engine.working_qty, 100)

        qty2, report2 = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty2, 0)
        self.assertEqual(report2.status, POVStatus.AWAITING_FILLS)

    def test_realized_rate_never_exceeds_target_over_a_full_run(self):
        engine = _engine(total_qty=100000, min_slice_qty=1, max_slice_qty=100000)
        for volume in (1, 7, 13, 500, 0, 2, 9999, 3, 41, 1000):
            qty, report = engine.process_volume_update(volume, 10.0)
            if qty:
                engine.record_fill(qty, 10.0)
            self.assertLessEqual(engine.realized_participation_rate(), 0.15 + 1e-12)
            self.assertEqual(report.overfill_qty, 0)


class TestFillLifecycle(unittest.TestCase):

    def test_fill_moves_quantity_from_working_to_filled(self):
        engine = _engine()
        qty, _ = engine.process_volume_update(1000, 150.0)
        engine.record_fill(qty, 150.0)

        self.assertEqual(engine.filled_qty, 176)
        self.assertEqual(engine.working_qty, 0)
        # 176 / (1000 + 176) = 0.14965986..., at or below the 15% target.
        self.assertAlmostEqual(engine.realized_participation_rate(), 176 / 1176.0, places=12)
        self.assertLessEqual(engine.realized_participation_rate(), 0.15)

    def test_released_quantity_returns_to_the_schedule(self):
        engine = _engine()
        qty, _ = engine.process_volume_update(1000, 150.0)
        engine.record_fill(100, 150.0)
        engine.record_unfilled(qty - 100, reason="EXPIRED")

        self.assertEqual(engine.filled_qty, 100)
        self.assertEqual(engine.working_qty, 0)
        self.assertEqual(engine.remaining_qty, 900)

        # No new away volume: the 76 released shares are still owed against the same
        # cumulative target and are re-offered immediately.
        qty2, _ = engine.process_volume_update(0, 150.0)
        self.assertEqual(qty2, 76)

    def test_completed_status_after_parent_is_filled(self):
        engine = _engine(total_qty=100, target_rate=0.5, max_rate=0.5,
                         min_slice_qty=1, max_slice_qty=1000)
        qty, _ = engine.process_volume_update(1000, 10.0)
        engine.record_fill(qty, 10.0)
        _, report = engine.process_volume_update(1000, 10.0)
        self.assertEqual(report.status, POVStatus.COMPLETED)
        self.assertEqual(report.remaining_qty, 0)

    def test_completed_order_freezes_its_realized_rate(self):
        """
        Realized participation belongs to the window the order actually traded in.
        Volume arriving after completion must not dilute it towards zero.
        """
        engine = _engine(total_qty=100, target_rate=0.5, max_rate=0.5,
                         min_slice_qty=1, max_slice_qty=1000)
        qty, _ = engine.process_volume_update(1000, 10.0)
        engine.record_fill(qty, 10.0)
        _, first = engine.process_volume_update(1000, 10.0)   # marks COMPLETED
        _, later = engine.process_volume_update(100000, 10.0)

        self.assertEqual(later.cum_market_volume, first.cum_market_volume)
        self.assertEqual(later.realized_participation_rate,
                         first.realized_participation_rate)
        # 100 filled against 1,000 away shares consumed while working.
        self.assertAlmostEqual(later.realized_participation_rate, 100 / 1100.0, places=6)

    def test_overfill_is_surfaced_and_caps_further_scheduling(self):
        """A broker over-fill must not be hidden, and must stop the algorithm."""
        engine = _engine(total_qty=100000, max_slice_qty=100000)
        qty, _ = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty, 176)

        engine.record_fill(60000, 10.0)  # grossly over-filled child order
        self.assertEqual(engine.overfill_qty, 60000 - 176)

        # Projected participation 60000/61000 = 98.4% > max_rate 30%.
        qty2, report2 = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty2, 0)
        self.assertEqual(report2.status, POVStatus.RATE_CAPPED)
        self.assertEqual(report2.overfill_qty, 59824)

    def test_invalid_fill_reports_raise(self):
        engine = _engine()
        engine.process_volume_update(1000, 150.0)
        with self.assertRaises(ValueError):
            engine.record_fill(0)
        with self.assertRaises(ValueError):
            engine.record_fill(-5)
        with self.assertRaises(ValueError):
            engine.record_fill(10, price=0.0)
        with self.assertRaises(ValueError):
            engine.record_unfilled(0)
        with self.assertRaises(ValueError):
            engine.record_unfilled(10_000)  # more than is working


class TestVolumeBasis(unittest.TestCase):

    def test_consolidated_basis_nets_own_prints(self):
        """Tape volume includes own executions; counting them again over-participates."""
        config = ParticipationOfVolumePovExecutionConfig(volume_basis=VolumeBasis.CONSOLIDATED)
        engine = ParticipationOfVolumePovExecutionEngine(
            config=config,
            parent_order=POVParentOrder("AAPL", 10000, "BUY", target_rate=0.15,
                                        min_slice_qty=1, max_slice_qty=10000),
        )
        qty1, _ = engine.process_volume_update(1000, 10.0)
        self.assertEqual(qty1, 176)
        engine.record_fill(qty1, 10.0)

        # Next tape interval is 1,176: 1,000 away plus this order's own 176 prints.
        qty2, report2 = engine.process_volume_update(1176, 10.0)
        self.assertEqual(report2.cum_market_volume, 2000)  # own prints netted off
        self.assertEqual(qty2, 176)                        # target 352 less 176 filled

    def test_away_basis_on_the_same_tape_over_participates(self):
        """Contrast case: the identical inputs read as away volume schedule more."""
        engine = _engine(total_qty=10000, min_slice_qty=1, max_slice_qty=10000)
        qty1, _ = engine.process_volume_update(1000, 10.0)
        engine.record_fill(qty1, 10.0)
        qty2, report2 = engine.process_volume_update(1176, 10.0)
        self.assertEqual(report2.cum_market_volume, 2176)
        self.assertEqual(qty2, 208)  # floor(0.176470 x 2176) = 384, less 176 filled
        self.assertGreater(qty2, 176)


class TestInputValidation(unittest.TestCase):

    def test_negative_volume_rejected(self):
        """A negative interval would shrink the participation denominator."""
        engine = _engine()
        with self.assertRaises(ValueError):
            engine.process_volume_update(-100, 150.0)

    def test_non_integer_and_non_finite_inputs_rejected(self):
        engine = _engine()
        with self.assertRaises(ValueError):
            engine.process_volume_update(100.5, 150.0)
        with self.assertRaises(ValueError):
            engine.process_volume_update(1000, 0.0)
        with self.assertRaises(ValueError):
            engine.process_volume_update(1000, float("nan"))
        with self.assertRaises(ValueError):
            engine.process_volume_update(1000, -10.0)


class TestEngineDisabled(unittest.TestCase):

    def test_disabled_engine_schedules_nothing(self):
        config = ParticipationOfVolumePovExecutionConfig(enabled=False)
        engine = ParticipationOfVolumePovExecutionEngine(config=config)
        qty, report = engine.process_volume_update(1000, 150.0)
        self.assertEqual(qty, 0)
        self.assertEqual(report.status, POVStatus.ENGINE_DISABLED)
        self.assertIsInstance(report, POVExecutionReport)
        self.assertEqual(engine.cum_away_vol, 0)


if __name__ == "__main__":
    unittest.main()
