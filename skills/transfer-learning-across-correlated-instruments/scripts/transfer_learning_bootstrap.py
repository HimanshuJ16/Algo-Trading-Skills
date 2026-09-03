"""
transfer-learning-across-correlated-instruments: L2-SP fine-tuning of a linear
forecaster from a liquid source instrument onto a cold-start target instrument,
with an explicitly out-of-sample deployment gate.

Three things in this module are load-bearing, and each exists because the naive
version of it silently produces a number that looks like evidence and is not:

1.  **The evaluation is genuinely out-of-sample.** The target history is split
    *chronologically* -- never shuffled -- into a fit window and a held-out window;
    every model is fit on the fit window only and scored on the held-out window
    only. Scoring a fine-tuned model on the same rows it was fitted on produces an
    in-sample R-squared that rises with model flexibility, which for a cold-start
    instrument with a handful of bars is close to a guarantee of a positive-looking
    result: an ordinary least-squares fit on ``n = D + 1`` rows of pure noise
    interpolates them exactly. A gate driven by that number approves every transfer.

    Scores use the out-of-sample R-squared of Campbell and Thompson (*Review of
    Financial Studies* 21(4), 2008, pp. 1509-1531), whose benchmark is the
    historical mean computed from the **fit window**. Using the held-out window's
    own mean as the benchmark -- what a textbook ``r2_score`` does -- hands the
    benchmark a statistic of the evaluation period, which is information the model
    being evaluated did not have.

    The source model is additionally truncated to bars strictly before the held-out
    window opens. Source and target are correlated by construction -- that is the
    premise of the whole exercise -- so source rows drawn from the evaluation period
    leak the target's evaluation period into the pre-trained weights.

2.  **The L2-SP penalty is solved exactly, not approached by gradient descent.**
    The objective (Li, Grandvalet & Davoine, "Explicit Inductive Bias for Transfer
    Learning with Convolutional Networks", ICML 2018, pp. 2825-2834 -- L2-SP) is

        L(w, b) = (1/N) * sum_i (y_i - z_i . w - b)^2 + lambda * ||w - w_src||^2

    over source-standardized features ``z``. It is quadratic, so it has a closed
    form and there is nothing for an iterative optimizer to buy. Profiling out the
    intercept (``b = ybar - zbar . w``) and centering leaves

        ( Zc^T Zc / N + lambda I ) w = Zc^T yc / N + lambda w_src

    A per-sample update loop that averages the data-fit gradient over ``N`` but
    applies the penalty gradient once per sample -- the obvious way to write it --
    imposes an effective penalty of ``N * lambda``. The visible symptom is that
    shrinkage toward the source *tightens* as the target history grows, so the same
    configured ``lambda`` means something different for every instrument.

    Solved correctly, shrinkage is governed by ``lambda`` alone and is invariant to
    ``N``: with standardized features ``Zc^T Zc / N`` converges to the feature
    covariance, so the departure from the prior settles at roughly
    ``(w_ols - w_src) / (1 + lambda)`` however much target data arrives. That is the
    published L2-SP objective and it is deliberate -- ``lambda`` is defined against
    the *mean* squared error and encodes a fixed prior strength. A desk that wants
    the source prior to wash out asymptotically must itself scale ``l2_penalty``
    with ``1 / n_target``.

    A useful consequence: for ``lambda > 0`` the system is positive definite
    regardless of how few target rows exist, so the transfer model is identified
    where the target-only baseline is not. That asymmetry is the point of the
    method, and it is also why the baseline is reported separately rather than
    assumed to exist.

3.  **The domain-shift metric is named for what it is.** ``calculate_covariate_shift``
    returns the per-feature ``|mu_src - mu_tgt| / sigma_src`` -- a source-scaled
    **standardized mean difference (SMD)**. It is not a Wasserstein distance: the
    first Wasserstein distance is ``inf_{pi in Gamma(u,v)} integral |x - y| dpi``,
    equivalently ``integral |U(x) - V(x)| dx`` over the two CDFs (SciPy,
    ``scipy.stats.wasserstein_distance``), and is sensitive to differences in shape
    and dispersion that an SMD cannot see. Two distributions with identical means
    and wildly different variances score 0.0 here.

    An SMD also compares only marginals, so it detects a shift in ``P(X)`` and says
    nothing about whether ``P(Y|X)`` is stable. Covariate shift in the sense of
    Shimodaira (*Journal of Statistical Planning and Inference* 90(2), 2000) is
    precisely the case ``p_tr(x) != p_te(x)`` *with* ``p_tr(y|x) = p_te(y|x)``; the
    second half is an assumption this module cannot verify and does not test. The
    target-return correlation gate is a weaker, different check -- co-movement of
    the two instruments' outcomes -- and it is not evidence that the two share a
    predictive relationship.

None of the thresholds here are standards. ``min_correlation = 0.60``,
``max_domain_shift = 2.0`` and ``min_correlation_overlap = 30`` are operational
defaults that must be calibrated per desk, instrument class and feature set; see
``references/standards.md``. For scale, the covariate-balance literature treats an
SMD of 0.1-0.25 as "balanced" (Austin, 2009/2011), so 2.0 is a very permissive
ceiling chosen for a setting where some shift is expected and tolerable.

This module holds no state between calls, performs no I/O, and depends on nothing
outside the standard library.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# A Pearson correlation needs two points; a Fisher-z interval needs n > 3.
_MIN_SAMPLES_FOR_CORRELATION = 2
_MIN_SAMPLES_FOR_FISHER_Z = 4

# Two-sided 95% normal quantile, for the Fisher-z correlation interval.
_Z_95 = 1.959963984540054

# A feature whose standard deviation is this small relative to its own level carries
# no information and cannot be standardized: dividing by it turns rounding noise into
# a multi-sigma "signal" in the scaled features.
_MIN_RELATIVE_STD = 1e-12

# Pivot magnitude below which the normal-equation system is treated as singular.
# Reached when features are collinear (or duplicated) and lambda is 0.
_SINGULAR_PIVOT = 1e-12


class MLOpsError(Exception):
    """Raised when a transfer-learning input or intermediate result is unusable."""


@dataclass
class Dataset:
    """
    One instrument's aligned feature/target history.

    Attributes:
        symbol: Instrument identifier, echoed into audit output.
        features: Row-major design matrix, shape ``(N, D)``. Every row must have the
            same length and every entry must be finite.
        targets: Realised target values, length ``N``. Must be finite.
        feature_names: Optional column labels, used to name the worst-shifted feature
            in audit output. Must be empty or of length ``D``.
        timestamps: Optional strictly increasing integer bar timestamps, length ``N``.
            Any consistent unit (epoch nanoseconds, epoch seconds, a bar index) is
            acceptable as long as source and target use the *same* one. Optional for
            the individual fit/predict primitives; **required** by
            :meth:`FinancialTransferLearningEngine.evaluate_transfer_performance`,
            which cannot align two instruments or order a chronological split
            without them.
    """

    symbol: str
    features: List[List[float]]
    targets: List[float]
    feature_names: List[str] = field(default_factory=list)
    timestamps: Optional[List[int]] = None


@dataclass
class TransferConfig:
    """
    Gate thresholds and the L2-SP prior strength.

    None of these are standards. Every one is an operational default to be calibrated
    per desk; see the module docstring and ``references/standards.md``.

    Attributes:
        source_symbol: Expected source identifier, for audit output.
        target_symbol: Expected target identifier, for audit output.
        min_correlation: Floor on the Pearson correlation between source and target
            targets over their aligned overlap. Applied to the point estimate.
        min_correlation_overlap: Minimum number of *aligned* observations the
            correlation must be computed from. Without a floor here the gate is
            routinely decided by a handful of bars, where a sample correlation of 0.9
            is unremarkable under the null.
        l2_penalty: L2-SP ``lambda``, the strength of the pull toward the source
            weights, in units of mean squared error. Must be >= 0, and must be > 0
            when the target fit window has fewer than ``D + 2`` rows, since only the
            penalty makes the system identifiable there.
        max_domain_shift: Ceiling on the *mean* per-feature standardized mean
            difference.
        max_feature_domain_shift: Optional ceiling on the *worst single* feature's
            standardized mean difference. ``None`` disables it. Recommended: a mean
            across ``D`` features hides one catastrophically shifted feature, which
            is a common route to negative transfer.
        test_fraction: Fraction of the target history, taken from the end of the
            series, held out for scoring. Never sampled at random.
        min_test_samples: Minimum rows the held-out window must contain for its
            R-squared to be reported at all.
    """

    source_symbol: str
    target_symbol: str
    min_correlation: float = 0.60
    min_correlation_overlap: int = 30
    l2_penalty: float = 0.1
    max_domain_shift: float = 2.0
    max_feature_domain_shift: Optional[float] = None
    test_fraction: float = 0.30
    min_test_samples: int = 5


@dataclass
class ModelParameters:
    """
    A fitted linear model plus the scaler its features must be transformed with.

    ``feature_means``/``feature_stds`` travel with the weights on purpose: a
    fine-tuned target model inherits the **source** scaler, and separating the two is
    what breaks feature-space alignment.
    """

    weights: List[float]
    bias: float
    feature_means: List[float]
    feature_stds: List[float]


@dataclass
class TransferEvaluation:
    """
    Outcome of one out-of-sample transfer evaluation.

    ``direct_target_r2`` and ``transfer_model_r2`` are Campbell-Thompson out-of-sample
    R-squared values on the held-out target window, both against the same benchmark:
    the mean target over the target fit window. They are therefore directly
    comparable, and either may be negative -- a negative value means the model lost to
    that historical mean. ``direct_target_r2`` and ``transfer_gain_r2`` are ``None``
    when the target-only baseline is not identified, which is common on a genuinely
    cold-start instrument.
    """

    source_symbol: str
    target_symbol: str
    correlation: float
    correlation_overlap: int
    correlation_ci95_low: Optional[float]
    domain_shift_score: float
    max_feature_shift: float
    worst_shift_feature: str
    direct_target_r2: Optional[float]
    transfer_model_r2: float
    transfer_gain_r2: Optional[float]
    n_source_fit: int
    n_target_fit: int
    n_target_test: int
    is_transfer_recommended: bool
    rejection_reasons: List[str] = field(default_factory=list)
    audit_trail: List[str] = field(default_factory=list)


def _validate_dataset(data: Dataset, *, require_timestamps: bool) -> Tuple[int, int]:
    """Validates one dataset and returns ``(N, D)``. Raises on anything unusable."""
    n = len(data.features)
    if n == 0:
        raise MLOpsError(f"{data.symbol}: dataset is empty.")
    if len(data.targets) != n:
        raise MLOpsError(
            f"{data.symbol}: {n} feature rows but {len(data.targets)} targets."
        )

    d = len(data.features[0])
    if d == 0:
        raise MLOpsError(f"{data.symbol}: feature rows have zero columns.")

    for i, row in enumerate(data.features):
        if len(row) != d:
            raise MLOpsError(
                f"{data.symbol}: ragged feature matrix -- row 0 has {d} columns, "
                f"row {i} has {len(row)}."
            )
        for j, value in enumerate(row):
            if not math.isfinite(value):
                raise MLOpsError(
                    f"{data.symbol}: non-finite feature at row {i}, column {j} "
                    f"({value!r}). NaN propagates silently into R-squared and then "
                    f"reads as a rejection, so it is refused here instead."
                )
    for i, value in enumerate(data.targets):
        if not math.isfinite(value):
            raise MLOpsError(f"{data.symbol}: non-finite target at row {i} ({value!r}).")

    if data.feature_names and len(data.feature_names) != d:
        raise MLOpsError(
            f"{data.symbol}: {len(data.feature_names)} feature names for {d} columns."
        )

    if require_timestamps:
        if data.timestamps is None:
            raise MLOpsError(
                f"{data.symbol}: timestamps are required to align two instruments and "
                f"to order a chronological split. Supply Dataset.timestamps."
            )
        if len(data.timestamps) != n:
            raise MLOpsError(
                f"{data.symbol}: {n} rows but {len(data.timestamps)} timestamps."
            )
        for i in range(1, n):
            if data.timestamps[i] <= data.timestamps[i - 1]:
                raise MLOpsError(
                    f"{data.symbol}: timestamps must be strictly increasing; index "
                    f"{i} ({data.timestamps[i]}) does not follow index {i - 1} "
                    f"({data.timestamps[i - 1]}). Duplicated or out-of-order bars "
                    f"corrupt both the alignment and the chronological split."
                )

    return n, d


def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """
    Solves ``matrix @ x = rhs`` by Gaussian elimination with partial pivoting.

    ``matrix`` is ``D x D`` where ``D`` is the feature count, so the cubic cost is
    irrelevant next to the ``O(N * D^2)`` Gram accumulation that produced it. Raises
    :class:`MLOpsError` rather than returning a garbage solution when singular.
    """
    d = len(rhs)
    aug = [list(matrix[i]) + [rhs[i]] for i in range(d)]

    for col in range(d):
        pivot_row = max(range(col, d), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < _SINGULAR_PIVOT:
            raise MLOpsError(
                "Normal-equation system is singular: features are collinear or "
                "duplicated. Drop the redundant feature, or set a positive "
                "l2_penalty, which makes the system positive definite."
            )
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]

        pivot = aug[col][col]
        for row in range(col + 1, d):
            factor = aug[row][col] / pivot
            if factor == 0.0:
                continue
            for k in range(col, d + 1):
                aug[row][k] -= factor * aug[col][k]

    solution = [0.0] * d
    for row in range(d - 1, -1, -1):
        total = aug[row][d] - sum(aug[row][k] * solution[k] for k in range(row + 1, d))
        solution[row] = total / aug[row][row]
    return solution


class FinancialTransferLearningEngine:
    """
    Transfer-learning engine for linear forecasters on correlated instruments.

    Stateless: every method takes everything it needs as an argument, so two
    evaluations can never contaminate one another.
    """

    # ---------------------------------------------------------------- diagnostics

    @staticmethod
    def calculate_correlation(x: Sequence[float], y: Sequence[float]) -> float:
        """
        Pearson correlation of two series the caller has **already aligned**.

        Returns 0.0 when either series is constant -- the coefficient is undefined
        there, and 0.0 is the fail-closed answer for a gate whose thresholds are all
        lower bounds.
        """
        n = len(x)
        if len(y) != n or n < _MIN_SAMPLES_FOR_CORRELATION:
            raise MLOpsError(
                f"Correlation needs two equal-length series of at least "
                f"{_MIN_SAMPLES_FOR_CORRELATION} points; got {n} and {len(y)}."
            )

        mean_x = sum(x) / n
        mean_y = sum(y) / n
        num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        den_x = math.sqrt(sum((x[i] - mean_x) ** 2 for i in range(n)))
        den_y = math.sqrt(sum((y[i] - mean_y) ** 2 for i in range(n)))

        if den_x == 0.0 or den_y == 0.0:
            logger.warning("Correlation undefined (a series is constant); returning 0.0.")
            return 0.0
        # Clamp against |r| > 1 from floating-point accumulation, which would make the
        # Fisher-z transform below raise.
        return max(-1.0, min(1.0, num / (den_x * den_y)))

    @staticmethod
    def correlation_ci95_lower(r: float, n: int) -> Optional[float]:
        """
        Lower bound of the two-sided 95% Fisher-z interval for a correlation.

        ``z = artanh(r)`` is approximately normal with standard error
        ``1 / sqrt(n - 3)``; the bound is ``tanh(z - 1.96 * se)``. Reported so an
        auditor can see how little a gate decided on a short overlap actually
        established. Returns ``None`` for ``n <= 3`` or ``|r| == 1``, where the
        transform is undefined.
        """
        if n < _MIN_SAMPLES_FOR_FISHER_Z or abs(r) >= 1.0:
            return None
        z = math.atanh(r)
        se = 1.0 / math.sqrt(n - 3)
        return math.tanh(z - _Z_95 * se)

    @staticmethod
    def calculate_covariate_shift(
        source_features: List[List[float]],
        target_features: List[List[float]],
    ) -> List[float]:
        """
        Per-feature source-scaled standardized mean difference (SMD).

        Returns ``|mu_src - mu_tgt| / sigma_src`` for each feature, using the
        **sample** standard deviation (``ddof = 1``) of the source. Callers wanting
        the single scalar of ``references/standards.md`` take the mean; see
        :meth:`mean_covariate_shift`.

        This is not a Wasserstein distance, and it compares marginals only -- see the
        module docstring. A feature whose source distribution is degenerate is
        rejected rather than divided through a floor, because a floor turns a constant
        column into an arbitrarily large fake shift.
        """
        if not source_features or not target_features:
            raise MLOpsError("Covariate shift needs non-empty source and target matrices.")

        n_src, n_tgt = len(source_features), len(target_features)
        if n_src < 2:
            raise MLOpsError(
                f"Source needs at least 2 rows for a sample standard deviation; "
                f"got {n_src}."
            )

        d = len(source_features[0])
        if len(target_features[0]) != d:
            raise MLOpsError(
                f"Feature-space mismatch: source has {d} columns, target has "
                f"{len(target_features[0])}. Transferring across mismatched feature "
                f"spaces silently reuses source weights for absent features."
            )
        for label, matrix in (("source", source_features), ("target", target_features)):
            for i, row in enumerate(matrix):
                if len(row) != d:
                    raise MLOpsError(
                        f"Ragged {label} matrix: row 0 has {d} columns, row {i} has "
                        f"{len(row)}."
                    )

        shifts: List[float] = []
        for j in range(d):
            src_col = [source_features[i][j] for i in range(n_src)]
            tgt_col = [target_features[i][j] for i in range(n_tgt)]

            src_mean = sum(src_col) / n_src
            tgt_mean = sum(tgt_col) / n_tgt
            try:
                variance = sum((v - src_mean) ** 2 for v in src_col) / (n_src - 1)
            except OverflowError as exc:
                raise MLOpsError(
                    f"Source feature {j} overflows when squared ({exc}). Its magnitude "
                    f"is far outside a usable range -- a units error."
                ) from exc
            if not math.isfinite(variance):
                raise MLOpsError(
                    f"Source feature {j} has a non-finite variance; its magnitude is "
                    f"far outside a usable range."
                )
            src_std = math.sqrt(variance)

            if src_std <= _MIN_RELATIVE_STD * max(1.0, abs(src_mean)):
                raise MLOpsError(
                    f"Source feature {j} is constant (std={src_std:.3e}); its "
                    f"standardized mean difference is undefined. Drop the column."
                )
            shifts.append(abs(src_mean - tgt_mean) / src_std)

        return shifts

    @classmethod
    def mean_covariate_shift(
        cls,
        source_features: List[List[float]],
        target_features: List[List[float]],
    ) -> float:
        """Mean per-feature SMD -- the scalar of ``references/standards.md``."""
        shifts = cls.calculate_covariate_shift(source_features, target_features)
        return sum(shifts) / len(shifts)

    # -------------------------------------------------------------------- fitting

    @staticmethod
    def _standardize(
        features: List[List[float]], symbol: str
    ) -> Tuple[List[float], List[float]]:
        """
        Population mean/standard deviation per column (``ddof = 0``), the scaler
        convention. Rejects degenerate columns instead of flooring the divisor.
        """
        n, d = len(features), len(features[0])
        means = [sum(features[i][j] for i in range(n)) / n for j in range(d)]
        stds: List[float] = []
        for j in range(d):
            # Values are validated finite, but their squares need not be: a feature
            # around 1e180 overflows here. That is a units error, and saying so beats
            # an unexplained OverflowError from deep inside a variance sum.
            try:
                var = sum((features[i][j] - means[j]) ** 2 for i in range(n)) / n
            except OverflowError as exc:
                raise MLOpsError(
                    f"{symbol}: feature {j} overflows when squared ({exc}). Its "
                    f"magnitude is far outside a usable range -- a units error, not a "
                    f"modelling choice."
                ) from exc
            if not math.isfinite(var):
                raise MLOpsError(
                    f"{symbol}: feature {j} has a non-finite variance. Its magnitude is "
                    f"far outside a usable range."
                )
            std = math.sqrt(var)
            if std <= _MIN_RELATIVE_STD * max(1.0, abs(means[j])):
                raise MLOpsError(
                    f"{symbol}: feature {j} is constant (std={std:.3e}) and cannot be "
                    f"standardized. Drop the column before fitting."
                )
            stds.append(std)
        return means, stds

    @staticmethod
    def _apply_scaler(
        features: List[List[float]], params: ModelParameters
    ) -> List[List[float]]:
        d = len(params.weights)
        if len(features[0]) != d:
            raise MLOpsError(
                f"Feature-space mismatch: model has {d} weights, data has "
                f"{len(features[0])} columns."
            )
        return [
            [(row[j] - params.feature_means[j]) / params.feature_stds[j] for j in range(d)]
            for row in features
        ]

    @staticmethod
    def _solve_penalized(
        scaled_x: List[List[float]],
        y: Sequence[float],
        l2_penalty: float,
        prior_weights: Sequence[float],
    ) -> Tuple[List[float], float]:
        """
        Exact minimiser of ``(1/N)||y - Zw - b||^2 + lambda * ||w - w_prior||^2``.

        Profiles out the intercept and solves the centred system
        ``(Zc^T Zc / N + lambda I) w = Zc^T yc / N + lambda w_prior``, then recovers
        ``b = ybar - zbar . w``. With ``lambda = 0`` this is ordinary least squares
        and ``prior_weights`` has no effect.
        """
        n, d = len(scaled_x), len(scaled_x[0])
        col_means = [sum(scaled_x[i][j] for i in range(n)) / n for j in range(d)]
        y_mean = sum(y) / n

        centered_x = [[scaled_x[i][j] - col_means[j] for j in range(d)] for i in range(n)]
        centered_y = [y[i] - y_mean for i in range(n)]

        # Inputs are validated finite, but their squares need not be: a feature around
        # 1e180 overflows the Gram matrix. Turn that into a named error rather than a
        # raw OverflowError, or worse an inf that solves to a silent NaN model.
        gram = [[0.0] * d for _ in range(d)]
        rhs = [0.0] * d
        try:
            for j in range(d):
                for k in range(j, d):
                    total = sum(centered_x[i][j] * centered_x[i][k] for i in range(n)) / n
                    gram[j][k] = total
                    gram[k][j] = total
                rhs[j] = sum(centered_x[i][j] * centered_y[i] for i in range(n)) / n
        except OverflowError as exc:
            raise MLOpsError(
                f"Feature moments overflowed ({exc}). Feature magnitudes are far outside "
                f"a usable range -- this is a units error, not a modelling choice."
            ) from exc

        for j in range(d):
            gram[j][j] += l2_penalty
            rhs[j] += l2_penalty * prior_weights[j]

        if any(not math.isfinite(v) for row in gram for v in row) or any(
            not math.isfinite(v) for v in rhs
        ):
            raise MLOpsError(
                "Feature moments are not finite. Feature magnitudes are far outside a "
                "usable range -- this is a units error, not a modelling choice."
            )

        weights = _solve(gram, rhs)
        bias = y_mean - sum(col_means[j] * weights[j] for j in range(d))
        if any(not math.isfinite(w) for w in weights) or not math.isfinite(bias):
            raise MLOpsError(
                "Solved model parameters are not finite. A NaN model scores NaN, which "
                "reads as a quiet rejection downstream, so it is refused here."
            )
        return weights, bias

    def fit_source_model(self, source_data: Dataset) -> ModelParameters:
        """
        Fits the pre-trained source model: ordinary least squares on standardized
        source features, solved in closed form.

        Requires ``N >= D + 2`` so the fit has at least one residual degree of
        freedom. The returned scaler is the one every downstream target model must
        reuse -- re-standardizing the target with its own statistics is what breaks
        feature-space alignment.
        """
        n, d = _validate_dataset(source_data, require_timestamps=False)
        if n < d + 2:
            raise MLOpsError(
                f"{source_data.symbol}: {n} rows for {d} features. An unregularized "
                f"fit needs at least {d + 2} rows to leave a residual degree of freedom."
            )

        means, stds = self._standardize(source_data.features, source_data.symbol)
        scaled_x = [
            [(row[j] - means[j]) / stds[j] for j in range(d)]
            for row in source_data.features
        ]
        weights, bias = self._solve_penalized(
            scaled_x, source_data.targets, l2_penalty=0.0, prior_weights=[0.0] * d
        )

        logger.info(
            "Fitted source model on %s (%d rows, %d features).", source_data.symbol, n, d
        )
        return ModelParameters(
            weights=weights, bias=bias, feature_means=means, feature_stds=stds
        )

    def fine_tune_target_model(
        self,
        source_params: ModelParameters,
        target_data: Dataset,
        config: TransferConfig,
    ) -> ModelParameters:
        """
        L2-SP fine-tune of ``source_params`` onto the target history, in closed form.

        Target features are transformed with the **source** scaler, which
        ``source_params`` carries for exactly this reason. The target's dimensionality
        must match the source model's: a shorter target row would otherwise leave
        trailing source weights untouched and in force against features that do not
        exist.
        """
        n, d = _validate_dataset(target_data, require_timestamps=False)
        if config.l2_penalty < 0.0:
            raise MLOpsError(f"l2_penalty must be >= 0; got {config.l2_penalty}.")
        if d != len(source_params.weights):
            raise MLOpsError(
                f"{target_data.symbol}: target has {d} features but the source model "
                f"has {len(source_params.weights)} weights. Align the feature space "
                f"before transferring."
            )
        if n < d + 2 and config.l2_penalty == 0.0:
            raise MLOpsError(
                f"{target_data.symbol}: {n} rows for {d} features with l2_penalty=0 is "
                f"unidentifiable. A positive l2_penalty is what makes the cold-start "
                f"fit well posed."
            )

        scaled_x = self._apply_scaler(target_data.features, source_params)
        weights, bias = self._solve_penalized(
            scaled_x,
            target_data.targets,
            l2_penalty=config.l2_penalty,
            prior_weights=source_params.weights,
        )

        logger.info(
            "Fine-tuned %s from %d rows (lambda=%.4g).",
            target_data.symbol,
            n,
            config.l2_penalty,
        )
        return ModelParameters(
            weights=weights,
            bias=bias,
            feature_means=source_params.feature_means,
            feature_stds=source_params.feature_stds,
        )

    def predict(
        self, params: ModelParameters, features: List[List[float]]
    ) -> List[float]:
        """Applies the model's own scaler, then the linear map."""
        if not features:
            raise MLOpsError("predict() requires at least one feature row.")
        scaled_x = self._apply_scaler(features, params)
        d = len(params.weights)
        return [
            sum(params.weights[j] * row[j] for j in range(d)) + params.bias
            for row in scaled_x
        ]

    # -------------------------------------------------------------------- scoring

    def calculate_r2(self, y_true: Sequence[float], y_pred: Sequence[float]) -> float:
        """
        In-sample coefficient of determination, benchmarked against the mean of
        ``y_true`` itself.

        Correct for scoring a fit on the rows it was fitted on. For a held-out window
        use :meth:`calculate_oos_r2`, whose benchmark does not depend on the
        evaluation period.
        """
        n = len(y_true)
        if n == 0 or len(y_pred) != n:
            raise MLOpsError(
                f"R-squared needs two equal-length non-empty series; got {n} and "
                f"{len(y_pred)}."
            )
        return self.calculate_oos_r2(y_true, y_pred, sum(y_true) / n)

    @staticmethod
    def calculate_oos_r2(
        y_true: Sequence[float], y_pred: Sequence[float], benchmark: float
    ) -> float:
        """
        Campbell-Thompson (2008) out-of-sample R-squared against a fixed benchmark.

        ``1 - sum (y - yhat)^2 / sum (y - benchmark)^2``, where ``benchmark`` is the
        historical mean from the *fit* window. Negative values are meaningful and
        expected: they say the model lost to that historical mean.
        """
        n = len(y_true)
        if n == 0 or len(y_pred) != n:
            raise MLOpsError(
                f"R-squared needs two equal-length non-empty series; got {n} and "
                f"{len(y_pred)}."
            )
        if not math.isfinite(benchmark):
            raise MLOpsError(f"Benchmark must be finite; got {benchmark!r}.")

        ss_res = sum((y_true[i] - y_pred[i]) ** 2 for i in range(n))
        ss_tot = sum((y - benchmark) ** 2 for y in y_true)
        if ss_tot == 0.0:
            raise MLOpsError(
                "Every held-out target equals the benchmark, so R-squared is "
                "undefined. This is a degenerate evaluation window, not a score of 0."
            )
        return 1.0 - (ss_res / ss_tot)

    # ----------------------------------------------------------------- evaluation

    def evaluate_transfer_performance(
        self,
        source_data: Dataset,
        target_data: Dataset,
        config: TransferConfig,
    ) -> TransferEvaluation:
        """
        Runs the full gate: alignment, correlation, domain shift, and a genuinely
        out-of-sample comparison of the transferred model against a target-only
        baseline.

        Both datasets must carry ``timestamps``. The target history is split
        chronologically at ``1 - test_fraction``; the source history is truncated to
        bars strictly before the held-out window opens, so pre-training cannot see the
        evaluation period through the source instrument.

        A transfer is recommended only when *all* of the following hold, and every
        failure is listed in ``rejection_reasons``:

        1. the aligned overlap is at least ``min_correlation_overlap`` bars;
        2. the correlation over that overlap is at least ``min_correlation``;
        3. mean SMD is at most ``max_domain_shift`` (and, if configured, the worst
           feature is at most ``max_feature_domain_shift``);
        4. the transferred model beats the historical-mean benchmark out of sample
           (``transfer_model_r2 > 0``);
        5. it also beats the target-only baseline, when that baseline is identified.

        Condition 4 is not redundant with 5. Out of sample both R-squared values can
        be negative, and "beats a catastrophic baseline" is then satisfied by a model
        that is itself worse than predicting the fit-window mean.
        """
        n_src, d_src = _validate_dataset(source_data, require_timestamps=True)
        n_tgt, d_tgt = _validate_dataset(target_data, require_timestamps=True)
        source_ts: List[int] = source_data.timestamps or []
        target_ts: List[int] = target_data.timestamps or []

        if d_src != d_tgt:
            raise MLOpsError(
                f"Feature-space mismatch: {source_data.symbol} has {d_src} columns, "
                f"{target_data.symbol} has {d_tgt}."
            )
        if not 0.0 < config.test_fraction < 1.0:
            raise MLOpsError(
                f"test_fraction must be in (0, 1); got {config.test_fraction}."
            )

        audit = [
            f"Evaluating transfer {source_data.symbol} -> {target_data.symbol} "
            f"({n_src} source rows, {n_tgt} target rows, {d_tgt} features)."
        ]
        reasons: List[str] = []

        # 1. Chronological split of the target. Never shuffled.
        n_test = int(round(n_tgt * config.test_fraction))
        n_fit = n_tgt - n_test
        if n_test < config.min_test_samples:
            raise MLOpsError(
                f"{target_data.symbol}: test_fraction={config.test_fraction} over "
                f"{n_tgt} rows yields {n_test} held-out rows, below "
                f"min_test_samples={config.min_test_samples}. There is not enough "
                f"target history to evaluate this transfer honestly."
            )
        if n_fit < 2:
            raise MLOpsError(
                f"{target_data.symbol}: only {n_fit} rows left to fit on after the split."
            )

        test_start_ts = target_ts[n_fit]
        tgt_fit = Dataset(
            target_data.symbol,
            target_data.features[:n_fit],
            target_data.targets[:n_fit],
            target_data.feature_names,
            target_ts[:n_fit],
        )
        test_x = target_data.features[n_fit:]
        test_y = target_data.targets[n_fit:]
        audit.append(
            f"Chronological split: {n_fit} fit rows, {n_test} held-out rows from "
            f"timestamp {test_start_ts}."
        )

        # 2. Truncate the source to bars strictly before the held-out window. The two
        #    instruments are correlated by premise, so source rows from the evaluation
        #    period leak it into the pre-trained weights.
        n_src_fit = sum(1 for ts in source_ts if ts < test_start_ts)
        if n_src_fit < n_src:
            audit.append(
                f"Dropped {n_src - n_src_fit} source rows at or after {test_start_ts} "
                f"to keep pre-training out of the target's evaluation window."
            )
        if n_src_fit < d_src + 2:
            raise MLOpsError(
                f"{source_data.symbol}: only {n_src_fit} source rows precede the "
                f"target's held-out window, below the {d_src + 2} an unregularized fit "
                f"needs. Extend the source history or shrink test_fraction."
            )
        src_fit = Dataset(
            source_data.symbol,
            source_data.features[:n_src_fit],
            source_data.targets[:n_src_fit],
            source_data.feature_names,
            source_ts[:n_src_fit],
        )

        # 3. Correlation over the timestamp-aligned overlap of the two fit windows.
        #    Slicing a prefix of the source instead would correlate the source's oldest
        #    bars against the target's, which is not a correlation of anything.
        src_by_ts = {ts: i for i, ts in enumerate(source_ts[:n_src_fit])}
        overlap = [
            (src_by_ts[ts], i) for i, ts in enumerate(target_ts[:n_fit]) if ts in src_by_ts
        ]
        n_overlap = len(overlap)
        if n_overlap < _MIN_SAMPLES_FOR_CORRELATION:
            raise MLOpsError(
                f"Only {n_overlap} timestamps are shared between {source_data.symbol} "
                f"and {target_data.symbol} in the fit window, so the correlation gate "
                f"cannot be evaluated. Check that both series use the same timestamp "
                f"unit and trading calendar."
            )
        corr = self.calculate_correlation(
            [src_fit.targets[s] for s, _ in overlap],
            [tgt_fit.targets[t] for _, t in overlap],
        )
        ci_low = self.correlation_ci95_lower(corr, n_overlap)
        audit.append(
            f"Aligned target correlation: {corr:+.3f} over {n_overlap} shared bars"
            + (f" (95% CI lower bound {ci_low:+.3f})." if ci_low is not None else ".")
        )
        if n_overlap < config.min_correlation_overlap:
            reasons.append(
                f"Correlation overlap {n_overlap} < min_correlation_overlap "
                f"{config.min_correlation_overlap}."
            )
        if corr < config.min_correlation:
            reasons.append(
                f"Correlation {corr:+.3f} < min_correlation {config.min_correlation}."
            )

        # 4. Domain shift, computed on the fit windows only.
        shifts = self.calculate_covariate_shift(src_fit.features, tgt_fit.features)
        mean_shift = sum(shifts) / len(shifts)
        worst_idx = max(range(len(shifts)), key=lambda j: shifts[j])
        worst_name = (
            target_data.feature_names[worst_idx]
            if target_data.feature_names
            else f"feature_{worst_idx}"
        )
        audit.append(
            f"Standardized mean difference: mean {mean_shift:.3f}, worst "
            f"{shifts[worst_idx]:.3f} on '{worst_name}'."
        )
        if mean_shift > config.max_domain_shift:
            reasons.append(
                f"Mean SMD {mean_shift:.3f} > max_domain_shift {config.max_domain_shift}."
            )
        if (
            config.max_feature_domain_shift is not None
            and shifts[worst_idx] > config.max_feature_domain_shift
        ):
            reasons.append(
                f"Feature '{worst_name}' SMD {shifts[worst_idx]:.3f} > "
                f"max_feature_domain_shift {config.max_feature_domain_shift}."
            )

        # 5. Benchmark is the fit-window mean -- Campbell-Thompson -- not the held-out
        #    window's own mean, which the model being scored never had.
        benchmark = sum(tgt_fit.targets) / n_fit

        src_params = self.fit_source_model(src_fit)
        transfer_params = self.fine_tune_target_model(src_params, tgt_fit, config)
        transfer_r2 = self.calculate_oos_r2(
            test_y, self.predict(transfer_params, test_x), benchmark
        )
        audit.append(f"Transferred model OOS R-squared: {transfer_r2:+.4f}")

        # 6. Target-only baseline. Unregularized by design -- it is the model a desk
        #    would have without transfer -- and on a genuinely cold-start instrument it
        #    is often simply unidentified. That is reported, not papered over.
        direct_r2: Optional[float] = None
        try:
            direct_params = self.fit_source_model(tgt_fit)
            direct_r2 = self.calculate_oos_r2(
                test_y, self.predict(direct_params, test_x), benchmark
            )
            audit.append(f"Target-only baseline OOS R-squared: {direct_r2:+.4f}")
        except MLOpsError as exc:
            audit.append(f"Target-only baseline not identified ({exc}).")

        gain = None if direct_r2 is None else transfer_r2 - direct_r2
        if gain is not None:
            audit.append(f"Transfer OOS R-squared gain: {gain:+.4f}")

        if transfer_r2 <= 0.0:
            reasons.append(
                f"Transferred model OOS R-squared {transfer_r2:+.4f} <= 0: it loses to "
                f"the fit-window historical mean."
            )
        if direct_r2 is not None and transfer_r2 <= direct_r2:
            reasons.append(
                f"No gain over target-only baseline ({transfer_r2:+.4f} <= "
                f"{direct_r2:+.4f})."
            )

        recommended = not reasons
        audit.append(
            "Recommendation: APPROVED"
            if recommended
            else "Recommendation: REJECTED -- " + "; ".join(reasons)
        )
        logger.info(
            "Transfer %s -> %s: OOS R2=%+.4f, approved=%s",
            source_data.symbol,
            target_data.symbol,
            transfer_r2,
            recommended,
        )

        return TransferEvaluation(
            source_symbol=source_data.symbol,
            target_symbol=target_data.symbol,
            correlation=corr,
            correlation_overlap=n_overlap,
            correlation_ci95_low=ci_low,
            domain_shift_score=mean_shift,
            max_feature_shift=shifts[worst_idx],
            worst_shift_feature=worst_name,
            direct_target_r2=direct_r2,
            transfer_model_r2=transfer_r2,
            transfer_gain_r2=gain,
            n_source_fit=n_src_fit,
            n_target_fit=n_fit,
            n_target_test=n_test,
            is_transfer_recommended=recommended,
            rejection_reasons=reasons,
            audit_trail=audit,
        )
