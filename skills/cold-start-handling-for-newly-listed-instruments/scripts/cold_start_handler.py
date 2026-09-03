"""
cold-start-handling-for-newly-listed-instruments: volatility estimation and position
sizing for instruments whose own price history is too short to estimate from.

Two separate problems are solved here, and keeping them separate is the point:

1. **Estimation.** A realized-volatility estimate from a handful of observations is
   almost pure noise: for i.i.d. normal returns the sample variance carries
   ``nu = n - 1`` degrees of freedom and relative variance ``2 / nu``, so five days of
   data give a variance estimate with roughly 70% relative standard error. The estimate
   is blended toward a peer-group prior with the weight the conjugate normal-variance
   model implies -- ``w = nu / (nu + nu_0)`` applied **in variance space**, since a
   scaled-inverse-chi-squared prior on ``sigma**2`` with ``nu_0`` degrees of freedom and
   scale ``sigma_peer**2`` gives the posterior scale
   ``(nu_0 * sigma_peer**2 + nu * s**2) / (nu_0 + nu)`` (Gelman et al., *Bayesian Data
   Analysis*, 3rd ed., ch. 2-3). Blending standard deviations instead -- the naive
   ``w * sigma_obs + (1 - w) * sigma_peer`` -- is not this estimator and, because the
   square root is concave, systematically *understates* volatility whenever the sample
   and the prior disagree. That is the wrong direction to be wrong in for a risk control.

   The posterior *mean* of ``sigma**2`` is this scale inflated by ``nu_1 / (nu_1 - 2)``
   with ``nu_1 = nu_0 + nu``; this module returns the scale, so a caller who wants the
   strictly conservative expectation should apply that factor itself.

2. **Risk appetite.** How much capital the instrument may take is a policy decision, not
   an estimator. It ramps linearly with the fraction of the warmup window that has
   elapsed and reaches the full base allocation at graduation.

Conflating the two -- using one weight for both -- forces an untrue claim: that the
sample estimator becomes exact on the day the probation window ends. It does not.
``confidence_weight`` therefore approaches 1.0 asymptotically and never equals it, while
``probation_progress`` reaches 1.0 exactly at ``warmup_period_days``.

This module holds no state between calls and performs no I/O. Sourcing ``n_obs``, the
sample volatility and the peer prior is the caller's job; see ``references/workflows.md``
for how each must be constructed to keep this estimator honest.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# A sample variance needs at least one degree of freedom, i.e. two observations.
_MIN_OBS_FOR_SAMPLE_VARIANCE = 2

# Volatilities are squared before blending, so absurd magnitudes overflow or underflow to
# an unusable result. Anything outside this range is a units error, not a volatility --
# rejecting it here turns an opaque OverflowError into a message that names the cause.
_MAX_PLAUSIBLE_VOLATILITY = 1e6
_MIN_PLAUSIBLE_PRIOR_VOLATILITY = 1e-12


@dataclass(frozen=True)
class InstrumentStatus:
    """
    Outcome of one cold-start evaluation.

    Attributes:
        symbol: Instrument identifier, echoed from the request.
        n_obs: Number of usable return observations the caller supplied.
        is_probationary: True until ``n_obs >= warmup_period_days``. Governs the size
            cap only -- it does not mean the volatility estimate is unshrunk afterwards.
        confidence_weight: Weight placed on the instrument's own sample variance, in
            ``[0.0, 1.0)``. Asymptotic: it never reaches 1.0, because a finite sample
            never fully displaces the prior.
        probation_progress: Fraction of the warmup window elapsed, in ``[0.0, 1.0]``.
        estimated_volatility: Shrunk volatility, in the same units as the inputs.
            Guaranteed finite and strictly positive.
        max_position_cap_pct: Position size ceiling as a fraction of the caller's base
            allocation unit. Non-decreasing in ``n_obs``.
        used_observed_volatility: False when the sample was ignored entirely (fewer than
            two observations), so an auditor can tell a shrunk estimate from a pure prior.
    """

    symbol: str
    n_obs: int
    is_probationary: bool
    confidence_weight: float
    probation_progress: float
    estimated_volatility: float
    max_position_cap_pct: float
    used_observed_volatility: bool


class ColdStartHandler:
    """
    Shrinks short-sample volatility toward a peer prior and ramps the size cap.

    Args:
        warmup_period_days: Observations required before the instrument graduates and
            the size cap reaches ``base_max_position_pct``. A risk-policy choice, not a
            statistical one -- see ``references/standards.md`` for the market-structure
            events (lock-up expiry, index seasoning) that should inform it. Must be a
            positive int.
        base_max_position_pct: The size ceiling a graduated instrument receives,
            expressed in whatever unit the caller sizes in (a fraction of NAV by default,
            hence the ``(0.0, 1.0]`` bound).
        prior_strength_days: ``nu_0``, the peer prior's weight expressed in degrees of
            freedom -- literally "the prior is worth this many days of this instrument's
            own data". Calibrate it from the prior's own uncertainty: for a prior whose
            estimate of ``sigma**2`` has relative variance ``v``, ``nu_0 = 2 / v``. The
            10.0 default is deliberately modest; raise it when the peer group is tight
            and directly comparable, lower it when it is a loose sector proxy.
        shrink_in_variance_space: Keep this True. False switches to the naive
            standard-deviation blend at the same weight, and exists only so a pipeline
            migrating off that estimator can quantify the difference; it understates
            volatility (see the module docstring). It does not reproduce the older outputs
            exactly, because the weight changed too.
        probation_floor_pct: Optional non-zero floor for the size cap, so a probationary
            instrument is throttled rather than excluded outright. Defaults to 0.0,
            meaning an instrument with no history of its own gets no capital.

    Raises:
        ValueError: On any invalid configuration. Failing at construction is deliberate:
            a silently degenerate risk control is worse than a crash at startup.
    """

    def __init__(
        self,
        warmup_period_days: int = 30,
        base_max_position_pct: float = 1.0,
        prior_strength_days: float = 10.0,
        shrink_in_variance_space: bool = True,
        probation_floor_pct: float = 0.0,
    ) -> None:
        if not isinstance(warmup_period_days, int) or isinstance(warmup_period_days, bool):
            raise ValueError(
                f"warmup_period_days must be an int, got {type(warmup_period_days).__name__}."
            )
        if warmup_period_days < 1:
            raise ValueError(f"warmup_period_days must be >= 1, got {warmup_period_days}.")

        base_max = float(base_max_position_pct)
        if not math.isfinite(base_max) or not 0.0 < base_max <= 1.0:
            raise ValueError(
                f"base_max_position_pct must be finite and in (0.0, 1.0], got {base_max!r}."
            )

        prior_strength = float(prior_strength_days)
        if not math.isfinite(prior_strength) or prior_strength <= 0.0:
            raise ValueError(
                "prior_strength_days must be finite and > 0 (a zero-strength prior "
                f"defeats the purpose of shrinkage), got {prior_strength!r}."
            )

        floor = float(probation_floor_pct)
        if not math.isfinite(floor) or not 0.0 <= floor <= base_max:
            raise ValueError(
                "probation_floor_pct must be finite and in [0.0, base_max_position_pct], "
                f"got {floor!r}."
            )

        self.warmup_period_days = warmup_period_days
        self.base_max_position_pct = base_max
        self.prior_strength_days = prior_strength
        self.shrink_in_variance_space = bool(shrink_in_variance_space)
        self.probation_floor_pct = floor

    # ---------------------------------------------------------------- weights

    def calculate_shrinkage_weight(self, n_obs: int) -> float:
        """
        Weight on the instrument's own sample variance: ``nu / (nu + nu_0)``.

        ``nu = n_obs - 1`` is the sample variance's degrees of freedom, so a single
        observation carries no weight at all -- there is no sample variance to weight.
        The result is strictly below 1.0 for any finite ``n_obs``.
        """
        self._validate_n_obs(n_obs)
        if n_obs < _MIN_OBS_FOR_SAMPLE_VARIANCE:
            return 0.0
        dof = float(n_obs - 1)
        return dof / (dof + self.prior_strength_days)

    def calculate_probation_progress(self, n_obs: int) -> float:
        """Fraction of the warmup window elapsed, clamped to ``[0.0, 1.0]``."""
        self._validate_n_obs(n_obs)
        return min(1.0, n_obs / self.warmup_period_days)

    # ---------------------------------------------------------------- main API

    def process_instrument(
        self,
        symbol: str,
        n_obs: int,
        observed_volatility: Optional[float] = None,
        peer_prior_volatility: Optional[float] = None,
    ) -> InstrumentStatus:
        """
        Evaluates one instrument and returns its shrunk volatility and size cap.

        Args:
            symbol: Instrument identifier, used for logging and echoed back.
            n_obs: Count of usable return observations (non-negative int). A count of
                observations actually present in the series, not a calendar difference:
                halted sessions and missing bars must already be excluded.
            observed_volatility: Sample volatility over those observations, in the same
                units as the prior (both annualized, or neither). May be omitted or NaN
                when ``n_obs < 2``, where it carries no weight and is ignored; it is
                required and must be finite and non-negative otherwise.
            peer_prior_volatility: Peer-group prior volatility. Required, finite, and
                strictly positive -- a zero prior is not a neutral fallback, it asserts a
                riskless instrument and will blow up any volatility-scaled sizer
                downstream.

        Returns:
            An :class:`InstrumentStatus`. ``estimated_volatility`` is always finite and
            strictly positive, so the caller never has to defend against NaN.

        Raises:
            ValueError: On any input that would make the result meaningless.
        """
        self._validate_n_obs(n_obs)
        prior = self._validate_prior(peer_prior_volatility)

        weight = self.calculate_shrinkage_weight(n_obs)
        progress = self.calculate_probation_progress(n_obs)
        is_probationary = n_obs < self.warmup_period_days

        if weight > 0.0:
            sample = self._validate_observed(observed_volatility, n_obs)
            used_sample = True
        else:
            # Fewer than two observations: there is no sample variance. Whatever the
            # caller passed (None, 0.0, NaN) is ignored rather than multiplied by zero,
            # because 0.0 * nan is nan, not 0.0.
            sample = prior
            used_sample = False

        if self.shrink_in_variance_space:
            blended_variance = weight * sample**2 + (1.0 - weight) * prior**2
            estimated_volatility = math.sqrt(blended_variance)
        else:
            estimated_volatility = weight * sample + (1.0 - weight) * prior

        if not math.isfinite(estimated_volatility) or estimated_volatility <= 0.0:
            # Not reachable through validated inputs, but kept as the last line of
            # defence: the alternative to raising is emitting a NaN or zero volatility
            # into a position sizer, which is what the zero-NaN standard forbids.
            raise RuntimeError(
                f"{symbol}: shrinkage produced an unusable volatility "
                f"({estimated_volatility!r}) from sample={sample!r}, prior={prior!r}, "
                f"weight={weight!r}."
            )

        cap = max(self.probation_floor_pct, self.base_max_position_pct * progress)

        if is_probationary:
            logger.info(
                "Instrument %s PROBATIONARY (%d/%d obs): sample weight %.4f, "
                "volatility %.4f, position cap %.4f",
                symbol, n_obs, self.warmup_period_days, weight,
                estimated_volatility, cap,
            )
        else:
            logger.info(
                "Instrument %s GRADUATED (%d obs): sample weight %.4f, volatility %.4f",
                symbol, n_obs, weight, estimated_volatility,
            )

        return InstrumentStatus(
            symbol=symbol,
            n_obs=n_obs,
            is_probationary=is_probationary,
            confidence_weight=weight,
            probation_progress=progress,
            estimated_volatility=estimated_volatility,
            max_position_cap_pct=cap,
            used_observed_volatility=used_sample,
        )

    # ------------------------------------------------------------- validation

    @staticmethod
    def _validate_n_obs(n_obs: int) -> None:
        if not isinstance(n_obs, int) or isinstance(n_obs, bool):
            raise ValueError(f"n_obs must be an int, got {type(n_obs).__name__}.")
        if n_obs < 0:
            raise ValueError(f"n_obs must be >= 0, got {n_obs}.")

    @staticmethod
    def _validate_prior(peer_prior_volatility: Optional[float]) -> float:
        if peer_prior_volatility is None:
            raise ValueError(
                "peer_prior_volatility is required: without a prior there is nothing to "
                "shrink toward and a short sample would be used raw."
            )
        prior = float(peer_prior_volatility)
        if not math.isfinite(prior):
            raise ValueError(f"peer_prior_volatility must be finite, got {prior!r}.")
        if prior <= 0.0:
            raise ValueError(
                f"peer_prior_volatility must be > 0, got {prior!r}. Zero is not a "
                "neutral fallback for a missing prior."
            )
        if not _MIN_PLAUSIBLE_PRIOR_VOLATILITY <= prior <= _MAX_PLAUSIBLE_VOLATILITY:
            raise ValueError(
                f"peer_prior_volatility {prior!r} is outside the plausible range "
                f"[{_MIN_PLAUSIBLE_PRIOR_VOLATILITY}, {_MAX_PLAUSIBLE_VOLATILITY}] and "
                "would overflow or underflow when squared -- check the units."
            )
        return prior

    @staticmethod
    def _validate_observed(observed_volatility: Optional[float], n_obs: int) -> float:
        if observed_volatility is None:
            raise ValueError(
                f"observed_volatility is required when n_obs ({n_obs}) supports a "
                "sample variance."
            )
        sample = float(observed_volatility)
        if not math.isfinite(sample):
            raise ValueError(
                f"observed_volatility must be finite, got {sample!r}. A NaN sample "
                "usually means the return series has gaps that n_obs did not account for."
            )
        if sample < 0.0:
            raise ValueError(f"observed_volatility must be >= 0, got {sample!r}.")
        if sample > _MAX_PLAUSIBLE_VOLATILITY:
            raise ValueError(
                f"observed_volatility {sample!r} exceeds {_MAX_PLAUSIBLE_VOLATILITY} and "
                "would overflow when squared -- check the units."
            )
        return sample
