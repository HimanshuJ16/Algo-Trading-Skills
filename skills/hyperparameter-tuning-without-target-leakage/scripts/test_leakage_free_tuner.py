import logging
import math
import statistics
import unittest

from leakage_free_tuner import (
    LeakageFreeHyperparameterTunerEngine,
    LeakageFreeTuningReport,
    TuningError,
    _canonical_key,
    _contiguous_blocks,
    expected_max_sharpe_under_null,
)

# Euler-Mascheroni constant, quoted independently of the module under test.
GAMMA = 0.5772156649015329


def setUpModule():
    """
    Keeps the engine's (correct, deliberate) audit warnings out of the test
    output. `assertLogs` installs its own handler, so log assertions still work.
    """
    engine_logger = logging.getLogger("leakage_free_tuner")
    engine_logger.addHandler(logging.NullHandler())
    engine_logger.propagate = False


class _Opaque:
    """A parameter value whose repr is not a valid Python expression."""

    def __repr__(self) -> str:
        return "<opaque estimator>"


class RecordingEval:
    """
    Evaluation callback that records every (train, val) index set it is handed.

    The recorded sets are what the isolation and purge/embargo assertions are
    made against: they are the exact data the engine authorised for each fit.
    """

    def __init__(self, score_fn=None):
        self.calls = []
        self._score_fn = score_fn or (lambda params, tr, val: 1.0)

    def __call__(self, params, train_idx, val_idx):
        self.calls.append((dict(params), list(train_idx), list(val_idx)))
        return self._score_fn(params, train_idx, val_idx)


class TestPurgedEmbargoedSplitting(unittest.TestCase):

    def setUp(self):
        self.tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )

    def test_purged_embargoed_index_generation(self):
        # 100 samples, validation fold [30, 50).
        # Purge window 5 -> purges [25, 30) (5 samples).
        # Embargo ceil(1% of 100) = 1 sample -> embargoes [50, 51) (1 sample).
        train_idx, val_idx, purged, embargoed = self.tuner.generate_purged_embargoed_indices(
            n_samples=100, val_start=30, val_end=50
        )

        self.assertEqual(len(val_idx), 20)
        self.assertEqual(purged, 5)
        self.assertEqual(embargoed, 1)
        self.assertEqual(len(train_idx), 100 - 20 - 5 - 1)  # 74 samples

        # The purged and embargoed bars are the specific ones De Prado's
        # Snippet 7.1/7.2 geometry identifies, not merely the right count.
        self.assertEqual(set(range(25, 30)) & set(train_idx), set())
        self.assertNotIn(50, train_idx)
        self.assertIn(24, train_idx)
        self.assertIn(51, train_idx)

    def test_purge_window_zero_purges_nothing(self):
        tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=2, inner_folds_count=2, purge_window_samples=0, embargo_pct=0.0
        )
        train_idx, val_idx, purged, embargoed = tuner.generate_purged_embargoed_indices(
            n_samples=100, val_start=30, val_end=50
        )
        self.assertEqual((purged, embargoed), (0, 0))
        self.assertEqual(len(train_idx), 80)

    def test_embargo_window_rounds_up_where_de_prado_truncates(self):
        # Snippet 7.2 computes int(T * pctEmbargo), which is 0 for T < 100 at
        # 1% -- silently disabling the embargo. This module rounds up.
        tuner = LeakageFreeHyperparameterTunerEngine(embargo_pct=0.01)
        self.assertEqual(int(50 * 0.01), 0)          # the published behaviour
        self.assertEqual(tuner.embargo_window(50), 1)  # the conservative one
        self.assertEqual(tuner.embargo_window(300), 3)
        self.assertEqual(
            LeakageFreeHyperparameterTunerEngine(embargo_pct=0.0).embargo_window(300), 0
        )

    def test_split_restricted_to_candidate_pool(self):
        # The pool excludes [40, 60); nothing from that range may appear in any
        # of the returned index sets, whichever zone it would have fallen into.
        pool = [i for i in range(100) if not 40 <= i < 60]
        split = self.tuner.generate_purged_embargoed_split(
            candidate_indices=pool, val_start=30, val_end=40, n_samples=100
        )
        excluded = set(range(40, 60))
        for bucket in (split.train_indices, split.val_indices,
                       split.purged_indices, split.embargoed_indices):
            self.assertEqual(excluded & set(bucket), set())
        self.assertEqual(split.val_indices, list(range(30, 40)))
        self.assertEqual(split.purged_indices, list(range(25, 30)))

    def test_rejects_a_pool_that_is_not_strictly_ascending_or_in_range(self):
        """
        The purge and embargo zones are half-open ranges in time order, and a
        block's span is read off its first and last element. An unsorted,
        duplicated or out-of-range pool would misplace both zones silently.
        """
        bad_pools = [
            [0, 2, 1, 3],          # unsorted
            [0, 1, 1, 2],          # duplicated
            [-1, 0, 1],            # negative
            [0, 1, 100],           # beyond n_samples
            [0, 1, True],          # bool masquerading as an index
            [0, 1, 2.0],           # float
        ]
        for pool in bad_pools:
            with self.subTest(pool=pool):
                with self.assertRaises(TuningError):
                    self.tuner.generate_purged_embargoed_split(
                        candidate_indices=pool, val_start=30, val_end=40, n_samples=100
                    )

    def test_invalid_validation_block_raises(self):
        for val_start, val_end in ((50, 50), (-1, 10), (90, 101), (60, 50)):
            with self.assertRaises(TuningError):
                self.tuner.generate_purged_embargoed_indices(100, val_start, val_end)
        with self.assertRaises(TuningError):
            self.tuner.generate_purged_embargoed_indices(0, 0, 1)

    def test_contiguous_blocks_partition_exactly(self):
        for n_items, n_blocks in ((300, 3), (301, 3), (10, 4), (7, 7)):
            blocks = _contiguous_blocks(n_items, n_blocks)
            self.assertEqual(len(blocks), n_blocks)
            covered = [i for start, end in blocks for i in range(start, end)]
            self.assertEqual(covered, list(range(n_items)))
            self.assertTrue(all(end > start for start, end in blocks))
        # Remainder goes to the leading blocks (numpy.array_split semantics).
        self.assertEqual(_contiguous_blocks(10, 4), [(0, 3), (3, 6), (6, 8), (8, 10)])


class TestConstructorValidation(unittest.TestCase):

    def test_rejects_degenerate_fold_counts_and_windows(self):
        bad_kwargs = [
            {"outer_folds_count": 1},
            {"outer_folds_count": 0},
            {"outer_folds_count": 3.0},
            {"inner_folds_count": 1},
            {"inner_folds_count": True},
            {"purge_window_samples": -1},
            {"embargo_pct": -0.01},
            {"embargo_pct": 1.0},
            {"embargo_pct": 1.5},
            {"embargo_pct": float("nan")},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(TuningError):
                    LeakageFreeHyperparameterTunerEngine(**kwargs)

    def test_accepts_boundary_configuration(self):
        tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=2, inner_folds_count=2, purge_window_samples=0, embargo_pct=0.0
        )
        self.assertEqual(tuner.embargo_window(1000), 0)


class TestNestedIsolation(unittest.TestCase):
    """
    Regression coverage for the defect that made the engine's central claim
    false: inner-loop index sets were drawn from the whole sample, so every
    outer test block was visible to the tuning loop that chose the
    hyperparameters later scored on it.
    """

    def setUp(self):
        self.tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )
        self.param_grid = [
            {"max_depth": 3, "learning_rate": 0.01},
            {"max_depth": 5, "learning_rate": 0.05},
            {"max_depth": 7, "learning_rate": 0.10},
        ]
        self.n_samples = 300
        self.outer_blocks = _contiguous_blocks(self.n_samples, 3)

    def _run_recorded(self, score_fn=None):
        recorder = RecordingEval(score_fn)
        report = self.tuner.execute_leakage_free_tuning(
            n_samples=self.n_samples, param_grid=self.param_grid, simulated_eval_func=recorder
        )
        return report, recorder

    def test_isolation_flag_is_checked_at_run_time_not_asserted(self):
        """
        The old implementation hard-coded `is_leakage_free_guaranteed=True`. The
        replacement flag must be the outcome of an actual check, so injecting a
        contaminated inner pool has to flip it to False.
        """
        report, _ = self._run_recorded()
        self.assertTrue(report.structural_isolation_verified)

        clean_split = self.tuner.generate_purged_embargoed_split
        n_samples = self.n_samples

        def contaminating_split(candidate_indices, val_start, val_end, **kwargs):
            split = clean_split(candidate_indices, val_start, val_end, **kwargs)
            # Only sabotage the inner splits (those drawn from a restricted
            # pool), leaving the outer split that defines the test block intact.
            if len(list(candidate_indices)) == n_samples:
                return split
            contaminated = sorted(set(split.train_indices) | {0, 1, 2})
            return type(split)(
                train_indices=contaminated,
                val_indices=split.val_indices,
                purged_indices=split.purged_indices,
                embargoed_indices=split.embargoed_indices,
            )

        self.tuner.generate_purged_embargoed_split = contaminating_split
        try:
            with self.assertLogs("leakage_free_tuner", level="ERROR"):
                sabotaged = self.tuner.execute_leakage_free_tuning(
                    self.n_samples, self.param_grid, RecordingEval()
                )
        finally:
            del self.tuner.generate_purged_embargoed_split

        self.assertFalse(sabotaged.structural_isolation_verified)

    def test_inner_tuning_calls_never_touch_the_outer_test_block(self):
        _, recorder = self._run_recorded()
        grid_size = len(self.param_grid)
        # Per outer fold: inner_folds * grid_size tuning calls, then 1 OOS call.
        calls_per_fold = self.tuner.inner_folds_count * grid_size + 1

        for fold_idx, (test_start, test_end) in enumerate(self.outer_blocks):
            test_block = set(range(test_start, test_end))
            base = fold_idx * calls_per_fold
            for offset in range(calls_per_fold - 1):
                _, train_idx, val_idx = recorder.calls[base + offset]
                with self.subTest(fold=fold_idx, call=offset):
                    self.assertEqual(test_block & set(train_idx), set())
                    self.assertEqual(test_block & set(val_idx), set())

            # The out-of-sample call scores exactly the outer test block.
            _, oos_train, oos_val = recorder.calls[base + calls_per_fold - 1]
            self.assertEqual(set(oos_val), test_block)
            self.assertEqual(test_block & set(oos_train), set())

    def test_outer_training_pool_is_purged_and_embargoed_against_the_test_block(self):
        """
        The outer fit was previously trained on every non-test observation,
        including the ones whose labels overlap the test block it is scored on.
        """
        _, recorder = self._run_recorded()
        grid_size = len(self.param_grid)
        calls_per_fold = self.tuner.inner_folds_count * grid_size + 1
        embargo = self.tuner.embargo_window(self.n_samples)  # 3 bars at 1% of 300

        for fold_idx, (test_start, test_end) in enumerate(self.outer_blocks):
            _, oos_train, _ = recorder.calls[fold_idx * calls_per_fold + calls_per_fold - 1]
            train_set = set(oos_train)
            purge_zone = set(range(max(0, test_start - self.tuner.purge_window_samples), test_start))
            embargo_zone = set(range(test_end, min(self.n_samples, test_end + embargo)))
            with self.subTest(fold=fold_idx):
                self.assertEqual(train_set & purge_zone, set(), "purge zone leaked into training")
                self.assertEqual(train_set & embargo_zone, set(), "embargo zone leaked into training")
                # The gap is a gap, not a truncation: data further away survives.
                if test_start - self.tuner.purge_window_samples - 1 >= 0:
                    self.assertIn(test_start - self.tuner.purge_window_samples - 1, train_set)
                if test_end + embargo < self.n_samples:
                    self.assertIn(test_end + embargo, train_set)


class TestReportedStatisticsAreMeasured(unittest.TestCase):
    """
    Regression coverage for the defect that made the headline output fictional:
    the leakage haircut was `oos + random.uniform(0.3, 0.7)`, so the report was
    both non-deterministic and guaranteed to allege leakage it never measured.
    """

    def setUp(self):
        self.tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )
        self.param_grid = [
            {"max_depth": 3, "learning_rate": 0.01},
            {"max_depth": 5, "learning_rate": 0.05},
            {"max_depth": 7, "learning_rate": 0.10},
        ]

    @staticmethod
    def _train_set_insensitive(params, train_idx, val_idx):
        """Score depends only on the parameters, never on the training data."""
        return 1.5 + params["max_depth"] * 0.1

    @staticmethod
    def _contamination_rewarded(params, train_idx, val_idx):
        """
        Score rises when the training set retains observations whose 5-bar
        labels overlap the validation block -- i.e. exactly what purging removes.
        A correct nested run scores the clean baseline; a leaky K-Fold does not.
        """
        train_set = set(train_idx)
        val_start = min(val_idx)
        overlapping = sum(1 for i in range(val_start - 5, val_start) if i in train_set)
        return 0.50 + 0.10 * overlapping + 0.01 * params["max_depth"]

    def test_run_is_deterministic(self):
        first = self.tuner.execute_leakage_free_tuning(300, self.param_grid, self._train_set_insensitive)
        second = self.tuner.execute_leakage_free_tuning(300, self.param_grid, self._train_set_insensitive)
        self.assertEqual(first, second)

    def test_haircut_is_zero_when_the_evaluator_cannot_be_contaminated(self):
        """
        The old implementation returned a haircut of 0.3-0.7 here, alleging
        leakage on an evaluator that is provably insensitive to its training set.
        """
        report = self.tuner.execute_leakage_free_tuning(
            300, self.param_grid, self._train_set_insensitive
        )
        self.assertIsInstance(report, LeakageFreeTuningReport)
        self.assertEqual(report.leakage_overestimation_haircut, 0.0)
        self.assertEqual(report.selection_bias_haircut, 0.0)
        # depth 7 scores highest and is train-set independent.
        self.assertEqual(report.out_of_sample_outer_sharpe, 2.2)
        self.assertEqual(report.leaky_cv_overestimated_sharpe, 2.2)
        self.assertEqual(report.best_params, {"max_depth": 7, "learning_rate": 0.10})

    def test_haircut_is_positive_when_leakage_genuinely_helps(self):
        report = self.tuner.execute_leakage_free_tuning(
            300, self.param_grid, self._contamination_rewarded
        )
        # Nested purged CV never sees the 5 overlapping bars, so it scores the
        # clean baseline; the unpurged K-Fold collects them on 2 of 3 blocks.
        self.assertAlmostEqual(report.out_of_sample_outer_sharpe, 0.57, places=6)
        self.assertGreater(report.leaky_cv_overestimated_sharpe, report.out_of_sample_outer_sharpe)
        self.assertAlmostEqual(
            report.leakage_overestimation_haircut,
            report.leaky_cv_overestimated_sharpe - report.out_of_sample_outer_sharpe,
            places=6,
        )
        self.assertGreater(report.leakage_overestimation_haircut, 0.0)

    def test_purge_and_embargo_counts_are_reported(self):
        report = self.tuner.execute_leakage_free_tuning(
            300, self.param_grid, self._train_set_insensitive
        )
        self.assertGreater(report.purged_samples_count, 0)
        self.assertGreater(report.embargoed_samples_count, 0)
        self.assertEqual(report.total_outer_folds, 3)
        self.assertEqual(report.grid_size, 3)
        self.assertTrue(report.structural_isolation_verified)

    def test_candidate_scores_cover_the_whole_grid_with_one_winner(self):
        report = self.tuner.execute_leakage_free_tuning(
            300, self.param_grid, self._train_set_insensitive
        )
        self.assertEqual(len(report.candidate_scores), 3)
        self.assertEqual(sum(1 for c in report.candidate_scores if c.is_best), 1)
        winner = next(c for c in report.candidate_scores if c.is_best)
        self.assertEqual(winner.params, report.best_params)
        # Scores are ordered by max_depth under this evaluator.
        self.assertEqual(
            [c.inner_cv_mean_sharpe for c in report.candidate_scores], [1.8, 2.0, 2.2]
        )

    def test_ties_resolve_to_the_first_grid_entry(self):
        report = self.tuner.execute_leakage_free_tuning(
            300, self.param_grid, lambda params, tr, val: 1.0
        )
        self.assertEqual(report.best_params, self.param_grid[0])

    def test_parameters_with_unevaluable_reprs_survive_selection(self):
        """
        The old implementation reconstructed the winning parameters with
        `eval(str(sorted(params.items())))`, which both executes caller-supplied
        text and fails outright on any value whose repr is not an expression.
        """
        grid = [{"estimator": _Opaque(), "max_depth": 3}, {"estimator": _Opaque(), "max_depth": 7}]
        report = self.tuner.execute_leakage_free_tuning(
            300, grid, lambda params, tr, val: float(params["max_depth"])
        )
        self.assertEqual(report.best_params["max_depth"], 7)
        self.assertIsInstance(report.best_params["estimator"], _Opaque)

    def test_a_mutating_callback_cannot_corrupt_later_candidates(self):
        """
        The isolation check runs once per inner fold, before the grid is scored.
        If the callback received the engine's own lists, the first candidate
        could mutate the index sets every later candidate is then scored on --
        after the check had already passed.
        """
        seen = []

        def mutating(params, train_idx, val_idx):
            seen.append((len(train_idx), len(val_idx)))
            train_idx.clear()
            val_idx.append(-999)
            return float(params["max_depth"])

        report = self.tuner.execute_leakage_free_tuning(300, self.param_grid, mutating)
        self.assertTrue(report.structural_isolation_verified)
        # Every candidate in a given fold must have seen identically sized sets.
        per_fold = [seen[i:i + 3] for i in range(0, self.tuner.inner_folds_count * 3, 3)]
        for sizes in per_fold:
            self.assertEqual(len(set(sizes)), 1, "callback mutation leaked between candidates")
        self.assertTrue(all(train > 0 and val > 0 for train, val in seen))

    def test_canonical_key_is_order_independent_and_evaluation_free(self):
        self.assertEqual(
            _canonical_key({"a": 1, "b": 2}), _canonical_key({"b": 2, "a": 1})
        )
        self.assertNotEqual(_canonical_key({"a": 1}), _canonical_key({"a": "1"}))
        # A value whose repr is executable text is never executed.
        key = _canonical_key({"a": "__import__('os').getcwd()"})
        self.assertEqual(key, (("a", '"__import__(\'os\').getcwd()"'),))


class TestOrchestrationValidation(unittest.TestCase):

    def setUp(self):
        self.tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )
        self.grid = [{"max_depth": 3}]

    def test_rejects_malformed_inputs(self):
        ok = lambda params, tr, val: 1.0
        cases = [
            (0, self.grid, ok),
            (-10, self.grid, ok),
            (300, [], ok),
            (300, [{"a": 1}, "not-a-dict"], ok),
            (300, self.grid, "not-callable"),
        ]
        for n_samples, grid, fn in cases:
            with self.subTest(n_samples=n_samples, grid=grid):
                with self.assertRaises(TuningError):
                    self.tuner.execute_leakage_free_tuning(n_samples, grid, fn)

    def test_rejects_sample_too_short_for_the_fold_geometry(self):
        # 3 outer x 2 inner needs at least 6 observations before purging.
        with self.assertRaises(TuningError):
            self.tuner.execute_leakage_free_tuning(5, self.grid, lambda p, t, v: 1.0)

    def test_rejects_configuration_that_purges_a_fold_empty(self):
        greedy = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=40, embargo_pct=0.40
        )
        with self.assertRaises(TuningError):
            greedy.execute_leakage_free_tuning(60, self.grid, lambda p, t, v: 1.0)

    def test_rejects_non_finite_scores(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "1.0", None):
            with self.subTest(score=bad):
                with self.assertRaises(TuningError):
                    self.tuner.execute_leakage_free_tuning(
                        300, self.grid, lambda p, t, v: bad
                    )


class TestExpectedMaxSharpeUnderNull(unittest.TestCase):
    """
    Bailey & Lopez de Prado (2014), Journal of Portfolio Management 40(5):
        E[max SR] = E[SR] + sd[SR] * ((1-g) Z^-1[1 - 1/N] + g Z^-1[1 - 1/(Ne)])
    Expected values below are assembled from published standard-normal
    quantiles, not from the module's own arithmetic.
    """

    def test_single_trial_involves_no_selection(self):
        self.assertEqual(expected_max_sharpe_under_null(1, 1.0), 0.0)
        self.assertEqual(expected_max_sharpe_under_null(1, 1.0, mean_trial_sharpe=0.8), 0.8)

    def test_two_trials_reduce_to_the_second_quantile_term(self):
        # Z^-1[1 - 1/2] = Z^-1[0.5] = 0 exactly, so only the gamma term survives.
        # Z^-1[1 - 1/(2e)] = Z^-1[0.8160602794142788] = 0.9004525966377903.
        expected = GAMMA * 0.9004525966377903
        self.assertAlmostEqual(expected_max_sharpe_under_null(2, 1.0), expected, places=9)

    def test_ten_trials_matches_hand_assembled_quantiles(self):
        # Z^-1[0.9] = 1.2815515655446004 (textbook 90th percentile).
        # Z^-1[1 - 1/(10e)] = Z^-1[0.9632120558828558] = 1.7892417645816279.
        expected = (1.0 - GAMMA) * 1.2815515655446004 + GAMMA * 1.7892417645816279
        self.assertAlmostEqual(expected_max_sharpe_under_null(10, 1.0), expected, places=9)
        self.assertAlmostEqual(expected_max_sharpe_under_null(10, 1.0), 1.5745983, places=6)

    def test_scales_linearly_with_trial_dispersion(self):
        base = expected_max_sharpe_under_null(10, 1.0)
        self.assertAlmostEqual(expected_max_sharpe_under_null(10, 2.5), 2.5 * base, places=9)
        self.assertEqual(expected_max_sharpe_under_null(10, 0.0), 0.0)

    def test_strictly_increasing_in_the_number_of_trials(self):
        values = [expected_max_sharpe_under_null(n, 1.0) for n in (2, 5, 10, 50, 100, 1000)]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(b > a for a, b in zip(values, values[1:])))

    def test_mean_shifts_the_result(self):
        self.assertAlmostEqual(
            expected_max_sharpe_under_null(10, 1.0, mean_trial_sharpe=0.4),
            expected_max_sharpe_under_null(10, 1.0) + 0.4,
            places=9,
        )

    def test_rejects_invalid_arguments(self):
        for args in ((0, 1.0), (-3, 1.0), (2.5, 1.0), (True, 1.0), (10, -0.5),
                     (10, float("nan")), (10, 1.0, float("inf"))):
            with self.subTest(args=args):
                with self.assertRaises(TuningError):
                    expected_max_sharpe_under_null(*args)

    def test_reported_luck_floor_uses_cross_candidate_dispersion(self):
        tuner = LeakageFreeHyperparameterTunerEngine(
            outer_folds_count=3, inner_folds_count=2, purge_window_samples=5, embargo_pct=0.01
        )
        grid = [{"max_depth": d} for d in (3, 5, 7)]
        report = tuner.execute_leakage_free_tuning(
            300, grid, lambda params, tr, val: 1.5 + params["max_depth"] * 0.1
        )
        # Candidate means are 1.8, 2.0, 2.2 -> sample stdev exactly 0.2.
        expected = expected_max_sharpe_under_null(3, statistics.stdev([1.8, 2.0, 2.2]))
        self.assertAlmostEqual(report.expected_max_sharpe_under_null, round(expected, 6), places=6)
        self.assertTrue(math.isfinite(report.expected_max_sharpe_under_null))


if __name__ == "__main__":
    unittest.main()
