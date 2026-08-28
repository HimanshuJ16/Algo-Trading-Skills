"""
quantile-regression-for-uncertainty-aware-signals: linear conditional-quantile model
fitted by Pinball-loss stochastic subgradient descent, with monotone rearrangement of
crossing quantiles, coverage diagnostics, and an uncertainty-scaled position sizer.

Instead of a single point forecast E[Y|X], three conditional quantiles of the forward
return are estimated -- a lower tail, a central (median) forecast, and an upper tail.
The spread between the outer two is the model's own statement of how uncertain it is,
and position size is scaled by it.

Pinball ("check") loss, the asymmetric piecewise-linear loss whose minimiser is the
conditional tau-quantile (Koenker & Bassett 1978, Econometrica 46(1), 33-50):

    L_tau(y, y_hat) = tau * (y - y_hat)        if y >= y_hat
                    = (1 - tau) * (y_hat - y)  if y <  y_hat

equivalently L_tau(y, y_hat) = (y - y_hat) * (tau - 1{y < y_hat}). Up to a monotone
transform of the arguments this is the *only* family of losses consistent for the
quantile functional (Gneiting 2011, JASA 106(494), 746-762): squared error is consistent
for the mean and for no quantile, so an MSE-trained model cannot be repurposed as a tail
estimator no matter how it is thresholded.

Subgradient with respect to the prediction:

    dL/dy_hat = -tau        if y >= y_hat
              = (1 - tau)   if y <  y_hat

Three properties make naive SGD on this loss fail, and each is addressed explicitly:

1. *The subgradient never shrinks.* Its magnitude is tau or 1 - tau regardless of how
   close the fit is, so a constant step size produces a limit cycle of width O(eta)
   around the optimum forever; more data does not shrink the error, and the leftover
   oscillation lands directly in the estimated band width -- the one number this model
   exists to produce. Step sizes therefore follow a Robbins-Monro schedule (Robbins &
   Monro 1951, Ann. Math. Statist. 22(3), 400-407):

       eta_t = learning_rate * target_scale / (1 + t) ** decay_power

   With ``decay_power`` in (0.5, 1.0] this satisfies sum(eta_t) = inf and
   sum(eta_t^2) < inf, the classical convergence conditions.

2. *A decaying schedule has a finite travel budget.* sum(eta_t) diverges, but slowly, so
   an estimator starting from zero may never reach a distant optimum. The intercepts are
   therefore warm-started at the empirical *marginal* quantiles of the training targets,
   which is the exact answer when no feature is informative and a good starting point
   when they are, leaving SGD only the conditional structure to learn.

3. *The loss is non-smooth, so the last iterate is noisy even when converged.* The
   deployed coefficients are the Polyak-Ruppert average of the final ``averaging_tail``
   fraction of iterates (Polyak & Juditsky 1992, SIAM J. Control Optim. 30(4), 838-855),
   which is asymptotically optimal and far steadier than any single iterate.

Features are standardised internally using training-fold statistics only, because a
single global step size cannot serve features of wildly different magnitudes, and the
step size is expressed as a multiple of the training targets' interquartile range so
that the default is meaningful whether targets are returns (~1e-2) or prices (~1e2).

Crossing quantiles (q_lower > q_upper) are repaired by sorting -- the monotone
rearrangement of Chernozhukov, Fernandez-Val & Galichon (2010), Econometrica 78(3),
1093-1125, which is guaranteed to be weakly closer to the true monotone quantile curve
in finite samples than the unsorted estimate.

Limitations (documented, deliberate):

- **Linear in the supplied features.** Non-linear quantile structure must be supplied as
  engineered features; there is no basis expansion here. For production work at scale,
  gradient-boosted quantile objectives or a linear-programming quantile solver will fit
  better and faster; what this module is for is the surrounding discipline -- crossing
  repair, coverage measurement, and refusing to size a degenerate band.
- **Look-ahead is the caller's responsibility.** Every target must be a return realised
  strictly *after* all of its features were observable. Nothing here can detect a target
  that overlaps its own feature window.
- **Non-stationarity is not handled.** ``fit`` is a complete refit over whatever sample
  it is given. Re-fit walk-forward; a band estimated in a calm regime understates risk
  in a volatile one, and a stale band understates it silently.
- **Marginal coverage only.** ``calibration_report`` measures unconditional coverage over
  the sample supplied. Coverage can be correct on average and badly wrong inside a
  specific regime, which is the case that costs money.
- **Out-of-domain inputs saturate rather than fail.** Both the median forecast and the
  band scale with the features, so their *ratio* -- and therefore the position size --
  stays at the cap however far outside the fitting range the input goes. ``predict``
  reports ``is_extrapolating`` and ``max_feature_zscore``; it does not block, because mild
  extrapolation is routine as features drift and hard-zeroing would silently kill a live
  strategy.
- **The size multiplier is a heuristic, not an optimal bet size.** ``|q_central| / width``
  is a signal-to-uncertainty ratio -- not a Kelly fraction, not a risk-budgeted
  allocation, and not a substitute for independent, non-bypassable pre-trade risk limits.
"""
import logging
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Lower / central / upper quantile levels. The central level supplies the signal
#: direction; the outer pair defines the uncertainty band.
DEFAULT_QUANTILES: Tuple[float, float, float] = (0.10, 0.50, 0.90)

#: Initial step size, expressed as a multiple of the training targets' interquartile
#: range. Because the step is rescaled by the target's own dispersion, this default is
#: unit-free and applies equally to return-scale and price-scale targets.
DEFAULT_LEARNING_RATE = 0.2

#: Robbins-Monro decay exponent. Any value in (0.5, 1.0] satisfies both convergence
#: conditions; 0.6 decays slowly enough to keep a usable travel budget.
DEFAULT_DECAY_POWER = 0.6

#: Fraction of the final iterates entering the Polyak-Ruppert average. 0.0 deploys the
#: last iterate instead, which is measurably noisier.
DEFAULT_AVERAGING_TAIL = 0.5

#: Narrowest band treated as a measurement rather than a degenerate model, in the
#: target's own units (1e-4 = 1 bp of return). A band at or below this is NOT a
#: high-confidence forecast -- it is a collapsed or degenerate model -- and the sizer
#: refuses to trade it. Calibrate to the target's units before use.
DEFAULT_MIN_UNCERTAINTY_WIDTH = 1e-4

#: Standardised-feature magnitude beyond which a prediction is flagged as extrapolating
#: outside the fitting sample. Because a linear model's median forecast and its band both
#: scale with the features, the *ratio* between them stays roughly constant far outside the
#: training range -- so an absurd feature value yields a confident maximum-size position
#: with no other outward sign. Financial features are fat-tailed, so 5 sigma is a flag
#: rather than a hard bound; calibrate it to the feature set.
DEFAULT_EXTRAPOLATION_Z_LIMIT = 5.0

#: Feature standard deviation below which a column is treated as constant.
_CONSTANT_FEATURE_TOLERANCE = 1e-12


@dataclass
class QuantilePrediction:
    """
    One uncertainty-aware signal.

    ``uncertainty_width`` is the measured band ``q_upper - q_lower``, never floored.
    ``uncertainty_floor_binding`` reports separately whether it collapsed to or below
    ``min_uncertainty_width``, in which case ``confidence_scaled_size`` is 0.0.

    ``status_message`` carries only the single most severe condition, in the order
    degenerate band > extrapolating > band straddles zero > sized. The boolean flags are
    always all populated, so branch on those rather than on the string when more than one
    condition matters.
    """
    tau_lower: float
    tau_central: float
    tau_upper: float
    q_lower: float
    q_central: float
    q_upper: float
    uncertainty_width: float
    confidence_ratio: float
    signal_direction: float
    confidence_scaled_size: float
    uncertainty_floor_binding: bool
    interval_straddles_zero: bool
    is_extrapolating: bool
    max_feature_zscore: float
    crossing_repaired: bool
    observations_trained: int
    status_message: str


@dataclass
class QuantileCalibration:
    """Coverage and pinball score for one quantile level over an evaluation sample."""
    tau: float
    nominal_coverage: float
    empirical_coverage: float
    coverage_error: float
    mean_pinball_loss: float
    observations: int


def _require_finite(value: float, name: str) -> float:
    """
    Rejects NaN/inf at the boundary.

    A NaN target is more dangerous here than it first looks: the quantile subgradient
    branches on ``y - y_hat >= 0``, and every comparison against NaN is False, so a NaN
    silently takes the "observation below the prediction" branch and drags every quantile
    downward. The model then emits a well-formed, confidently signed signal built
    entirely from corrupt data. It must fail loudly instead.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return numeric


def _require_unit_interval(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if not 0.0 < numeric < 1.0:
        raise ValueError(f"{name} must lie strictly in (0, 1), got {numeric}.")
    return numeric


def _validate_quantiles(quantiles: Sequence[float]) -> Tuple[float, float, float]:
    """
    Requires exactly three strictly increasing levels in (0, 1).

    Three is structural, not arbitrary: the sizer needs a lower tail, a direction, and an
    upper tail. Strict ordering is enforced at construction so that a crossing detected
    later can only ever be a fitting artefact, never a configuration error.
    """
    levels = tuple(_require_unit_interval(q, "quantile") for q in quantiles)
    if len(levels) != 3:
        raise ValueError(
            f"Exactly three quantiles (lower, central, upper) are required, got {len(levels)}."
        )
    if not levels[0] < levels[1] < levels[2]:
        raise ValueError(f"Quantiles must be strictly increasing, got {levels}.")
    return levels  # type: ignore[return-value]


def empirical_quantile(values: Sequence[float], tau: float) -> float:
    """
    Sample tau-quantile by linear interpolation between order statistics.

    This is the R type-7 / NumPy default ``linear`` convention: with n sorted values the
    quantile sits at position ``(n - 1) * tau``. Used to warm-start the intercepts at the
    marginal quantiles of the training targets.
    """
    _require_unit_interval(tau, "tau")
    if not values:
        raise ValueError("Cannot take a quantile of an empty sample.")
    ordered = sorted(_require_finite(v, "value") for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * tau
    lower_index = math.floor(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (ordered[upper_index] - ordered[lower_index])


def pinball_loss(y_true: float, y_pred: float, tau: float) -> float:
    """
    Pinball (check) loss for a single observation.

        L_tau(y, y_hat) = tau * (y - y_hat)       if y >= y_hat
                        = (1 - tau) * (y_hat - y) if y <  y_hat

    Always non-negative, and zero only at ``y_hat == y``. At tau = 0.5 it equals *half*
    the absolute error, so a mean pinball loss at tau = 0.5 is MAE / 2 -- do not compare
    it against an MAE figure without that factor.
    """
    _require_unit_interval(tau, "tau")
    error = _require_finite(y_true, "y_true") - _require_finite(y_pred, "y_pred")
    return tau * error if error >= 0.0 else (tau - 1.0) * error


def mean_pinball_loss(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    tau: float,
) -> float:
    """
    Mean pinball loss over a sample. Lower is better. It is the quantity a correctly
    fitted tau-quantile model minimises, and the standard score for comparing two
    candidate quantile models on identical data.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true has {len(y_true)} observations but y_pred has {len(y_pred)}."
        )
    if not y_true:
        raise ValueError("Cannot score an empty sample.")
    return sum(pinball_loss(t, p, tau) for t, p in zip(y_true, y_pred)) / len(y_true)


class QuantileRegressionSignalModel:
    """
    Linear conditional-quantile model fitted by Pinball-loss SGD, producing an
    uncertainty-scaled position multiplier.

    One coefficient vector and intercept is fitted per quantile level, independently.
    Independent fits can cross in finite samples; ``predict`` repairs crossings by
    monotone rearrangement and reports whether it had to.

    An intercept is always fitted and is not optional. Without one, all three quantile
    lines are forced through the origin, so the model cannot represent a location-shift
    family ``y = f(x) + e`` at all and the band width it reports is an artefact of that
    constraint rather than a measurement of dispersion.
    """

    def __init__(
        self,
        num_features: int,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        quantiles: Sequence[float] = DEFAULT_QUANTILES,
        decay_power: float = DEFAULT_DECAY_POWER,
        averaging_tail: float = DEFAULT_AVERAGING_TAIL,
        min_uncertainty_width: float = DEFAULT_MIN_UNCERTAINTY_WIDTH,
        extrapolation_z_limit: float = DEFAULT_EXTRAPOLATION_Z_LIMIT,
    ):
        """
        Args:
            num_features: Length of every feature vector. 0 gives an intercept-only model
                estimating the *unconditional* quantiles of the target -- a legitimate
                baseline to beat before trusting any conditional band.
            learning_rate: Initial step size as a multiple of the training targets'
                interquartile range (unit-free).
            quantiles: Exactly three strictly increasing levels in (0, 1).
            decay_power: Robbins-Monro exponent. (0.5, 1.0] converges; 0.0 disables decay
                and does not converge, and is offered only for comparison.
            averaging_tail: Fraction of final iterates in the Polyak-Ruppert average,
                in [0, 1). 0.0 deploys the noisier last iterate.
            min_uncertainty_width: Narrowest band accepted as a measurement, in the
                target's units. At or below it the sizer returns 0.0.
            extrapolation_z_limit: Standardised-feature magnitude beyond which a
                prediction is flagged as extrapolating. Flagged, not blocked -- whether an
                out-of-domain forecast may be traded is a risk-policy decision.
        """
        if num_features < 0:
            raise ValueError(f"num_features must be non-negative, got {num_features}.")
        if not learning_rate > 0.0:
            raise ValueError(f"learning_rate must be positive, got {learning_rate}.")
        if not 0.0 <= decay_power <= 1.0:
            raise ValueError(f"decay_power must lie in [0, 1], got {decay_power}.")
        if not 0.0 <= averaging_tail < 1.0:
            raise ValueError(f"averaging_tail must lie in [0, 1), got {averaging_tail}.")
        if not min_uncertainty_width > 0.0:
            raise ValueError(
                f"min_uncertainty_width must be positive, got {min_uncertainty_width}."
            )
        if not extrapolation_z_limit > 0.0:
            raise ValueError(
                f"extrapolation_z_limit must be positive, got {extrapolation_z_limit}."
            )
        if decay_power == 0.0:
            logger.warning(
                "decay_power=0 uses a constant step size, which violates the "
                "Robbins-Monro conditions: coefficients will oscillate indefinitely and "
                "the estimated band width will retain an O(learning_rate) error."
            )

        self.num_features = num_features
        self.eta = learning_rate
        self.quantiles = _validate_quantiles(quantiles)
        self.decay_power = decay_power
        self.averaging_tail = averaging_tail
        self.min_uncertainty_width = min_uncertainty_width
        self.extrapolation_z_limit = extrapolation_z_limit

        # Coefficients act on *standardised* features; the scaler below is populated by
        # fit() from training-fold statistics only.
        self.weights: Dict[float, List[float]] = {
            q: [0.0] * num_features for q in self.quantiles
        }
        self.intercepts: Dict[float, float] = {q: 0.0 for q in self.quantiles}
        self.observations_trained = 0
        self._feature_means: List[float] = [0.0] * num_features
        self._feature_scales: List[float] = [1.0] * num_features
        self._target_scale = 1.0
        self._is_fitted = False

    @property
    def is_fitted(self) -> bool:
        """True once ``fit`` has established a feature scaler and warm-started intercepts."""
        return self._is_fitted

    @staticmethod
    def pinball_loss_gradient(y_true: float, y_pred: float, tau: float) -> float:
        """
        Subgradient of the pinball loss with respect to the prediction:

            dL/dy_hat = -tau      if y >= y_hat
                      = (1 - tau) if y <  y_hat

        The loss is non-differentiable at ``y == y_hat``; -tau is taken there, a valid
        element of the subdifferential [-tau, 1 - tau].
        """
        return -tau if (y_true - y_pred) >= 0.0 else (1.0 - tau)

    def _validate_features(self, features: Sequence[float]) -> List[float]:
        """
        Length and finiteness check.

        Without the length check a short vector raises IndexError partway through the
        coefficient update, leaving the model half-modified, and a long vector is
        silently truncated by ``zip`` -- the surplus features are dropped with no error
        and the caller never learns the prediction ignored them.
        """
        if len(features) != self.num_features:
            raise ValueError(
                f"Expected {self.num_features} features, got {len(features)}."
            )
        return [_require_finite(x, f"features[{i}]") for i, x in enumerate(features)]

    def _standardize(self, features: Sequence[float]) -> List[float]:
        return [
            (x - mean) / scale
            for x, mean, scale in zip(features, self._feature_means, self._feature_scales)
        ]

    def _current_step_size(self) -> float:
        decay = (1.0 + self.observations_trained) ** self.decay_power
        return self.eta * self._target_scale / decay

    def _apply_update(self, standardized: Sequence[float], target: float) -> None:
        """One subgradient step for every quantile, on already-standardised features."""
        step = self._current_step_size()
        for q in self.quantiles:
            prediction = self.intercepts[q] + sum(
                w * x for w, x in zip(self.weights[q], standardized)
            )
            grad = self.pinball_loss_gradient(target, prediction, q)
            self.intercepts[q] -= step * grad
            for i in range(self.num_features):
                self.weights[q][i] -= step * grad * standardized[i]
        self.observations_trained += 1

    def _fit_scaler(
        self,
        rows: Sequence[Sequence[float]],
        targets: Sequence[float],
    ) -> None:
        """
        Derive feature means/scales and the target scale from the training sample only.

        Standardisation is internal because a single global step size cannot serve
        features of different magnitudes: one column two orders larger than the rest
        dominates every update. It is fitted here, inside ``fit``, so it can only ever see
        training-fold data -- deriving it from the full sample would leak the evaluation
        distribution into the model.
        """
        n = len(rows)
        self._feature_means = [sum(row[j] for row in rows) / n for j in range(self.num_features)]
        scales: List[float] = []
        for j in range(self.num_features):
            mean = self._feature_means[j]
            variance = sum((row[j] - mean) ** 2 for row in rows) / n
            deviation = math.sqrt(variance)
            if deviation <= _CONSTANT_FEATURE_TOLERANCE:
                logger.warning(
                    "Feature %d is constant across the training sample; it carries no "
                    "conditional information and is collinear with the intercept. It is "
                    "centred to zero and will not affect predictions.",
                    j,
                )
                scales.append(1.0)
            else:
                scales.append(deviation)
        self._feature_scales = scales

        self._target_scale = self._target_dispersion(targets)

    def _target_dispersion(self, targets: Sequence[float]) -> float:
        """
        Robust dispersion of the training targets, used to put the step size on the
        target's own scale so that ``learning_rate`` means the same thing whether targets
        are returns (~1e-2) or index points (~1e3).

        The interquartile range is the primary measure. It can legitimately be zero when
        the middle half of the sample is a point mass, so it falls back to the spread of
        the configured outer quantiles and then to the full range. A target with *no*
        dispersion at all is fatal, not a fallback case: every quantile of a constant is
        that constant, so there is no band to estimate. Scaling the step by an arbitrary
        constant instead would let SGD push the three warm-started intercepts apart into
        a band that is purely an artefact of the step size -- and a spurious band is
        exactly what the sizer converts into a large position.
        """
        interquartile = empirical_quantile(targets, 0.75) - empirical_quantile(targets, 0.25)
        if interquartile > 0.0:
            return interquartile

        tau_lower, _, tau_upper = self.quantiles
        outer = empirical_quantile(targets, tau_upper) - empirical_quantile(targets, tau_lower)
        if outer > 0.0:
            logger.warning(
                "Training targets have a zero interquartile range; falling back to the "
                "tau=%.2f-%.2f spread (%.6g) as the step scale.",
                tau_lower,
                tau_upper,
                outer,
            )
            return outer

        full_range = max(targets) - min(targets)
        if full_range > 0.0:
            logger.warning(
                "Training targets have a zero tau=%.2f-%.2f spread, so the *marginal* "
                "band is degenerate; falling back to the full range (%.6g) as the step "
                "scale. Any band fitted here comes entirely from the features, and part "
                "of its width may be a step-size artefact. Validate it against held-out "
                "coverage via calibration_report() before sizing on it.",
                tau_lower,
                tau_upper,
                full_range,
            )
            return full_range

        raise ValueError(
            "Training targets are constant, so every conditional quantile is that same "
            "constant and there is no uncertainty band to estimate. Supply targets with "
            "dispersion rather than fitting a band that would be an artefact of the "
            "step size."
        )

    def fit(
        self,
        feature_rows: Sequence[Sequence[float]],
        targets: Sequence[float],
        epochs: int = 1,
        shuffle: bool = True,
        seed: Optional[int] = None,
    ) -> "QuantileRegressionSignalModel":
        """
        Fit the three quantile models over a dataset.

        This is a **complete refit**, not a continuation: coefficients, the scaler, and
        the step-size clock are all reset first, so the same data and seed always produce
        the same model. That is the semantic walk-forward trading wants -- call ``fit``
        once per window rather than accumulating one ever-growing model across regimes.

        The whole dataset is validated up front, so a malformed row at index 500 cannot
        leave a partially trained model behind. Shuffling uses a private
        ``random.Random(seed)``, so a seeded fit is exactly reproducible and global RNG
        state is never touched.

        Rows must already be lag-correct: each target realised strictly after all of its
        own features were observable. Shuffling a lag-correct dataset is safe; it cannot
        repair a leaky one.

        Args:
            epochs: Passes over the data. One pass suffices for large samples; small
                samples need several. Confirm the choice with ``calibration_report`` on
                held-out data rather than assuming.

        Returns:
            self, to allow chaining.
        """
        if len(feature_rows) != len(targets):
            raise ValueError(
                f"{len(feature_rows)} feature rows but {len(targets)} targets."
            )
        if not feature_rows:
            raise ValueError("Cannot fit on an empty dataset.")
        if epochs < 1:
            raise ValueError(f"epochs must be at least 1, got {epochs}.")

        clean_rows = [self._validate_features(row) for row in feature_rows]
        clean_targets = [
            _require_finite(t, f"targets[{i}]") for i, t in enumerate(targets)
        ]

        self.weights = {q: [0.0] * self.num_features for q in self.quantiles}
        self.observations_trained = 0
        self._fit_scaler(clean_rows, clean_targets)

        # Warm start each intercept at the marginal quantile of the target. With a
        # decaying step the total distance the iterates can travel is finite, so starting
        # from zero can leave a distant optimum permanently out of reach; starting from
        # the unconditional answer leaves only the conditional structure to learn.
        self.intercepts = {
            q: empirical_quantile(clean_targets, q) for q in self.quantiles
        }

        standardized_rows = [self._standardize(row) for row in clean_rows]
        total_steps = len(clean_rows) * epochs
        averaging_start = int(total_steps * (1.0 - self.averaging_tail))

        summed_weights = {q: [0.0] * self.num_features for q in self.quantiles}
        summed_intercepts = {q: 0.0 for q in self.quantiles}
        averaged_steps = 0

        rng = random.Random(seed)
        order = list(range(len(standardized_rows)))
        for _ in range(epochs):
            if shuffle:
                rng.shuffle(order)
            for i in order:
                self._apply_update(standardized_rows[i], clean_targets[i])
                if self.averaging_tail > 0.0 and self.observations_trained > averaging_start:
                    for q in self.quantiles:
                        summed_intercepts[q] += self.intercepts[q]
                        for j in range(self.num_features):
                            summed_weights[q][j] += self.weights[q][j]
                    averaged_steps += 1

        # Polyak-Ruppert: deploy the averaged tail rather than the final iterate. The
        # pinball loss is non-smooth, so the last iterate still carries a full step of
        # noise even after the schedule has converged.
        if averaged_steps > 0:
            for q in self.quantiles:
                self.intercepts[q] = summed_intercepts[q] / averaged_steps
                self.weights[q] = [
                    total / averaged_steps for total in summed_weights[q]
                ]

        # Coefficients are only reachable from finite inputs and finite steps, but an
        # extreme feature scale can still overflow the dot product during training. Fail
        # here rather than letting a non-finite coefficient reach predict(), where it
        # would silently disable every ordering and threshold comparison downstream.
        for q in self.quantiles:
            if not math.isfinite(self.intercepts[q]) or not all(
                math.isfinite(w) for w in self.weights[q]
            ):
                raise ValueError(
                    f"Fit diverged: non-finite coefficients at tau={q}. Feature or target "
                    "magnitudes are extreme enough to overflow the update; rescale the "
                    "inputs before fitting."
                )

        self._is_fitted = True
        logger.info(
            "Fitted quantile model: taus=%s rows=%d epochs=%d averaged_iterates=%d",
            self.quantiles,
            len(clean_rows),
            epochs,
            averaged_steps,
        )
        return self

    def train_sample(self, features: Sequence[float], target: float) -> None:
        """
        One online subgradient step on a single observation, for all three quantiles.

        Requires a prior ``fit``: the update needs the feature scaler and the target
        scale, and neither can be inferred from one observation. Inputs are fully
        validated before any coefficient is touched, so a rejected observation leaves the
        model exactly as it was.

        Note the step-size schedule keeps decaying from wherever ``fit`` left it, so
        online steps are *refinements*, not regime adaptation. To follow a regime change,
        refit on a fresh window.
        """
        if not self._is_fitted:
            raise RuntimeError(
                "train_sample requires a prior fit(): the feature scaler and target "
                "scale cannot be derived from a single observation."
            )
        clean_features = self._validate_features(features)
        clean_target = _require_finite(target, "target")
        self._apply_update(self._standardize(clean_features), clean_target)

    def _rearranged_quantiles(
        self,
        standardized: Sequence[float],
    ) -> Tuple[List[float], bool]:
        """
        Per-quantile predictions with crossings repaired by sorting.

        Monotone rearrangement (Chernozhukov, Fernandez-Val & Galichon 2010): sorting a
        crossing estimate is guaranteed to be weakly closer to the true quantile curve
        than leaving it crossed, so the repair never costs accuracy.
        """
        raw = [
            self.intercepts[q] + sum(w * x for w, x in zip(self.weights[q], standardized))
            for q in self.quantiles
        ]
        ordered = sorted(raw)
        return ordered, raw != ordered

    def predict(
        self,
        features: Sequence[float],
        max_position_size: float = 1.0,
    ) -> QuantilePrediction:
        """
        Produce the three conditional quantiles and the uncertainty-scaled size.

            width = q_upper - q_lower                       (after crossing repair)
            size  = sign(q_central) * min(max_position_size, |q_central| / width)
            size  = 0.0                        if width <= min_uncertainty_width

        A band that has collapsed to or below the floor is treated as *no measurement*,
        never as maximum confidence. Dividing by a floored width instead would turn a
        degenerate model -- whose three quantiles are identical -- into the largest
        position the caller permits, the worst possible response to the worst possible
        input.

        Two conditions are surfaced rather than acted on, because whether either
        disqualifies a trade is a risk-policy decision belonging to the caller:

        - ``interval_straddles_zero``: ``q_lower < 0 < q_upper``, so the band does not
          support the sign of the trade at that confidence level.
        - ``is_extrapolating``: some feature is more than ``extrapolation_z_limit`` sigma
          from the fitting sample. This one is easy to miss and matters: because a linear
          model's median forecast and its band both scale with the features, their *ratio*
          stays roughly constant however far out of domain the input goes, so an absurd
          feature value produces a confident, capped-out position rather than an obviously
          broken number. ``max_feature_zscore`` carries the magnitude.

        Raises:
            RuntimeError: if the model has not been fitted. An unfitted model's
                coefficients are all zero, which yields a plausible-looking prediction of
                exactly zero with a zero-width band; that must never reach a sizer.
        """
        if not self._is_fitted:
            raise RuntimeError("Model is not fitted; call fit() before predict().")
        if not math.isfinite(max_position_size) or max_position_size <= 0.0:
            raise ValueError(
                f"max_position_size must be positive and finite, got {max_position_size}."
            )
        clean_features = self._validate_features(features)
        tau_lower, tau_central, tau_upper = self.quantiles

        standardized = self._standardize(clean_features)
        max_feature_zscore = max((abs(z) for z in standardized), default=0.0)
        is_extrapolating = max_feature_zscore > self.extrapolation_z_limit
        if is_extrapolating:
            logger.warning(
                "Feature vector is %.2f sigma from the fitting sample (limit %.2f): this "
                "prediction extrapolates outside the range the quantiles were fitted on. "
                "Both the median forecast and the band scale with the features, so the "
                "confidence ratio -- and therefore the position size -- can stay at its "
                "cap however far out of domain the input is.",
                max_feature_zscore,
                self.extrapolation_z_limit,
            )

        (q_lower, q_central, q_upper), crossing_repaired = self._rearranged_quantiles(
            standardized
        )
        if crossing_repaired:
            logger.debug(
                "Quantile crossing repaired by monotone rearrangement at features=%s.",
                clean_features,
            )

        uncertainty_width = q_upper - q_lower
        floor_binding = uncertainty_width <= self.min_uncertainty_width
        interval_straddles_zero = q_lower < 0.0 < q_upper

        if floor_binding:
            logger.warning(
                "Uncertainty band %.6g is at or below min_uncertainty_width %.6g; "
                "refusing to size. This is a degenerate band, not a confident forecast.",
                uncertainty_width,
                self.min_uncertainty_width,
            )
            confidence_ratio = 0.0
            signal_direction = 0.0
            scaled_size = 0.0
            status = "degenerate_band_not_sized"
        else:
            confidence_ratio = abs(q_central) / uncertainty_width
            signal_direction = math.copysign(1.0, q_central) if q_central != 0.0 else 0.0
            scaled_size = signal_direction * min(max_position_size, confidence_ratio)
            if is_extrapolating:
                status = "sized_outside_training_feature_range"
            elif interval_straddles_zero:
                status = "sized_direction_unsupported_by_band"
            else:
                status = "sized"

        return QuantilePrediction(
            tau_lower=tau_lower,
            tau_central=tau_central,
            tau_upper=tau_upper,
            q_lower=q_lower,
            q_central=q_central,
            q_upper=q_upper,
            uncertainty_width=uncertainty_width,
            confidence_ratio=confidence_ratio,
            signal_direction=signal_direction,
            confidence_scaled_size=scaled_size,
            uncertainty_floor_binding=floor_binding,
            interval_straddles_zero=interval_straddles_zero,
            is_extrapolating=is_extrapolating,
            max_feature_zscore=max_feature_zscore,
            crossing_repaired=crossing_repaired,
            observations_trained=self.observations_trained,
            status_message=status,
        )

    def calibration_report(
        self,
        feature_rows: Sequence[Sequence[float]],
        targets: Sequence[float],
    ) -> List[QuantileCalibration]:
        """
        Empirical coverage and pinball score per quantile.

        A correctly fitted tau-quantile model places a fraction tau of realised targets
        at or below its prediction. This is the calibration half of the "maximise
        sharpness subject to calibration" paradigm (Gneiting, Balabdaoui & Raftery 2007,
        JRSS-B 69(2), 243-268): a narrow band is only an asset once coverage is right, and
        a narrow band with 60% coverage at tau = 0.90 is an over-confident sizer, not a
        precise one -- it will scale positions up on exactly the forecasts it understands
        least.

        Quantiles are scored *after* crossing repair, because the rearranged values are
        what the sizer consumes.

        Evaluate on data the model was not fitted on. In-sample coverage is close to
        tautological for a model trained to minimise this very loss, and will overstate
        calibration.
        """
        if len(feature_rows) != len(targets):
            raise ValueError(
                f"{len(feature_rows)} feature rows but {len(targets)} targets."
            )
        if not feature_rows:
            raise ValueError("Cannot compute calibration on an empty sample.")
        if not self._is_fitted:
            raise RuntimeError("Model is not fitted; nothing to calibrate.")

        clean_rows = [self._validate_features(row) for row in feature_rows]
        clean_targets = [
            _require_finite(t, f"targets[{i}]") for i, t in enumerate(targets)
        ]

        per_tau: Dict[float, List[float]] = {q: [] for q in self.quantiles}
        for row in clean_rows:
            rearranged, _ = self._rearranged_quantiles(self._standardize(row))
            for tau, value in zip(self.quantiles, rearranged):
                per_tau[tau].append(value)

        observations = len(clean_targets)
        reports: List[QuantileCalibration] = []
        for tau in self.quantiles:
            predictions = per_tau[tau]
            covered = sum(1 for y, q in zip(clean_targets, predictions) if y <= q)
            empirical = covered / observations
            reports.append(
                QuantileCalibration(
                    tau=tau,
                    nominal_coverage=tau,
                    empirical_coverage=empirical,
                    coverage_error=empirical - tau,
                    mean_pinball_loss=mean_pinball_loss(clean_targets, predictions, tau),
                    observations=observations,
                )
            )
        return reports
