"""Unit tests for the FIFO queue position model.

Expected values in the quantitative tests are derived independently of the
implementation — closed-form Poisson expressions written out by hand, and
queue arithmetic worked through in the test body — so that a restatement of the
implementation's own formula cannot make a broken model pass.
"""

import math
import unittest

from queue_position_modeling_for_passive_orders import (
    Config,
    PassiveOrderTracker,
    QueuePositionConfigurationError,
    QueuePositionModelEngine,
    QueuePositionReport,
    QueuePositionValidationError,
    poisson_survival,
)


def make_tracker(**overrides) -> PassiveOrderTracker:
    fields = {
        "order_id": "ORD_PASSIVE_01",
        "side": "BUY",
        "price": 100.0,
        "our_quantity": 100.0,
        "initial_queue_ahead": 1000.0,
        "total_level_volume": 2000.0,
    }
    fields.update(overrides)
    return PassiveOrderTracker(**fields)


class TestPoissonSurvival(unittest.TestCase):
    """Closed-form checks against hand-evaluated Poisson expressions."""

    def test_at_least_one_trade(self):
        # P(N >= 1 | mu) = 1 - e^-mu
        self.assertAlmostEqual(poisson_survival(1, 2.5), 1.0 - math.exp(-2.5), places=12)

    def test_at_least_two_trades(self):
        # P(N >= 2 | 1) = 1 - e^-1 (1 + 1) = 1 - 2/e
        self.assertAlmostEqual(poisson_survival(2, 1.0), 1.0 - 2.0 / math.e, places=12)

    def test_at_least_three_trades(self):
        # P(N >= 3 | 2) = 1 - e^-2 (1 + 2 + 2) = 1 - 5 e^-2
        self.assertAlmostEqual(
            poisson_survival(3, 2.0), 1.0 - 5.0 * math.exp(-2.0), places=12
        )

    def test_non_positive_k_is_certain(self):
        self.assertEqual(poisson_survival(0, 5.0), 1.0)
        self.assertEqual(poisson_survival(-3, 5.0), 1.0)

    def test_zero_intensity_never_fills(self):
        self.assertEqual(poisson_survival(1, 0.0), 0.0)
        self.assertEqual(poisson_survival(10, 0.0), 0.0)

    def test_far_upper_tail_is_zero_not_negative(self):
        self.assertEqual(poisson_survival(1000, 1.0), 0.0)

    def test_monotone_decreasing_in_k(self):
        values = [poisson_survival(k, 4.0) for k in range(1, 20)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_normal_approximation_branch_does_not_saturate(self):
        # mu = 1000 exceeds the exact-summation threshold. At k = mu the true
        # survival probability is close to one half; the guard exists because
        # math.exp(-mu) underflows to 0.0 and would return a constant 1.0.
        probability = poisson_survival(1000, 1000.0)
        self.assertGreater(probability, 0.45)
        self.assertLess(probability, 0.55)

    def test_normal_approximation_branch_low_k_is_near_certain(self):
        self.assertGreater(poisson_survival(10, 1000.0), 0.999)


class TestConfigValidation(unittest.TestCase):

    def test_alpha_out_of_range_rejected(self):
        for bad in (-0.1, 1.5, 2.0):
            with self.assertRaises(QueuePositionConfigurationError):
                Config(cancellation_share_alpha=bad)

    def test_alpha_non_finite_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(QueuePositionConfigurationError):
                Config(cancellation_share_alpha=bad)

    def test_bool_rejected_as_numeric(self):
        with self.assertRaises(QueuePositionConfigurationError):
            Config(cancellation_share_alpha=True)

    def test_non_positive_sizes_rejected(self):
        for bad in (0.0, -1.0, float("nan")):
            with self.assertRaises(QueuePositionConfigurationError):
                Config(average_order_size=bad)
            with self.assertRaises(QueuePositionConfigurationError):
                Config(average_trade_size=bad)

    def test_negative_tolerance_rejected(self):
        with self.assertRaises(QueuePositionConfigurationError):
            Config(front_of_queue_tolerance=-1e-6)

    def test_alpha_boundaries_accepted(self):
        self.assertEqual(Config(cancellation_share_alpha=0.0).cancellation_share_alpha, 0.0)
        self.assertEqual(Config(cancellation_share_alpha=1.0).cancellation_share_alpha, 1.0)

    def test_engine_rejects_non_config(self):
        with self.assertRaises(QueuePositionConfigurationError):
            QueuePositionModelEngine(config={"cancellation_share_alpha": 0.5})


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = QueuePositionModelEngine()

    def _expect_rejection(self, tracker, **kwargs):
        call = {"accumulated_fills": 0.0, "accumulated_cancellations": 0.0}
        call.update(kwargs)
        with self.assertRaises(QueuePositionValidationError):
            self.engine.update_queue_position(tracker, **call)

    def test_nan_queue_ahead_is_refused_not_reported_front_of_queue(self):
        # Regression: max(0.0, float('nan')) returns 0.0 in CPython, so an
        # unvalidated NaN used to be reported as FRONT_OF_QUEUE with a fill
        # probability of 1.0 — the most aggressive output the model can emit,
        # produced by corrupt data.
        self._expect_rejection(make_tracker(initial_queue_ahead=float("nan")))

    def test_nan_fill_rate_is_refused(self):
        # Regression: min(1.0, float('nan')) returns 1.0.
        self._expect_rejection(
            make_tracker(), historical_fill_rate_per_sec=float("nan")
        )

    def test_non_finite_fields_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            self._expect_rejection(make_tracker(price=value))
            self._expect_rejection(make_tracker(our_quantity=value))
            self._expect_rejection(make_tracker(total_level_volume=value))
            self._expect_rejection(make_tracker(), accumulated_fills=value)
            self._expect_rejection(make_tracker(), accumulated_cancellations=value)
            self._expect_rejection(make_tracker(), time_horizon_sec=value)

    def test_negative_fills_refused(self):
        # Regression: a negative fill volume used to grow the queue ahead beyond
        # the total volume resting at the level, which cannot happen.
        self._expect_rejection(make_tracker(), accumulated_fills=-9999.0)

    def test_negative_cancellations_refused(self):
        self._expect_rejection(make_tracker(), accumulated_cancellations=-1.0)

    def test_unknown_side_refused(self):
        # Regression: an arbitrary string used to be upper-cased and reported.
        self._expect_rejection(make_tracker(side="sideways"))
        self._expect_rejection(make_tracker(side=""))

    def test_side_is_normalised(self):
        report = self.engine.update_queue_position(make_tracker(side=" buy "), 0.0, 0.0)
        self.assertEqual(report.side, "BUY")

    def test_non_positive_quantity_and_price_refused(self):
        self._expect_rejection(make_tracker(our_quantity=0.0))
        self._expect_rejection(make_tracker(our_quantity=-500.0))
        self._expect_rejection(make_tracker(price=0.0))
        self._expect_rejection(make_tracker(price=-1.0))

    def test_negative_queue_ahead_refused(self):
        self._expect_rejection(make_tracker(initial_queue_ahead=-1.0))

    def test_inconsistent_level_snapshot_refused(self):
        # total_level_volume includes our own resting quantity, so it can never
        # be smaller than initial_queue_ahead + our_quantity.
        self._expect_rejection(
            make_tracker(initial_queue_ahead=1000.0, our_quantity=100.0,
                         total_level_volume=1050.0)
        )

    def test_exactly_consistent_level_snapshot_accepted(self):
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=1000.0, our_quantity=100.0,
                         total_level_volume=1100.0),
            0.0, 0.0,
        )
        self.assertEqual(report.current_queue_ahead, 1000.0)

    def test_non_positive_horizon_refused(self):
        self._expect_rejection(make_tracker(), time_horizon_sec=0.0)
        self._expect_rejection(make_tracker(), time_horizon_sec=-5.0)

    def test_negative_fill_rate_refused(self):
        self._expect_rejection(make_tracker(), historical_fill_rate_per_sec=-1.0)

    def test_empty_order_id_refused(self):
        self._expect_rejection(make_tracker(order_id="   "))

    def test_wrong_tracker_type_refused(self):
        with self.assertRaises(QueuePositionValidationError):
            self.engine.update_queue_position({"order_id": "X"}, 0.0, 0.0)

    def test_overflowing_rate_times_horizon_refused(self):
        # Both factors are individually finite but their product is inf, which
        # then divides to NaN and would be reported as a fill probability.
        self._expect_rejection(
            make_tracker(initial_queue_ahead=0.0, total_level_volume=100.0),
            time_horizon_sec=1e300,
            historical_fill_rate_per_sec=1e300,
        )

    def test_overflowing_rank_quotient_raises_module_error(self):
        # queue_ahead / average_order_size overflows to inf, and math.ceil(inf)
        # raises a bare OverflowError that a caller guarding against
        # QueuePositionError would not catch.
        engine = QueuePositionModelEngine(Config(average_order_size=1e-300))
        tracker = make_tracker(initial_queue_ahead=1e300, total_level_volume=1e301)
        with self.assertRaises(QueuePositionValidationError):
            engine.update_queue_position(tracker, 0.0, 0.0)


class TestQueueArithmetic(unittest.TestCase):

    def setUp(self):
        self.engine = QueuePositionModelEngine()

    def test_fills_consume_queue_from_the_front(self):
        report = self.engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=0.0
        )
        self.assertEqual(report.current_queue_ahead, 500.0)
        self.assertEqual(report.cancellations_credited_ahead, 0.0)
        self.assertEqual(report.status, "QUEUE_PRIORITY_TRACKING")
        self.assertFalse(report.is_front_of_queue)

    def test_cancellation_share_excludes_our_own_resting_quantity(self):
        # 1000 ahead, 100 ours, 2000 total at the level.
        # After 500 fills, 500 rest ahead of us.
        # Other participants' resting volume = 2000 - 100 = 1900, so the assumed
        # uniform share of cancellations landing ahead of us is 500/1900, then
        # haircut by alpha = 0.5:  200 * (500/1900) * 0.5 = 26.315789...
        # Regression: the previous implementation divided by the full 2000
        # (including our own order) and reported 475.0.
        expected_credited = 200.0 * (500.0 / 1900.0) * 0.5
        report = self.engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=200.0
        )
        self.assertAlmostEqual(report.cancellations_credited_ahead, expected_credited, places=9)
        self.assertAlmostEqual(report.current_queue_ahead, 500.0 - expected_credited, places=9)
        self.assertNotAlmostEqual(report.current_queue_ahead, 475.0, places=3)

    def test_alpha_zero_credits_no_cancellations_ahead(self):
        engine = QueuePositionModelEngine(Config(cancellation_share_alpha=0.0))
        report = engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=200.0
        )
        self.assertEqual(report.cancellations_credited_ahead, 0.0)
        self.assertEqual(report.current_queue_ahead, 500.0)

    def test_alpha_one_is_the_pure_uniform_model(self):
        engine = QueuePositionModelEngine(Config(cancellation_share_alpha=1.0))
        report = engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=200.0
        )
        self.assertAlmostEqual(
            report.cancellations_credited_ahead, 200.0 * (500.0 / 1900.0), places=9
        )

    def test_queue_ahead_never_goes_negative_on_over_fill(self):
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=500.0, total_level_volume=1000.0),
            accumulated_fills=600.0, accumulated_cancellations=0.0,
        )
        self.assertEqual(report.current_queue_ahead, 0.0)
        self.assertTrue(report.is_front_of_queue)
        self.assertEqual(report.status, "FRONT_OF_QUEUE")
        self.assertEqual(report.estimated_queue_rank, 1)

    def test_cancellations_cannot_remove_more_than_rests_ahead(self):
        engine = QueuePositionModelEngine(Config(cancellation_share_alpha=1.0))
        report = engine.update_queue_position(
            make_tracker(initial_queue_ahead=100.0, our_quantity=1.0,
                         total_level_volume=101.0),
            accumulated_fills=0.0, accumulated_cancellations=1_000_000.0,
        )
        self.assertEqual(report.cancellations_credited_ahead, 100.0)
        self.assertEqual(report.current_queue_ahead, 0.0)
        self.assertTrue(report.is_front_of_queue)

    def test_no_cancellation_credit_once_queue_ahead_is_empty(self):
        report = self.engine.update_queue_position(
            make_tracker(), accumulated_fills=1000.0, accumulated_cancellations=500.0
        )
        self.assertEqual(report.current_queue_ahead, 0.0)
        self.assertEqual(report.cancellations_credited_ahead, 0.0)


class TestQueueRank(unittest.TestCase):

    def setUp(self):
        self.engine = QueuePositionModelEngine()

    def test_partially_filled_order_ahead_still_counts(self):
        # 150 units ahead at an average order size of 100 is one whole order
        # plus part of a second. Both block us, so two orders are ahead and our
        # rank is 3. Regression: floor-based counting reported rank 2.
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=150.0, total_level_volume=250.0), 0.0, 0.0
        )
        self.assertEqual(report.current_queue_ahead, 150.0)
        self.assertEqual(report.estimated_queue_rank, 3)

    def test_exact_multiple_of_average_order_size(self):
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=300.0, total_level_volume=400.0), 0.0, 0.0
        )
        self.assertEqual(report.estimated_queue_rank, 4)

    def test_front_of_queue_is_rank_one(self):
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=0.0, total_level_volume=100.0), 0.0, 0.0
        )
        self.assertEqual(report.estimated_queue_rank, 1)

    def test_average_order_size_is_configurable(self):
        engine = QueuePositionModelEngine(Config(average_order_size=500.0))
        report = engine.update_queue_position(
            make_tracker(initial_queue_ahead=1000.0), 0.0, 0.0
        )
        self.assertEqual(report.estimated_queue_rank, 3)


class TestFillProbability(unittest.TestCase):

    def setUp(self):
        self.engine = QueuePositionModelEngine()

    def test_front_of_queue_is_not_certain_to_fill(self):
        # Front of queue, our quantity 100, average trade size 100, expected
        # volume 50/s * 5s = 250 -> mu = 2.5 trades, one trade needed.
        # P(fill) = P(N >= 1 | 2.5) = 1 - e^-2.5 = 0.917915...
        # Regression: the deterministic ratio min(1, 250/100) reported 1.0,
        # i.e. certainty of execution.
        report = self.engine.update_queue_position(
            make_tracker(initial_queue_ahead=0.0, total_level_volume=100.0), 0.0, 0.0
        )
        self.assertAlmostEqual(report.fill_probability, 1.0 - math.exp(-2.5), places=12)
        self.assertLess(report.fill_probability, 1.0)

    def test_full_fill_probability_matches_hand_evaluated_poisson(self):
        # 1000 ahead, 500 filled, 200 cancelled -> 473.68421... ahead.
        # Volume needed for a complete fill = 473.684... + 100 = 573.684...
        # At an average trade size of 100 that is 6 trades; mu = 2.5.
        # P(N >= 6 | 2.5) = 1 - e^-2.5 * sum_{i=0..5} 2.5^i / i!
        mu = 2.5
        cdf = sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(6))
        report = self.engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=200.0
        )
        self.assertAlmostEqual(report.volume_required_for_full_fill,
                               573.6842105263158, places=9)
        self.assertAlmostEqual(report.fill_probability, 1.0 - cdf, places=12)
        # The superseded deterministic ratio would have reported 250/573.68 = 0.436.
        self.assertLess(report.fill_probability, 0.10)

    def test_partial_fill_probability_matches_hand_evaluated_poisson(self):
        # 473.68... ahead means 4 whole trades clear the queue and the 5th
        # touches us: P(N >= 5 | 2.5).
        mu = 2.5
        cdf = sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(5))
        report = self.engine.update_queue_position(
            make_tracker(), accumulated_fills=500.0, accumulated_cancellations=200.0
        )
        self.assertAlmostEqual(report.partial_fill_probability, 1.0 - cdf, places=12)

    def test_partial_fill_never_below_full_fill(self):
        for queue_ahead in (0.0, 1.0, 99.0, 100.0, 150.0, 999.0, 5000.0):
            for our_quantity in (1.0, 50.0, 100.0, 750.0):
                report = self.engine.update_queue_position(
                    make_tracker(
                        initial_queue_ahead=queue_ahead,
                        our_quantity=our_quantity,
                        total_level_volume=queue_ahead + our_quantity + 10.0,
                    ),
                    0.0, 0.0,
                )
                self.assertGreaterEqual(
                    report.partial_fill_probability, report.fill_probability,
                    msg=f"ahead={queue_ahead} qty={our_quantity}",
                )

    def test_fill_probability_decreases_as_queue_ahead_grows(self):
        probabilities = []
        for queue_ahead in (0.0, 100.0, 200.0, 400.0, 800.0):
            report = self.engine.update_queue_position(
                make_tracker(initial_queue_ahead=queue_ahead,
                             total_level_volume=queue_ahead + 100.0),
                0.0, 0.0,
            )
            probabilities.append(report.fill_probability)
        self.assertEqual(probabilities, sorted(probabilities, reverse=True))

    def test_zero_fill_rate_gives_zero_probability(self):
        report = self.engine.update_queue_position(
            make_tracker(), 0.0, 0.0, historical_fill_rate_per_sec=0.0
        )
        self.assertEqual(report.fill_probability, 0.0)
        self.assertEqual(report.partial_fill_probability, 0.0)
        self.assertEqual(report.expected_level_volume, 0.0)

    def test_longer_horizon_raises_probability(self):
        short = self.engine.update_queue_position(make_tracker(), 0.0, 0.0,
                                                  time_horizon_sec=1.0)
        long = self.engine.update_queue_position(make_tracker(), 0.0, 0.0,
                                                 time_horizon_sec=60.0)
        self.assertGreater(long.fill_probability, short.fill_probability)

    def test_probabilities_stay_in_unit_interval(self):
        for rate in (0.0, 1.0, 50.0, 5_000.0, 1_000_000.0):
            report = self.engine.update_queue_position(
                make_tracker(), 0.0, 0.0, historical_fill_rate_per_sec=rate
            )
            self.assertGreaterEqual(report.fill_probability, 0.0)
            self.assertLessEqual(report.fill_probability, 1.0)
            self.assertGreaterEqual(report.partial_fill_probability, 0.0)
            self.assertLessEqual(report.partial_fill_probability, 1.0)

    def test_expected_level_volume_is_reported_unclamped(self):
        report = self.engine.update_queue_position(
            make_tracker(), 0.0, 0.0,
            time_horizon_sec=10.0, historical_fill_rate_per_sec=200.0,
        )
        self.assertEqual(report.expected_level_volume, 2000.0)


class TestReportConsistency(unittest.TestCase):

    def setUp(self):
        self.engine = QueuePositionModelEngine()

    def test_reported_queue_ahead_agrees_with_front_of_queue_flag(self):
        tolerance = self.engine.config.front_of_queue_tolerance
        for fills in (0.0, 250.0, 999.0, 999.96, 1000.0, 5000.0):
            report = self.engine.update_queue_position(make_tracker(), fills, 0.0)
            self.assertEqual(
                report.current_queue_ahead <= tolerance,
                report.is_front_of_queue,
                msg=f"fills={fills}",
            )
            self.assertEqual(
                report.status,
                "FRONT_OF_QUEUE" if report.is_front_of_queue else "QUEUE_PRIORITY_TRACKING",
            )

    def test_sub_unit_residue_is_not_rounded_away(self):
        # Regression: the reported figure used to be rounded to one decimal
        # while the flag was computed from the raw value, so 0.04 units ahead
        # was reported as 0.0 alongside is_front_of_queue = False.
        report = self.engine.update_queue_position(make_tracker(), 999.96, 0.0)
        self.assertGreater(report.current_queue_ahead, 0.0)
        self.assertLess(report.current_queue_ahead, 0.1)
        self.assertFalse(report.is_front_of_queue)

    def test_audit_notes_are_populated(self):
        report = self.engine.update_queue_position(make_tracker(), 500.0, 200.0)
        self.assertIn("ORD_PASSIVE_01", report.audit_notes)
        self.assertIn("QUEUE_PRIORITY_TRACKING", report.audit_notes)
        self.assertIsInstance(report, QueuePositionReport)

    def test_engine_is_stateless_across_calls(self):
        # Cumulative inputs: the same observation must produce the same answer
        # however many times it is evaluated. Callers passing per-tick
        # increments instead will therefore understate queue progress.
        tracker = make_tracker()
        first = self.engine.update_queue_position(tracker, 500.0, 200.0)
        second = self.engine.update_queue_position(tracker, 500.0, 200.0)
        self.assertEqual(first.current_queue_ahead, second.current_queue_ahead)
        self.assertEqual(first.fill_probability, second.fill_probability)
        self.assertEqual(tracker.initial_queue_ahead, 1000.0)

        incremental = self.engine.update_queue_position(tracker, 250.0, 100.0)
        self.assertGreater(incremental.current_queue_ahead, first.current_queue_ahead)


if __name__ == "__main__":
    unittest.main()
