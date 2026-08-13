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

Limiting cases (see Almgren & Chriss, 1999):
  * ``kappa -> 0`` (risk-neutral / LOW urgency): trajectory becomes linear,
    i.e. a uniform TWAP schedule.
  * ``kappa -> infinity`` (infinitely risk-averse / HIGH urgency): trajectory
    degenerates to immediate execution at t=0.

References:
  - Almgren, R. & Chriss, N. (1999). "Optimal Execution of Portfolio
    Transactions".
  - Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality".
"""
import dataclasses
import logging
import math
from enum import Enum
from typing import List

logger = logging.getLogger(__name__)

__all__ = [
    "UrgencyLevel",
    "ExecutionTrajectory",
    "ArrivalPriceTrajectoryGenerator",
]


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
        # sinh(x) overflows near x ~= 710 (close to float64 max). When
        # kappa*T is that large the schedule degenerates to immediate
        # execution at t=0 anyway, so short-circuit before calling sinh.
        # Below the underflow threshold (denom ~ 0) the curve is effectively
        # linear and is handled by the uniform branch instead.
        kt = kappa * T
        if kt > 700.0:
            sizes = [0] * num_bins
            sizes[0] = total_size
            return sizes
        denom = math.sinh(kt)
        if not math.isfinite(denom) or denom < 1e-12:
            sizes = [0] * num_bins
            sizes[0] = total_size
            return sizes

        remaining = [
            total_size * math.sinh(kappa * (T - t)) / denom
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
