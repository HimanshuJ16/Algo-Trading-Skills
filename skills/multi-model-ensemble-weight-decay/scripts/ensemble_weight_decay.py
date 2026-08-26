"""
multi-model-ensemble-weight-decay: exponentially discounted performance memory,
numerically stable softmax reweighting, and demotion circuit breakers for a
live multi-model trading ensemble.

Design notes
------------
* **The output is a capital-allocation vector.** ``final_normalized_weight`` is
  consumed downstream to size live positions. Every guard in this module exists
  because a malformed weight vector -- NaN, sign-inverted, or silently assigned
  to an anti-predictive model -- misallocates capital without raising anything.

* **Exponential recency decay.** ``L_bar_t = lam * L_bar_{t-1} + (1 - lam) * L_t``
  is the standard EWMA recursion. Its memory is characterised exactly by the
  half-life ``ln(0.5) / ln(lam)``: lam=0.95 forgets half its weight in 13.51
  periods, lam=0.94 in 11.20, lam=0.99 in 68.97. J.P. Morgan/Reuters RiskMetrics
  (Technical Document, 4th ed., 1996, Appendix C) fits lam=0.94 for daily and
  lam=0.97 for monthly financial series; those are the anchors for the default
  here, not a regulatory mandate.

  On the first observation of a model ``previous_decayed_*`` is None and the
  recursion is seeded with the current reading, so ``L_bar = lam*L + (1-lam)*L
  = L`` exactly. lam has no observable effect until the second call. This is
  correct seeding, not a broken decay factor.

* **Softmax reweighting is the Hedge / exponentially weighted average
  forecaster** (Freund & Schapire, 1997; Cesa-Bianchi & Lugosi, *Prediction,
  Learning, and Games*, 2006, Ch. 2), whose weights are proportional to
  ``exp(-eta * L_i)``. ``temperature_beta`` is that learning rate eta. Published
  Hedge exponentiates a *cumulative* loss; this module exponentiates the
  *discounted* loss instead, which is the appropriate variant for a
  non-stationary regime where old losses must be forgotten. The regret bounds
  quoted for textbook Hedge do not transfer unchanged to this variant.

* **Numerically stable softmax.** Scores are shifted by their maximum before
  exponentiation. ``softmax(s - max(s)) == softmax(s)`` is an exact algebraic
  identity (the shift factors out of numerator and denominator), and it
  guarantees the largest term is ``exp(0) = 1``, so the denominator is always
  >= 1. Without it, ``exp()`` raises OverflowError on large positive scores and
  underflows every term to 0.0 on large negative ones, producing a
  ZeroDivisionError on the normalisation. See Goodfellow, Bengio & Courville,
  *Deep Learning* (2016), Ch. 4 "Numerical Computation".

* **Demotion is a circuit breaker, not a weighting term.** A model whose
  discounted IC is <= 0 is anti-predictive on the evidence available and is
  removed from the book entirely, under *both* weighting methods. Loss-based
  weighting alone cannot express this: a model can have a low MSE and a
  negative IC at the same time (it predicts small, wrongly signed moves).

* **The breaker reads the discounted IC, never the single-period IC.** For a
  cross-section of N names the sample correlation has standard error
  approximately 1/sqrt(N - 1) under the null of zero true IC. At N=100 that is
  ~0.10, so the *sign* of one period's IC is close to a coin flip for any model
  whose true IC is a realistic fraction of that. Triggering a capital
  reallocation on it would discard skilled models at roughly the rate it
  discards broken ones, and would defeat the purpose of the decay memory.
  Information coefficient in the Grinold & Kahn sense (IR = IC * sqrt(breadth);
  Grinold, 1989; Grinold & Kahn, *Active Portfolio Management*, 2nd ed., 1999).

* **There is no equal-weight fallback.** If every model is demoted, that is the
  ensemble telling the caller it has no usable model, and the correct response
  is to stop allocating -- not to spread capital evenly across the models the
  circuit breakers just rejected. The report returns
  ``ENSEMBLE_HALTED_ALL_DEMOTED`` with every weight at 0.0.
"""
from dataclasses import dataclass
import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Weight models by exponentially discounted forecast loss (lower is better).
EXPONENTIAL_LOSS = "EXPONENTIAL_LOSS"

#: Weight models by exponentially discounted information coefficient (higher is better).
IC_SOFTMAX = "IC_SOFTMAX"

VALID_WEIGHTING_METHODS = frozenset({EXPONENTIAL_LOSS, IC_SOFTMAX})

#: Decimal places for reported weights. Reported active weights are residual-
#: corrected to sum to exactly 1.0 at this precision.
WEIGHT_PRECISION = 6

STATUS_ACTIVE = "ACTIVE"
STATUS_DEMOTED_BELOW_FLOOR = "DEMOTED_BELOW_FLOOR"
STATUS_DEMOTED_NEGATIVE_IC = "DEMOTED_NEGATIVE_IC"
STATUS_PENDING_WARMUP = "PENDING_WARMUP"

ENSEMBLE_REWEIGHTED_SUCCESS = "ENSEMBLE_REWEIGHTED_SUCCESS"
ENSEMBLE_HALTED_ALL_DEMOTED = "ENSEMBLE_HALTED_ALL_DEMOTED"


class EnsembleWeightError(ValueError):
    """Raised on malformed telemetry or an infeasible ensemble configuration.

    Subclasses ``ValueError`` so callers written against the previous
    ``raise ValueError(...)`` contract keep working.
    """


def half_life_periods(decay_factor_lambda: float) -> float:
    """Periods over which an EWMA with this lambda forgets half its weight.

    ``ln(0.5) / ln(lambda)``. Undefined at lambda in {0.0, 1.0}: lambda=0 has no
    memory at all and lambda=1 never forgets.
    """
    if not 0.0 < decay_factor_lambda < 1.0:
        raise EnsembleWeightError(
            f"half_life_periods requires 0 < lambda < 1, got {decay_factor_lambda!r}."
        )
    return math.log(0.5) / math.log(decay_factor_lambda)


@dataclass
class ModelTelemetry:
    """One model's performance reading for the current reweighting period."""

    model_id: str
    recent_loss: float                          # Current-period MSE / LogLoss (lower is better)
    recent_ic: float                            # Current-period Information Coefficient (higher is better)
    days_active: int = 1                        # Periods of live history behind these readings
    previous_decayed_loss: Optional[float] = None
    previous_decayed_ic: Optional[float] = None


@dataclass
class EnsembleConfig:
    """Reweighting policy. Bounds are checked on construction."""

    decay_factor_lambda: float = 0.95           # EWMA recency memory, [0, 1]
    temperature_beta: float = 2.0               # Softmax learning rate eta, must be > 0
    min_weight_floor: float = 0.05              # Demote below this raw weight; must be < 1/M
    weighting_method: str = EXPONENTIAL_LOSS
    demote_on_negative_ic: bool = True          # Circuit breaker on discounted IC <= 0
    min_days_active: int = 1                    # Withhold weight until a model has this much history

    def __post_init__(self) -> None:
        if not 0.0 <= self.decay_factor_lambda <= 1.0:
            raise EnsembleWeightError(
                f"decay_factor_lambda must be in [0.0, 1.0], got "
                f"{self.decay_factor_lambda!r}. Values outside this range are not a "
                f"convex combination and make the decayed metric diverge."
            )
        if not math.isfinite(self.temperature_beta) or self.temperature_beta <= 0.0:
            raise EnsembleWeightError(
                f"temperature_beta must be finite and > 0, got "
                f"{self.temperature_beta!r}. A non-positive beta inverts the softmax "
                f"and assigns the largest weight to the worst-performing model."
            )
        if not 0.0 <= self.min_weight_floor < 1.0:
            raise EnsembleWeightError(
                f"min_weight_floor must be in [0.0, 1.0), got {self.min_weight_floor!r}."
            )
        if self.min_days_active < 1:
            raise EnsembleWeightError(
                f"min_days_active must be >= 1, got {self.min_days_active!r}."
            )
        if self.weighting_method not in VALID_WEIGHTING_METHODS:
            raise EnsembleWeightError(
                f"weighting_method must be one of {sorted(VALID_WEIGHTING_METHODS)}, "
                f"got {self.weighting_method!r}."
            )


@dataclass
class ModelWeightStatus:
    """Per-model outcome of one reweighting pass."""

    model_id: str
    raw_weight: float                           # Softmax weight before demotion, over all models
    final_normalized_weight: float              # Post-demotion weight; 0.0 if not active
    decayed_metric: float                       # Discounted value of the weighting metric
    is_active: bool
    status: str
    decayed_ic: float = 0.0                     # Discounted IC, always maintained


@dataclass
class EnsembleWeightReport:
    ensemble_id: str
    active_model_count: int
    demoted_model_count: int
    model_statuses: List[ModelWeightStatus]
    status: str
    audit_notes: str
    weighting_method: str = EXPONENTIAL_LOSS
    decay_half_life_periods: Optional[float] = None
    pending_warmup_model_count: int = 0          # Withheld for want of history, not demoted


class EnsembleWeightDecayEngine:
    """Reweights a multi-model ensemble from discounted performance telemetry.

    Stateless: the caller owns the decayed metrics between periods and feeds
    them back via ``previous_decayed_loss`` / ``previous_decayed_ic``. Two calls
    with identical inputs return identical reports.
    """

    def reweight_ensemble(
        self, ensemble_id: str, cfg: EnsembleConfig, models: List[ModelTelemetry]
    ) -> EnsembleWeightReport:
        """Update decayed metrics, compute softmax weights, apply the demotion
        circuit breakers, and renormalise the survivors to sum to 1.0.

        Raises:
            EnsembleWeightError: empty or duplicated telemetry, non-finite or
                negative readings, or a ``min_weight_floor`` at or above
                ``1 / len(models)`` (which would demote every model even when
                all perform identically).
        """
        self._validate_inputs(ensemble_id, cfg, models)

        decayed_losses, decayed_ics = self._update_decayed_metrics(cfg, models)
        metrics = decayed_losses if cfg.weighting_method == EXPONENTIAL_LOSS else decayed_ics
        raw_normalized = self._stable_softmax(cfg, metrics)

        statuses, active_weights = self._apply_circuit_breakers(
            cfg, models, metrics, decayed_ics, raw_normalized
        )
        # Insufficient history is not evidence of failure: PENDING_WARMUP is
        # counted apart from the two DEMOTED_* reasons so the audit trail does
        # not read a new model as a broken one.
        pending_count = sum(1 for s in statuses if s.status == STATUS_PENDING_WARMUP)
        demoted_count = sum(1 for s in statuses if not s.is_active) - pending_count

        if not active_weights:
            return self._halted_report(ensemble_id, cfg, statuses, demoted_count, pending_count)

        self._normalize_active(statuses, active_weights)
        return self._success_report(
            ensemble_id, cfg, statuses, len(active_weights), demoted_count, pending_count
        )

    # ---------------------------------------------------------------- validation

    @staticmethod
    def _validate_inputs(
        ensemble_id: str, cfg: EnsembleConfig, models: List[ModelTelemetry]
    ) -> None:
        if not ensemble_id or not ensemble_id.strip():
            raise EnsembleWeightError("ensemble_id must be a non-empty string.")
        if not models:
            raise EnsembleWeightError("Model telemetry list cannot be empty.")

        seen: Dict[str, int] = {}
        for idx, m in enumerate(models):
            if not m.model_id or not m.model_id.strip():
                raise EnsembleWeightError(f"models[{idx}] has an empty model_id.")
            if m.model_id in seen:
                raise EnsembleWeightError(
                    f"Duplicate model_id {m.model_id!r} at positions "
                    f"{seen[m.model_id]} and {idx}. Weights are keyed by model_id; "
                    f"duplicates silently overwrite one another's metrics."
                )
            seen[m.model_id] = idx

            for label, value in (
                ("recent_loss", m.recent_loss),
                ("recent_ic", m.recent_ic),
                ("previous_decayed_loss", m.previous_decayed_loss),
                ("previous_decayed_ic", m.previous_decayed_ic),
            ):
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EnsembleWeightError(
                        f"Model {m.model_id!r}: {label} must be numeric, got {value!r}."
                    )
                if not math.isfinite(float(value)):
                    raise EnsembleWeightError(
                        f"Model {m.model_id!r}: {label} is {value!r}. Non-finite "
                        f"telemetry propagates silently into the weight vector; "
                        f"repair or withhold the model instead."
                    )
            if m.recent_loss < 0.0:
                raise EnsembleWeightError(
                    f"Model {m.model_id!r}: recent_loss is {m.recent_loss!r}. Forecast "
                    f"losses (MSE, LogLoss) are non-negative by construction; a "
                    f"negative value indicates a sign error upstream."
                )
            if m.days_active < 0:
                raise EnsembleWeightError(
                    f"Model {m.model_id!r}: days_active must be >= 0, got {m.days_active!r}."
                )

        equal_weight = 1.0 / len(models)
        if cfg.min_weight_floor >= equal_weight:
            raise EnsembleWeightError(
                f"min_weight_floor ({cfg.min_weight_floor}) is at or above the "
                f"equal-weight share 1/M ({equal_weight:.6f}) for {len(models)} models. "
                f"Every model would be demoted even when all perform identically."
            )

    # ------------------------------------------------------------------- decay

    @staticmethod
    def _update_decayed_metrics(
        cfg: EnsembleConfig, models: List[ModelTelemetry]
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Advance the EWMA for loss and IC. Both are always maintained: the IC
        circuit breaker needs the discounted IC even under EXPONENTIAL_LOSS."""
        lam = cfg.decay_factor_lambda
        losses: Dict[str, float] = {}
        ics: Dict[str, float] = {}
        for m in models:
            prev_loss = m.previous_decayed_loss if m.previous_decayed_loss is not None else m.recent_loss
            prev_ic = m.previous_decayed_ic if m.previous_decayed_ic is not None else m.recent_ic
            losses[m.model_id] = lam * prev_loss + (1.0 - lam) * m.recent_loss
            ics[m.model_id] = lam * prev_ic + (1.0 - lam) * m.recent_ic
        return losses, ics

    # ----------------------------------------------------------------- softmax

    @staticmethod
    def _stable_softmax(cfg: EnsembleConfig, metrics: Dict[str, float]) -> Dict[str, float]:
        """Max-shifted softmax over the weighting metric.

        EXPONENTIAL_LOSS scores by ``-beta * loss`` (lower loss wins),
        IC_SOFTMAX by ``+beta * ic`` (higher IC wins). Shifting by the maximum
        score is exact and keeps the denominator >= 1, so neither an extreme
        loss nor an extreme IC can overflow or underflow the normalisation.
        """
        beta = cfg.temperature_beta
        sign = -1.0 if cfg.weighting_method == EXPONENTIAL_LOSS else 1.0
        scores = {m_id: sign * beta * value for m_id, value in metrics.items()}
        shift = max(scores.values())
        exponentials = {m_id: math.exp(s - shift) for m_id, s in scores.items()}
        total = math.fsum(exponentials.values())  # >= 1.0 by construction
        return {m_id: e / total for m_id, e in exponentials.items()}

    # --------------------------------------------------------- circuit breakers

    @staticmethod
    def _apply_circuit_breakers(
        cfg: EnsembleConfig,
        models: List[ModelTelemetry],
        metrics: Dict[str, float],
        decayed_ics: Dict[str, float],
        raw_normalized: Dict[str, float],
    ) -> Tuple[List[ModelWeightStatus], Dict[str, float]]:
        """Classify each model. Breakers run most- to least-specific:
        insufficient history, then anti-predictive IC, then weight floor.

        A single pass is sufficient. Renormalising over the survivors divides by
        a sum <= 1, so every surviving weight can only increase; no model that
        cleared the floor before renormalisation can fall below it after.
        """
        statuses: List[ModelWeightStatus] = []
        active_weights: Dict[str, float] = {}

        for m in models:
            m_id = m.model_id
            raw_w = raw_normalized[m_id]
            decayed_ic = decayed_ics[m_id]

            if m.days_active < cfg.min_days_active:
                status = STATUS_PENDING_WARMUP
            elif cfg.demote_on_negative_ic and decayed_ic <= 0.0:
                status = STATUS_DEMOTED_NEGATIVE_IC
            elif raw_w < cfg.min_weight_floor:
                status = STATUS_DEMOTED_BELOW_FLOOR
            else:
                status = STATUS_ACTIVE
                active_weights[m_id] = raw_w

            statuses.append(ModelWeightStatus(
                model_id=m_id,
                raw_weight=round(raw_w, WEIGHT_PRECISION),
                final_normalized_weight=0.0,
                decayed_metric=round(metrics[m_id], WEIGHT_PRECISION),
                is_active=(status == STATUS_ACTIVE),
                status=status,
                decayed_ic=round(decayed_ic, WEIGHT_PRECISION),
            ))

        return statuses, active_weights

    # ------------------------------------------------------------ normalisation

    @staticmethod
    def _normalize_active(
        statuses: List[ModelWeightStatus], active_weights: Dict[str, float]
    ) -> None:
        """Renormalise survivors to sum to exactly 1.0 at WEIGHT_PRECISION.

        Rounding each weight independently leaves a residual (0.999... or
        1.000...1) that is three orders of magnitude larger than the reporting
        precision, which violates the sum-to-one invariant the downstream
        allocator relies on. The residual is applied to the largest active
        weight, where it is proportionally smallest.

        The corrected weights sum to exactly 1.0 under exact summation
        (``math.fsum``). A naive left-to-right ``sum()`` over many weights can
        still differ by one or two ULPs (~2e-16): that is IEEE-754 accumulation
        in the caller's summation, not a residual in the allocation. Callers
        asserting the invariant should use ``math.fsum``.
        """
        total = math.fsum(active_weights.values())
        active = [s for s in statuses if s.is_active]
        for s in active:
            s.final_normalized_weight = round(active_weights[s.model_id] / total, WEIGHT_PRECISION)

        residual = round(1.0 - math.fsum(s.final_normalized_weight for s in active), WEIGHT_PRECISION)
        if residual:
            largest = max(active, key=lambda s: s.final_normalized_weight)
            largest.final_normalized_weight = round(
                largest.final_normalized_weight + residual, WEIGHT_PRECISION
            )

    # ---------------------------------------------------------------- reporting

    @staticmethod
    def _half_life(cfg: EnsembleConfig) -> Optional[float]:
        if not 0.0 < cfg.decay_factor_lambda < 1.0:
            return None
        return round(half_life_periods(cfg.decay_factor_lambda), 4)

    def _success_report(
        self,
        ensemble_id: str,
        cfg: EnsembleConfig,
        statuses: List[ModelWeightStatus],
        active_count: int,
        demoted_count: int,
        pending_count: int,
    ) -> EnsembleWeightReport:
        half_life = self._half_life(cfg)
        half_life_note = "n/a" if half_life is None else f"{half_life} periods"
        notes = (
            f"ENSEMBLE REWEIGHTED [{ensemble_id}]: {active_count} active, "
            f"{demoted_count} demoted, {pending_count} pending warm-up. "
            f"Method = {cfg.weighting_method}, "
            f"lambda = {cfg.decay_factor_lambda} (half-life {half_life_note}), "
            f"beta = {cfg.temperature_beta}, floor = {cfg.min_weight_floor}."
        )
        logger.info(notes)
        return EnsembleWeightReport(
            ensemble_id=ensemble_id,
            active_model_count=active_count,
            demoted_model_count=demoted_count,
            model_statuses=statuses,
            status=ENSEMBLE_REWEIGHTED_SUCCESS,
            audit_notes=notes,
            weighting_method=cfg.weighting_method,
            decay_half_life_periods=half_life,
            pending_warmup_model_count=pending_count,
        )

    def _halted_report(
        self,
        ensemble_id: str,
        cfg: EnsembleConfig,
        statuses: List[ModelWeightStatus],
        demoted_count: int,
        pending_count: int,
    ) -> EnsembleWeightReport:
        """No model is eligible for weight. Allocate nothing.

        Reached either because every model failed a breaker or because every
        model is still warming up. Both are "no usable model"; the per-model
        status and the count split say which.

        Falling back to equal weights here would re-admit exactly the models the
        breakers rejected -- including anti-predictive ones -- under a SUCCESS
        status, which is the failure mode this branch exists to prevent.
        """
        breakdown = ", ".join(f"{s.model_id}={s.status}" for s in statuses)
        notes = (
            f"ENSEMBLE HALTED [{ensemble_id}]: all {len(statuses)} models withheld "
            f"({breakdown}). No capital allocated. Method = {cfg.weighting_method}, "
            f"floor = {cfg.min_weight_floor}, demote_on_negative_ic = "
            f"{cfg.demote_on_negative_ic}."
        )
        logger.error(notes)
        return EnsembleWeightReport(
            ensemble_id=ensemble_id,
            active_model_count=0,
            demoted_model_count=demoted_count,
            model_statuses=statuses,
            status=ENSEMBLE_HALTED_ALL_DEMOTED,
            audit_notes=notes,
            weighting_method=cfg.weighting_method,
            decay_half_life_periods=self._half_life(cfg),
            pending_warmup_model_count=pending_count,
        )
