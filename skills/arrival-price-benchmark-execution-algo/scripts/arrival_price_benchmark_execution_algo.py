"""
arrival-price-benchmark-execution-algo:
Generates optimal execution trajectories (child order sizes) based on trader
urgency to minimize Implementation Shortfall relative to the Arrival Price.

The trajectory follows the closed-form Almgren-Chriss solution:

    x_t = X * sinh(kappa * (T - t)) / sinh(kappa * T)

where ``x_t`` is the number of shares remaining at the start of time bin ``t``,
``X`` is the total parent order size, ``T`` is the number of time bins, and
``kappa`` encodes the trader's risk aversion (urgency). The trade size for bin
``t`` is the difference ``x_t - x_{t+1}``, which is always non-negative and
strictly decreasing for ``kappa > 0`` (a front-loaded schedule).

Limiting cases (Almgren & Chriss 2000, Eqs. 9-13):
  * ``kappa -> 0`` (risk-neutral / LOW urgency): trajectory becomes linear,
    i.e. a uniform TWAP schedule.
  * ``kappa -> infinity`` (infinitely risk-averse / HIGH urgency): trajectory
    degenerates to immediate execution at t=0.

Note that a *long horizon* is not one of those limiting cases. The trade's
half-life ``1/kappa`` is set by the security's dynamics and impact parameters,
not by ``T`` (Sec. 2.3), so raising ``T`` at fixed ``kappa`` leaves the leading
bins unchanged -- at ``kappa = 1`` the first bin holds ~63.2% of the parent
whether the horizon is 10 bins or 10,000. See ``_sinh_ratio``.

This module also provides ``forecast_shortfall``, the Almgren-Chriss expected
cost and variance of a given schedule (Eqs. 5 and 8), for checking realised
execution against what the model predicted.

References:
  - Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio
    Transactions". *Journal of Risk* 3(2), 5-39.
    https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
    Eq. (17) trajectory, Eq. (18) trade list, Eqs. (5)/(8) cost sums,
    Eq. (20) closed-form E/V of the optimal strategy.
  - Perold, A. F. (1988). "The Implementation Shortfall: Paper Versus Reality".
    *Journal of Portfolio Management* 14(3), 4-9.
"""
import dataclasses
import logging
import math
from enum import Enum
from typing import List, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "UrgencyLevel",
    "ExecutionTrajectory",
    "ArrivalPriceTrajectoryGenerator",
    "ImpactParameters",
    "ShortfallForecast",
    "forecast_shortfall",
]


def _sinh_ratio(a: float, b: float) -> float:
    """
    ``sinh(a) / sinh(b)`` for ``0 <= a <= b`` with ``b > 0``, without overflow.

    Evaluating the ratio directly breaks down twice over. ``math.sinh(b)``
    itself overflows once ``b`` passes ~710.48 (the float64 limit), and the
    intermediate ``total_size * sinh(...)`` product overflows to ``inf`` at a
    *lower* threshold that depends on the order size -- after which
    ``inf - inf`` yields ``NaN``. Factoring out ``exp(-b)`` avoids both:

        sinh(a)/sinh(b) = exp(a - b) * (1 - exp(-2a)) / (1 - exp(-2b))

    ``expm1`` keeps the small-argument end accurate too, so the identity holds
    across the whole range rather than only where ``sinh`` happens to fit.

    Getting this right matters because the Almgren-Chriss half-life ``1/kappa``
    is independent of the horizon ``T`` (Almgren & Chriss 2000, Sec. 2.3): a
    long horizon does *not* collapse the schedule into an immediate dump. At
    ``kappa = 1`` the first bin is ~63.2% of the parent whether the horizon is
    10 bins or 10,000.
    """
    if a <= 0.0:
        return 0.0
    return math.exp(a - b) * math.expm1(-2.0 * a) / math.expm1(-2.0 * b)


class UrgencyLevel(Enum):
    """Trader urgency, proxying the risk-aversion parameter lambda in Almgren-Chriss."""

    LOW = "LOW"        # Risk-neutral: minimizes market impact (approaches TWAP)
    MEDIUM = "MEDIUM"  # Balanced: standard Almgren-Chriss curve
    HIGH = "HIGH"      # Risk-averse: front-loads execution to minimize timing risk


@dataclasses.dataclass(frozen=True)
class ExecutionTrajectory:
    """Immutable description of a child-order schedule across time bins."""

    total_size: int
    num_bins: int
    urgency: UrgencyLevel
    child_order_sizes: List[int]


class ArrivalPriceTrajectoryGenerator:
    """
    Generates Almgren-Chriss optimal execution trajectories.

    High urgency (high kappa) produces a steeply front-loaded exponential-style
    decay curve. Low urgency produces a uniform TWAP schedule (the kappa -> 0
    limit of the same closed-form solution, special-cased for exact uniformity
    and integer cleanliness).
    """

    # Kappa values chosen so that, over a 10-bin horizon, HIGH front-loads
    # ~63% of the order into the first bin and MEDIUM ~40%, both with smooth
    # monotonically decreasing tails and no degenerate single-bin dump.
    # LOW is handled by the uniform branch (the kappa -> 0 limit).
    _KAPPA = {
        UrgencyLevel.HIGH: 1.0,
        UrgencyLevel.MEDIUM: 0.5,
        UrgencyLevel.LOW: 0.0,
    }

    def _get_kappa(self, urgency: UrgencyLevel) -> float:
        """Return the decay rate kappa for the given urgency level."""
        return self._KAPPA[urgency]

    @staticmethod
    def _apportion(float_sizes: List[float], total_size: int) -> List[int]:
        """
        Convert fractional trade sizes to integers that sum exactly to
        ``total_size`` using the largest-remainder (Hamilton) method.

        This preserves the shape of the trajectory far better than naive
        floor-and-dump-residual, and guarantees every bin receives a
        non-negative integer allocation.
        """
        floors = [int(math.floor(s)) for s in float_sizes]
        remainder = total_size - sum(floors)
        n = len(float_sizes)
        if remainder <= 0:
            return floors
        # Award the leftover shares to the bins with the largest fractional
        # remainders. Ties broken by earlier bin index (stable sort) so the
        # residual skews toward the front of the schedule, matching the
        # front-loading intent of the algorithm.
        fractional = sorted(
            range(n),
            key=lambda i: (float_sizes[i] - floors[i], -i),
            reverse=True,
        )
        for i in range(remainder):
            floors[fractional[i]] += 1
        return floors

    def generate_schedule(
        self, total_size: int, num_bins: int, urgency: UrgencyLevel
    ) -> ExecutionTrajectory:
        """
        Compute the child-order schedule for a parent order.

        Args:
            total_size: Total number of shares to execute (must be > 0).
            num_bins: Number of equal time bins the horizon is split into
                (must be > 0).
            urgency: Trader urgency level driving the risk-aversion kappa.

        Returns:
            An ``ExecutionTrajectory`` whose ``child_order_sizes`` sum exactly
            to ``total_size`` and contain only non-negative integers.

        Raises:
            ValueError: if ``total_size`` or ``num_bins`` is not a positive
                integer.
            TypeError: if ``total_size`` or ``num_bins`` is not an ``int``, or
                ``urgency`` is not an ``UrgencyLevel``.
        """
        if not isinstance(total_size, int) or isinstance(total_size, bool):
            raise TypeError("total_size must be an int.")
        if not isinstance(num_bins, int) or isinstance(num_bins, bool):
            raise TypeError("num_bins must be an int.")
        if not isinstance(urgency, UrgencyLevel):
            raise TypeError("urgency must be an UrgencyLevel.")
        if total_size <= 0:
            raise ValueError("Total size must be strictly positive.")
        if num_bins <= 0:
            raise ValueError("Number of time bins must be strictly positive.")

        kappa = self._get_kappa(urgency)
        child_sizes = self._trajectory(total_size, num_bins, kappa)

        logger.info(
            "Generated %s urgency trajectory for %d shares across %d bins "
            "(kappa=%.3f); first bin=%d, last bin=%d.",
            urgency.value,
            total_size,
            num_bins,
            kappa,
            child_sizes[0],
            child_sizes[-1],
        )

        return ExecutionTrajectory(
            total_size=total_size,
            num_bins=num_bins,
            urgency=urgency,
            child_order_sizes=child_sizes,
        )

    def _trajectory(self, total_size: int, num_bins: int, kappa: float) -> List[int]:
        """Return the integer child-order sizes for a given kappa."""
        # Low urgency: the kappa -> 0 limit is exactly linear (uniform TWAP).
        # Special-cased to guarantee a clean, flat, integer schedule.
        if kappa == 0.0:
            return self._uniform_schedule(total_size, num_bins)

        # General case: closed-form Almgren-Chriss remaining-position curve.
        # x_t = X * sinh(kappa * (T - t)) / sinh(kappa * T), t = 0..T
        # trade_t = x_t - x_{t+1}  (shares executed during bin t)
        T = float(num_bins)
        # The ratio is evaluated in a scaled form that never materialises
        # sinh(kappa*T) itself, so arbitrarily long horizons stay exact
        # instead of overflowing to inf/NaN. See ``_sinh_ratio``.
        remaining = [
            total_size * _sinh_ratio(kappa * (T - t), kappa * T)
            for t in range(num_bins + 1)
        ]
        # remaining[0] == total_size, remaining[num_bins] == 0.0
        float_trades = [remaining[t] - remaining[t + 1] for t in range(num_bins)]
        int_trades = self._apportion(float_trades, total_size)

        # Defensive invariants: non-negative and sum-correct. The apportionment
        # already guarantees these for well-conditioned kappa; clamp as a guard
        # against floating-point edge cases at extreme parameters.
        int_trades = [max(0, s) for s in int_trades]
        drift = total_size - sum(int_trades)
        if drift != 0:
            int_trades[0] += drift
        return int_trades

    @staticmethod
    def _uniform_schedule(total_size: int, num_bins: int) -> List[int]:
        """Flat TWAP schedule: equal shares per bin, residual to the first bin."""
        base = total_size // num_bins
        sizes = [base] * num_bins
        residual = total_size - sum(sizes)
        if residual > 0:
            sizes[0] += residual
        return sizes


@dataclasses.dataclass(frozen=True)
class ImpactParameters:
    """
    Linear market-impact parameters of Almgren & Chriss (2000), Eqs. (6)-(8).

    All time-dimensioned quantities must share one time unit. The schedules
    produced by :class:`ArrivalPriceTrajectoryGenerator` treat one bin as one
    time unit, so pass ``tau=1.0`` and express ``sigma`` per sqrt(bin) to price
    a schedule from ``generate_schedule`` directly.

    Attributes:
        sigma: Volatility in price units per sqrt(time unit). Eq. (1).
        eta: Temporary impact coefficient, ($/share)/(share/time). Eq. (7).
        gamma: Permanent impact coefficient, ($/share)/share. Eq. (6).
        epsilon: Fixed cost per share -- half-spread plus fees, $/share. Eq. (7).
        tau: Length of one time bin, in the shared time unit.
    """

    sigma: float
    eta: float
    gamma: float = 0.0
    epsilon: float = 0.0
    tau: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("sigma", "eta", "gamma", "epsilon", "tau"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{field_name} must be a real number.")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite.")
        if self.sigma < 0.0:
            raise ValueError("sigma must be non-negative.")
        if self.gamma < 0.0:
            raise ValueError("gamma must be non-negative.")
        if self.epsilon < 0.0:
            raise ValueError("epsilon must be non-negative.")
        if self.eta <= 0.0:
            raise ValueError("eta must be strictly positive.")
        if self.tau <= 0.0:
            raise ValueError("tau must be strictly positive.")
        # Almgren & Chriss (2000), following Eq. (8): the cost functional is
        # strictly convex only while eta_tilde = eta - gamma*tau/2 > 0. Outside
        # that range the "optimal" trajectory is not a minimiser at all, so
        # refuse rather than return a meaningless number.
        if self.eta_tilde <= 0.0:
            raise ValueError(
                "eta_tilde = eta - gamma*tau/2 must be strictly positive for the "
                f"Almgren-Chriss cost model to be convex (got eta={self.eta}, "
                f"gamma={self.gamma}, tau={self.tau})."
            )

    @property
    def eta_tilde(self) -> float:
        """Temporary impact net of the permanent-impact credit: ``eta - gamma*tau/2``."""
        return self.eta - 0.5 * self.gamma * self.tau


@dataclasses.dataclass(frozen=True)
class ShortfallForecast:
    """Expected implementation shortfall and its variance for a schedule."""

    expected_cost: float
    variance: float
    stdev: float
    objective: float
    risk_aversion: float


def forecast_shortfall(
    child_order_sizes: Sequence[float],
    params: ImpactParameters,
    risk_aversion: float = 0.0,
) -> ShortfallForecast:
    """
    Expected shortfall ``E(x)`` and variance ``V(x)`` of a trading schedule.

    Computed from the defining sums of Almgren & Chriss (2000) -- Eq. (8) for
    the expectation under linear impact, Eq. (5) for the variance -- evaluated
    on the schedule actually being traded:

        E(x) = gamma*X^2/2 + epsilon*sum|n_k| + (eta_tilde/tau)*sum n_k^2
        V(x) = sigma^2 * tau * sum_{k=1..N} x_k^2

    where ``n_k`` is the size of bin ``k`` and ``x_k`` the shares still
    outstanding after it. The closed-form Eq. (20) applies only to the exact
    unrounded optimum; summing the definitions instead prices the integer
    schedule the algo will really send, and avoids the catastrophic
    cancellation and overflow that make Eq. (20) awkward to evaluate at small
    or large ``kappa*T``. The two agree to floating-point precision on the
    exact trajectory, which the unit tests assert directly.

    ``E`` is in currency units and ``V`` in currency squared, so a like-for-like
    comparison against realised shortfall uses ``stdev``, never ``variance``.

    Args:
        child_order_sizes: Per-bin sizes, all non-negative and finite. Typically
            ``ExecutionTrajectory.child_order_sizes``.
        params: Calibrated impact parameters.
        risk_aversion: Lambda in the Almgren-Chriss objective ``E + lambda*V``.
            Must be non-negative; 0.0 reports the risk-neutral cost only.

    Returns:
        A ``ShortfallForecast``.

    Raises:
        TypeError: if the arguments are not of the expected types.
        ValueError: if any size is negative or non-finite, the schedule is
            empty or sums to zero, or ``risk_aversion`` is negative.
    """
    if not isinstance(params, ImpactParameters):
        raise TypeError("params must be an ImpactParameters.")
    if isinstance(child_order_sizes, (str, bytes)):
        raise TypeError("child_order_sizes must be a sequence of numbers.")
    if not isinstance(risk_aversion, (int, float)) or isinstance(risk_aversion, bool):
        raise TypeError("risk_aversion must be a real number.")
    if not math.isfinite(float(risk_aversion)) or risk_aversion < 0.0:
        raise ValueError("risk_aversion must be finite and non-negative.")

    sizes = list(child_order_sizes)
    if not sizes:
        raise ValueError("child_order_sizes must not be empty.")
    for size in sizes:
        if not isinstance(size, (int, float)) or isinstance(size, bool):
            raise TypeError("child_order_sizes must contain only real numbers.")
        if not math.isfinite(float(size)):
            raise ValueError("child_order_sizes must be finite.")
        if size < 0.0:
            raise ValueError("child_order_sizes must be non-negative.")

    total = math.fsum(float(n) for n in sizes)
    if total <= 0.0:
        raise ValueError("child_order_sizes must sum to a positive quantity.")

    # Permanent impact across the whole parent, plus fixed per-share costs, plus
    # temporary impact, which is quadratic in each bin's size (Eq. 8). Sizes are
    # non-negative, so sum|n_k| == total.
    sum_squares = math.fsum(float(n) * float(n) for n in sizes)
    expected_cost = (
        0.5 * params.gamma * total * total
        + params.epsilon * total
        + (params.eta_tilde / params.tau) * sum_squares
    )

    # Variance of shortfall: volatility acting on the shares still outstanding
    # at the end of each bin (Eq. 5). The sum runs k = 1..N and so excludes the
    # full parent x_0, which is never exposed to post-decision drift.
    remaining = total
    squared_exposures = []
    for size in sizes:
        remaining -= float(size)
        squared_exposures.append(remaining * remaining)
    variance = params.sigma * params.sigma * params.tau * math.fsum(squared_exposures)

    objective = expected_cost + risk_aversion * variance

    logger.info(
        "Shortfall forecast over %d bins for %.0f shares: E=%.4f, sd=%.4f, "
        "objective(lambda=%.6g)=%.4f.",
        len(sizes),
        total,
        expected_cost,
        math.sqrt(variance),
        risk_aversion,
        objective,
    )

    return ShortfallForecast(
        expected_cost=expected_cost,
        variance=variance,
        stdev=math.sqrt(variance),
        objective=objective,
        risk_aversion=float(risk_aversion),
    )
