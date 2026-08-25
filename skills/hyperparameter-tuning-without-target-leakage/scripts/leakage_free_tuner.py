"""
hyperparameter-tuning-without-target-leakage: purged & embargoed *nested*
cross-validation for hyperparameter selection on serially dependent financial
time series with overlapping labels.

Design notes
------------
* **Index bookkeeping only.** This engine decides *which observations* each
  candidate may train on and validate against. It never fits a model, a scaler,
  or an encoder. Preprocessing isolation is the caller's obligation and is
  discharged by honouring the index sets handed to the evaluation callback --
  see `execute_leakage_free_tuning`.

* **Purging** (Lopez de Prado, *Advances in Financial Machine Learning*, 2018,
  Snippet 7.1, p. 106) drops training observations whose label interval overlaps
  the validation interval. Snippet 7.1 tests overlap with *inclusive* bounds, so
  a label ending exactly at the first validation bar is dropped. With a fixed
  h-bar forward label whose interval is ``[i, i + h]``, that is exactly the h
  observations ``[val_start - h, val_start)`` -- the convention used here.

* **Embargo** (Snippet 7.2, ``pctEmbargo``) drops training observations that
  *follow* the validation block, breaking serial correlation that purging alone
  cannot reach. Snippet 7.2 sizes it ``int(T * pctEmbargo)``, which truncates to
  **zero** for any sample shorter than ``1 / pctEmbargo`` (T < 100 at the
  customary 1%). This module uses ``ceil`` instead, so a positive
  ``embargo_pct`` always yields at least one embargoed bar. That is a
  deliberate, strictly more conservative deviation from the published snippet,
  not a reproduction of it.

* **Nesting** (Varma & Simon, *BMC Bioinformatics* 7:91, 2006; Cawley & Talbot,
  *JMLR* 11:2079-2107, 2010). Scoring a model on the same folds that chose its
  hyperparameters is optimistically biased. On null data with a true 50% error
  rate, Varma & Simon measured a tuned-CV estimate averaging 37.8% (shrunken
  centroids) and 41.7% (SVM); nested CV recovered an estimate "very close to
  that obtained on the independent testing set". The outer test block here is
  therefore never visible to the inner tuning loop -- and, because the outer
  training pool is itself purged and embargoed against that block, the final
  out-of-sample score is not contaminated by label overlap either.

* **Nothing is simulated.** Every number in `LeakageFreeTuningReport` is derived
  from the caller's own evaluation callback or from a closed-form expression.
  The reported haircut is a measurement and may legitimately be zero or
  negative; it is not manufactured to look like evidence of leakage.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
from statistics import NormalDist
from typing import Any, Callable, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Euler-Mascheroni constant, used by the expected-maximum-Sharpe expression.
_EULER_MASCHERONI = 0.5772156649015329

#: Signature of the caller-supplied evaluation callback.
EvalFunc = Callable[[Dict[str, Any], List[int], List[int]], float]


class TuningError(ValueError):
    """Raised on malformed tuning inputs or an infeasible fold configuration."""


@dataclass(frozen=True)
class PurgedSplit:
    """One purged and embargoed train/validation split over a candidate pool."""

    train_indices: List[int]
    val_indices: List[int]
    purged_indices: List[int]
    embargoed_indices: List[int]

    @property
    def purged_count(self) -> int:
        return len(self.purged_indices)

    @property
    def embargoed_count(self) -> int:
        return len(self.embargoed_indices)


@dataclass
class HyperparameterCandidate:
    params: Dict[str, Any]
    inner_cv_mean_sharpe: float
    is_best: bool = False


@dataclass
class LeakageFreeTuningReport:
    best_params: Dict[str, Any]
    out_of_sample_outer_sharpe: float
    best_inner_cv_sharpe: float
    leaky_cv_overestimated_sharpe: float
    leakage_overestimation_haircut: float
    selection_bias_haircut: float
    expected_max_sharpe_under_null: float
    grid_size: int
    total_outer_folds: int
    purged_samples_count: int
    embargoed_samples_count: int
    structural_isolation_verified: bool
    audit_notes: str
    candidate_scores: List[HyperparameterCandidate] = field(default_factory=list)


def expected_max_sharpe_under_null(
    n_trials: int,
    trial_sharpe_stdev: float,
    mean_trial_sharpe: float = 0.0,
) -> float:
    r"""
    Expected maximum Sharpe ratio across ``n_trials`` independent trials.

    Bailey & Lopez de Prado, "The Deflated Sharpe Ratio", *Journal of Portfolio
    Management* 40(5), 94-107 (2014):

    .. math::
        E[\max SR_n] \approx E[SR] + \sqrt{V[SR]}
            \left((1-\gamma) Z^{-1}\!\left[1 - \tfrac{1}{N}\right]
                  + \gamma Z^{-1}\!\left[1 - \tfrac{1}{Ne}\right]\right)

    with :math:`\gamma` the Euler-Mascheroni constant and :math:`Z^{-1}` the
    standard normal quantile function. Use it as a luck floor: a grid search
    whose best score sits at or below this value has produced no evidence of
    skill, only evidence that it tried many combinations.

    ``n_trials == 1`` involves no maximum-selection, so the expression reduces
    to ``mean_trial_sharpe`` rather than diverging at :math:`Z^{-1}[0]`.

    Raises:
        TuningError: if ``n_trials < 1``, either statistic is non-finite, or
            ``trial_sharpe_stdev`` is negative.
    """
    if not isinstance(n_trials, int) or isinstance(n_trials, bool) or n_trials < 1:
        raise TuningError("n_trials must be an int >= 1, got {!r}.".format(n_trials))
    for label, value in (("trial_sharpe_stdev", trial_sharpe_stdev),
                         ("mean_trial_sharpe", mean_trial_sharpe)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            raise TuningError("{} must be a finite number, got {!r}.".format(label, value))
    if trial_sharpe_stdev < 0.0:
        raise TuningError(
            "trial_sharpe_stdev must be non-negative, got {!r}.".format(trial_sharpe_stdev)
        )

    if n_trials == 1:
        return float(mean_trial_sharpe)

    normal = NormalDist()
    quantile_n = normal.inv_cdf(1.0 - 1.0 / n_trials)
    quantile_ne = normal.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    inflation = (1.0 - _EULER_MASCHERONI) * quantile_n + _EULER_MASCHERONI * quantile_ne
    return float(mean_trial_sharpe) + float(trial_sharpe_stdev) * inflation


def _contiguous_blocks(n_items: int, n_blocks: int) -> List[Tuple[int, int]]:
    """
    Partitions ``range(n_items)`` into ``n_blocks`` contiguous half-open blocks,
    distributing the remainder across the leading blocks (``numpy.array_split``
    semantics). Every block is non-empty provided ``n_items >= n_blocks``.

    Chosen over ``n_items // n_blocks`` with the remainder appended to the last
    block, which can leave a final fold substantially larger than the rest and
    skew the mean across folds.
    """
    base, remainder = divmod(n_items, n_blocks)
    blocks: List[Tuple[int, int]] = []
    start = 0
    for b in range(n_blocks):
        end = start + base + (1 if b < remainder else 0)
        blocks.append((start, end))
        start = end
    return blocks


def _canonical_key(params: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """
    Order-independent, hashable identity for a parameter dict.

    Built from ``repr`` of each value so unhashable or unorderable values
    (lists, estimator instances) are handled, and -- unlike round-tripping the
    dict through ``str``/``eval`` -- no caller-supplied text is ever evaluated.
    """
    return tuple(sorted((str(k), repr(v)) for k, v in params.items()))


def _require_finite_score(value: Any, context: str) -> float:
    """Rejects NaN/Inf scores before they silently win or lose every comparison."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TuningError(
            "Evaluation callback returned a non-finite score {!r} for {}. A NaN or Inf "
            "score silently wins or loses every comparison; fix the evaluator or reject "
            "the fold explicitly.".format(value, context)
        )
    return float(value)


class LeakageFreeHyperparameterTunerEngine:
    """
    Purged, embargoed and nested cross-validation for hyperparameter selection.

    The engine partitions a chronologically ordered sample into outer test
    blocks and, for each, tunes the grid strictly inside a purged and embargoed
    outer training pool. It returns both the honest nested out-of-sample score
    and the optimistic score a non-nested, non-purged K-Fold grid search would
    have reported on the same data, so the gap between them is measured rather
    than asserted.

    What it guarantees: no observation of an outer test block, and no
    observation inside that block's purge or embargo zone, appears in any index
    set used to tune or fit for that block. ``structural_isolation_verified``
    reports the result of checking that at run time.

    What it cannot guarantee: that the evaluation callback honours the indices
    it is given. A callback that fits a scaler on the full sample, reads a
    feature built with a centred rolling window, or ignores ``train_indices``
    entirely will still leak. See `feature-engineering-without-leakage`.
    """

    def __init__(
        self,
        outer_folds_count: int = 5,
        inner_folds_count: int = 3,
        purge_window_samples: int = 5,
        embargo_pct: float = 0.01,
    ):
        """
        Args:
            outer_folds_count: Number of outer test blocks (>= 2).
            inner_folds_count: Number of inner tuning folds per outer fold (>= 2).
            purge_window_samples: Label horizon h in bars. Observations in
                ``[val_start - h, val_start)`` are purged. Set this to the label
                horizon actually used to build the target; a smaller value
                leaves overlapping labels in the training set.
            embargo_pct: Fraction of the total sample embargoed after each
                validation block, in ``[0, 1)``. Rounded up, so any positive
                value embargoes at least one bar.

        Raises:
            TuningError: on any out-of-range or wrongly typed argument.
        """
        if (isinstance(outer_folds_count, bool) or not isinstance(outer_folds_count, int)
                or outer_folds_count < 2):
            raise TuningError(
                "outer_folds_count must be an int >= 2, got {!r}.".format(outer_folds_count)
            )
        if (isinstance(inner_folds_count, bool) or not isinstance(inner_folds_count, int)
                or inner_folds_count < 2):
            raise TuningError(
                "inner_folds_count must be an int >= 2, got {!r}.".format(inner_folds_count)
            )
        if (isinstance(purge_window_samples, bool) or not isinstance(purge_window_samples, int)
                or purge_window_samples < 0):
            raise TuningError(
                "purge_window_samples must be an int >= 0, got {!r}.".format(purge_window_samples)
            )
        if (isinstance(embargo_pct, bool) or not isinstance(embargo_pct, (int, float))
                or not math.isfinite(float(embargo_pct)) or not 0.0 <= float(embargo_pct) < 1.0):
            raise TuningError(
                "embargo_pct must be a finite float in [0, 1), got {!r}.".format(embargo_pct)
            )

        self.outer_folds_count = outer_folds_count
        self.inner_folds_count = inner_folds_count
        self.purge_window_samples = purge_window_samples
        self.embargo_pct = float(embargo_pct)

    # ------------------------------------------------------------------
    # Splitting primitives
    # ------------------------------------------------------------------

    def embargo_window(self, n_samples: int) -> int:
        """
        Embargo length in bars: ``ceil(n_samples * embargo_pct)``.

        Snippet 7.2 uses ``int(...)``, which silently disables the embargo when
        ``n_samples * embargo_pct < 1``. Rounding up keeps a positive
        ``embargo_pct`` meaningful on short samples.
        """
        if self.embargo_pct <= 0.0:
            return 0
        return int(math.ceil(n_samples * self.embargo_pct))

    def generate_purged_embargoed_split(
        self,
        candidate_indices: Sequence[int],
        val_start: int,
        val_end: int,
        n_samples: int,
    ) -> PurgedSplit:
        """
        Splits an ascending pool of candidate indices around a validation block.

        Purge and embargo zones are computed in *global* sample coordinates and
        then applied to the pool, so this composes: passing an already-purged
        outer training pool yields an inner split that inherits the outer fold's
        isolation instead of silently reaching back across it.

        Args:
            candidate_indices: Ascending global indices the training set may
                draw from. For an outer split this is ``range(n_samples)``; for
                an inner split it is the outer fold's training pool.
            val_start: First global index of the validation block (inclusive).
            val_end: One past the last global index (exclusive).
            n_samples: Total sample length, used to size the embargo.

        Returns:
            A `PurgedSplit`. ``val_indices`` contains only pool members, so an
            inner split never validates on data the outer fold withheld.

        Raises:
            TuningError: on a non-positive sample length, an empty or
                out-of-range validation block, or a candidate pool that is not
                strictly ascending and within ``[0, n_samples)``.
        """
        if isinstance(n_samples, bool) or not isinstance(n_samples, int) or n_samples <= 0:
            raise TuningError("n_samples must be a positive int, got {!r}.".format(n_samples))
        if not 0 <= val_start < val_end <= n_samples:
            raise TuningError(
                "Validation block [{}, {}) is empty or outside [0, {}).".format(
                    val_start, val_end, n_samples
                )
            )

        purge_start = max(0, val_start - self.purge_window_samples)
        embargo_end = min(n_samples, val_end + self.embargo_window(n_samples))

        train_indices: List[int] = []
        val_indices: List[int] = []
        purged_indices: List[int] = []
        embargoed_indices: List[int] = []

        # The pool must be strictly ascending: the purge and embargo zones are
        # half-open ranges in time order, and callers (including this class's
        # own inner loop) take a block's span from its first and last element.
        # An unsorted or duplicated pool would silently misplace both zones.
        previous = -1
        for i in candidate_indices:
            if isinstance(i, bool) or not isinstance(i, int):
                raise TuningError("candidate_indices must contain ints, got {!r}.".format(i))
            if not 0 <= i < n_samples:
                raise TuningError(
                    "candidate index {} is outside [0, {}).".format(i, n_samples)
                )
            if i <= previous:
                raise TuningError(
                    "candidate_indices must be strictly ascending; {} follows {}.".format(
                        i, previous
                    )
                )
            previous = i

            if val_start <= i < val_end:
                val_indices.append(i)
            elif purge_start <= i < val_start:
                purged_indices.append(i)
            elif val_end <= i < embargo_end:
                embargoed_indices.append(i)
            else:
                train_indices.append(i)

        return PurgedSplit(
            train_indices=train_indices,
            val_indices=val_indices,
            purged_indices=purged_indices,
            embargoed_indices=embargoed_indices,
        )

    def generate_purged_embargoed_indices(
        self,
        n_samples: int,
        val_start: int,
        val_end: int,
    ) -> Tuple[List[int], List[int], int, int]:
        """
        Whole-sample purged and embargoed split.

        Thin wrapper over `generate_purged_embargoed_split` with the candidate
        pool set to the entire sample. Retained for callers that need a single
        non-nested split.

        Returns:
            ``(train_indices, val_indices, purged_count, embargoed_count)``.
        """
        split = self.generate_purged_embargoed_split(
            candidate_indices=range(n_samples),
            val_start=val_start,
            val_end=val_end,
            n_samples=n_samples,
        )
        return (
            split.train_indices,
            split.val_indices,
            split.purged_count,
            split.embargoed_count,
        )

    # ------------------------------------------------------------------
    # Nested tuning
    # ------------------------------------------------------------------

    def execute_leakage_free_tuning(
        self,
        n_samples: int,
        param_grid: List[Dict[str, Any]],
        simulated_eval_func: EvalFunc,
    ) -> LeakageFreeTuningReport:
        """
        Runs purged, embargoed, nested cross-validation over ``param_grid``.

        Args:
            n_samples: Length of the chronologically ordered sample. Index ``i``
                must be the ``i``-th observation in time order; the purge and
                embargo geometry is meaningless on shuffled data.
            param_grid: Non-empty list of candidate parameter dicts.
            simulated_eval_func: ``f(params, train_indices, val_indices) -> float``.
                Must fit on ``train_indices`` **only** -- including every scaler,
                encoder, imputer and feature-selection step -- and score on
                ``val_indices``. Must return a finite score, higher is better.

        Returns:
            A `LeakageFreeTuningReport`. ``leakage_overestimation_haircut`` is a
            measurement, not a guarantee: it is zero when the callback's score
            does not depend on the training set, and can be negative when the
            leaky configuration happens to score worse.

        Raises:
            TuningError: on invalid inputs, a sample too short for the requested
                fold geometry, a fold left with no training data after purging,
                or a non-finite score from the callback.
        """
        if isinstance(n_samples, bool) or not isinstance(n_samples, int) or n_samples <= 0:
            raise TuningError("n_samples must be a positive int, got {!r}.".format(n_samples))
        if not isinstance(param_grid, (list, tuple)) or not param_grid:
            raise TuningError("param_grid must be a non-empty list of parameter dicts.")
        if not all(isinstance(p, dict) for p in param_grid):
            raise TuningError("param_grid must contain only parameter dicts.")
        if not callable(simulated_eval_func):
            raise TuningError("simulated_eval_func must be callable.")
        if n_samples < self.outer_folds_count * self.inner_folds_count:
            raise TuningError(
                "n_samples={} is too small for {} outer x {} inner folds; at least {} "
                "observations are required.".format(
                    n_samples, self.outer_folds_count, self.inner_folds_count,
                    self.outer_folds_count * self.inner_folds_count,
                )
            )

        grid_size = len(param_grid)
        outer_blocks = _contiguous_blocks(n_samples, self.outer_folds_count)

        outer_scores: List[float] = []
        winning_inner_scores: List[float] = []
        grid_inner_scores: Dict[int, List[float]] = {i: [] for i in range(grid_size)}
        best_param_counts: Dict[Tuple[Tuple[str, str], ...], int] = {}
        best_param_first_index: Dict[Tuple[Tuple[str, str], ...], int] = {}
        total_purged = 0
        total_embargoed = 0
        isolation_verified = True

        for fold_idx, (test_start, test_end) in enumerate(outer_blocks):
            # Outer split: the training pool is purged and embargoed against the
            # test block, so the out-of-sample fit never sees a label that
            # overlaps the block it is scored on.
            outer = self.generate_purged_embargoed_split(
                candidate_indices=range(n_samples),
                val_start=test_start,
                val_end=test_end,
                n_samples=n_samples,
            )
            total_purged += outer.purged_count
            total_embargoed += outer.embargoed_count

            pool = outer.train_indices
            if len(pool) < self.inner_folds_count:
                raise TuningError(
                    "Outer fold {}: training pool holds {} observations after purging and "
                    "embargoing, fewer than inner_folds_count={}. Reduce the fold counts, the "
                    "purge window, or the embargo percentage.".format(
                        fold_idx, len(pool), self.inner_folds_count
                    )
                )
            test_block = set(outer.val_indices)

            # Inner loop: tuning sees the outer training pool and nothing else.
            fold_scores: Dict[int, List[float]] = {i: [] for i in range(grid_size)}
            inner_blocks = _contiguous_blocks(len(pool), self.inner_folds_count)
            for inner_idx, (p0, p1) in enumerate(inner_blocks):
                block = pool[p0:p1]
                inner = self.generate_purged_embargoed_split(
                    candidate_indices=pool,
                    val_start=block[0],
                    val_end=block[-1] + 1,
                    n_samples=n_samples,
                )
                total_purged += inner.purged_count
                total_embargoed += inner.embargoed_count

                if not inner.train_indices or not inner.val_indices:
                    raise TuningError(
                        "Outer fold {}, inner fold {}: purging and embargoing left {} training "
                        "and {} validation observations. Widen the folds or shrink the "
                        "purge/embargo.".format(
                            fold_idx, inner_idx, len(inner.train_indices), len(inner.val_indices)
                        )
                    )

                # Structural isolation: the outer test block must be invisible here.
                if (test_block.intersection(inner.train_indices)
                        or test_block.intersection(inner.val_indices)):
                    isolation_verified = False
                    logger.error(
                        "Outer fold %d, inner fold %d: outer test observations reached the inner "
                        "tuning loop. Nested isolation is broken.", fold_idx, inner_idx,
                    )

                for p_idx, params in enumerate(param_grid):
                    # Hand the callback copies: a callback that sorts, pops or
                    # extends the list it is given would otherwise corrupt the
                    # index sets for every later candidate in this fold, after
                    # the isolation check above has already passed.
                    score = _require_finite_score(
                        simulated_eval_func(
                            params, list(inner.train_indices), list(inner.val_indices)
                        ),
                        "outer fold {}, inner fold {}, params {!r}".format(
                            fold_idx, inner_idx, params
                        ),
                    )
                    fold_scores[p_idx].append(score)
                    grid_inner_scores[p_idx].append(score)

            # Deterministic selection: highest mean inner score, ties broken
            # toward the lowest grid index (max returns the first maximal item).
            fold_means = {p_idx: statistics.fmean(s) for p_idx, s in fold_scores.items()}
            best_p_idx = max(range(grid_size), key=lambda i: fold_means[i])
            best_params = param_grid[best_p_idx]
            winning_inner_scores.append(fold_means[best_p_idx])

            key = _canonical_key(best_params)
            best_param_counts[key] = best_param_counts.get(key, 0) + 1
            best_param_first_index.setdefault(key, best_p_idx)

            # Out-of-sample: fit on the purged/embargoed pool, score the test block.
            outer_scores.append(
                _require_finite_score(
                    simulated_eval_func(
                        best_params, list(outer.train_indices), list(outer.val_indices)
                    ),
                    "outer fold {} out-of-sample, params {!r}".format(fold_idx, best_params),
                )
            )

        avg_oos_sharpe = round(statistics.fmean(outer_scores), 6)
        avg_best_inner = round(statistics.fmean(winning_inner_scores), 6)

        # Optimistic baseline: the same grid scored by a non-nested, non-purged,
        # non-embargoed K-Fold, reporting the best in-sample mean -- the number a
        # naive GridSearchCV would print. Measured, never simulated.
        leaky_sharpe = round(
            self._leaky_kfold_best_score(n_samples, param_grid, simulated_eval_func), 6
        )

        haircut = round(leaky_sharpe - avg_oos_sharpe, 6)
        selection_bias = round(avg_best_inner - avg_oos_sharpe, 6)

        candidate_means = [statistics.fmean(grid_inner_scores[i]) for i in range(grid_size)]
        trial_stdev = statistics.stdev(candidate_means) if grid_size > 1 else 0.0
        luck_floor = round(expected_max_sharpe_under_null(grid_size, trial_stdev), 6)

        # Most frequently selected parameter set; ties to the lowest grid index.
        winning_key = max(
            best_param_counts,
            key=lambda k: (best_param_counts[k], -best_param_first_index[k]),
        )
        winning_index = best_param_first_index[winning_key]
        final_best_params = dict(param_grid[winning_index])

        candidate_scores = [
            HyperparameterCandidate(
                params=dict(param_grid[i]),
                inner_cv_mean_sharpe=round(candidate_means[i], 6),
                is_best=(i == winning_index),
            )
            for i in range(grid_size)
        ]

        notes = (
            "Purged nested CV over {} outer x {} inner folds, grid size {}: out-of-sample score "
            "{:.4f}; best inner-CV score {:.4f} (selection bias {:+.4f}); non-nested unpurged "
            "K-Fold best {:.4f} (haircut {:+.4f}). Purged {:,} and embargoed {:,} "
            "observation-slots across all folds. Expected best-of-{} score under the null "
            "(Bailey & Lopez de Prado 2014) is {:.4f}. Structural isolation verified: {}.".format(
                self.outer_folds_count, self.inner_folds_count, grid_size, avg_oos_sharpe,
                avg_best_inner, selection_bias, leaky_sharpe, haircut, total_purged,
                total_embargoed, grid_size, luck_floor, isolation_verified,
            )
        )
        logger.info(notes)
        if not isolation_verified:
            logger.error(
                "Nested isolation check FAILED; treat the out-of-sample score as contaminated."
            )
        if haircut <= 0.0:
            logger.warning(
                "Leakage haircut is %+.4f. Either the evaluation callback ignores its training "
                "indices, or the leaky configuration happened to score worse; it is not evidence "
                "that the pipeline is leakage-free.", haircut,
            )
        if avg_best_inner <= luck_floor:
            logger.warning(
                "Best inner-CV score %.4f does not exceed the best-of-%d luck floor %.4f; the "
                "selected hyperparameters are indistinguishable from the winner of a random "
                "search.", avg_best_inner, grid_size, luck_floor,
            )

        return LeakageFreeTuningReport(
            best_params=final_best_params,
            out_of_sample_outer_sharpe=avg_oos_sharpe,
            best_inner_cv_sharpe=avg_best_inner,
            leaky_cv_overestimated_sharpe=leaky_sharpe,
            leakage_overestimation_haircut=haircut,
            selection_bias_haircut=selection_bias,
            expected_max_sharpe_under_null=luck_floor,
            grid_size=grid_size,
            total_outer_folds=self.outer_folds_count,
            purged_samples_count=total_purged,
            embargoed_samples_count=total_embargoed,
            structural_isolation_verified=isolation_verified,
            audit_notes=notes,
            candidate_scores=candidate_scores,
        )

    def _leaky_kfold_best_score(
        self,
        n_samples: int,
        param_grid: List[Dict[str, Any]],
        eval_func: EvalFunc,
    ) -> float:
        """
        Best mean score from a deliberately leaky K-Fold grid search.

        Reproduces the common mistake: contiguous K-Fold with **no** purge,
        **no** embargo and **no** nesting, reporting the maximum mean score over
        the grid -- the same folds both select and score.

        This isolates the cost of nesting plus purging. Shuffled K-Fold, which
        also destroys time ordering, leaks strictly more, so this baseline is a
        floor on the true overstatement rather than an estimate of it.
        """
        # The index sets do not depend on the parameters, so build them once
        # rather than rebuilding two O(n) lists for every point in the grid.
        folds: List[Tuple[int, int, List[int], List[int]]] = []
        for val_start, val_end in _contiguous_blocks(n_samples, self.outer_folds_count):
            train_idx = list(range(0, val_start)) + list(range(val_end, n_samples))
            val_idx = list(range(val_start, val_end))
            if train_idx and val_idx:
                folds.append((val_start, val_end, train_idx, val_idx))

        best = -math.inf
        for params in param_grid:
            scores: List[float] = []
            for val_start, val_end, train_idx, val_idx in folds:
                scores.append(
                    _require_finite_score(
                        eval_func(params, list(train_idx), list(val_idx)),
                        "leaky K-Fold baseline block [{}, {}), params {!r}".format(
                            val_start, val_end, params
                        ),
                    )
                )
            if scores:
                best = max(best, statistics.fmean(scores))
        if best == -math.inf:
            raise TuningError("Leaky K-Fold baseline produced no scorable folds.")
        return best
