"""
arrival-price-benchmark-execution-algo:
Generates optimal execution trajectories (child order sizes) based on trader urgency
to minimize Implementation Shortfall relative to the Arrival Price.
"""
import dataclasses
import logging
import math
from enum import Enum
from typing import List

logger = logging.getLogger(__name__)


class UrgencyLevel(Enum):
    LOW = "LOW"        # Risk-neutral: minimizes market impact (approaches TWAP)
    MEDIUM = "MEDIUM"  # Balanced: standard Almgren-Chriss curve
    HIGH = "HIGH"      # Risk-averse: front-loads execution to minimize timing risk


@dataclasses.dataclass
class ExecutionTrajectory:
    total_size: int
    num_bins: int
    urgency: UrgencyLevel
    child_order_sizes: List[int]


class ArrivalPriceTrajectoryGenerator:
    """
    Simulates the Almgren-Chriss optimal execution trajectory.
    High urgency (high lambda) results in an exponential decay curve (front-loaded).
    Low urgency (low lambda) results in a flatter, near-linear curve.
    """

    def _get_kappa(self, urgency: UrgencyLevel) -> float:
        # Kappa represents the decay rate of the remaining order size.
        # It is derived from the risk aversion (lambda), volatility, and market impact.
        if urgency == UrgencyLevel.HIGH:
            return 3.0    # Rapid decay, heavily front-loaded
        elif urgency == UrgencyLevel.MEDIUM:
            return 1.0    # Standard decay
        else: # LOW
            return 0.1    # Extremely slow decay, almost uniform

    def generate_schedule(self, total_size: int, num_bins: int, urgency: UrgencyLevel) -> ExecutionTrajectory:
        if total_size <= 0:
            raise ValueError("Total size must be strictly positive.")
        if num_bins <= 0:
            raise ValueError("Number of time bins must be strictly positive.")

        kappa = self._get_kappa(urgency)
        remaining_shares = total_size
        child_sizes = []

        # We calculate the trajectory using a discretized exponential decay function.
        # x_t = X * sinh(kappa * (T - t)) / sinh(kappa * T) 
        # For simplicity in this heuristic engine, we use an exponential decay rate based on kappa.
        
        for t in range(num_bins):
            if t == num_bins - 1:
                # Last bin: execute all remaining shares
                trade_size = remaining_shares
            else:
                # Calculate proportion of original total size to execute in this bin
                # High kappa = large initial chunks. Low kappa = ~ 1/num_bins
                if urgency == UrgencyLevel.LOW:
                    # Approach uniform distribution (TWAP)
                    trade_size = int(total_size / num_bins)
                else:
                    # Exponential front-loading
                    fraction = 1.0 - math.exp(-kappa / num_bins)
                    # Boost initial bins for high urgency
                    time_weight = math.exp(-kappa * (t / num_bins)) 
                    trade_size = int(total_size * fraction * time_weight * (num_bins / 2))
                
                # Prevent over-allocation and ensure at least 1 share if there are remaining
                trade_size = max(1, min(trade_size, remaining_shares))

            child_sizes.append(trade_size)
            remaining_shares -= trade_size

        # Adjust for integer rounding by adding any residual to the first bin (front-loading the remainder)
        if remaining_shares > 0:
            child_sizes[0] += remaining_shares
        elif remaining_shares < 0:
            # Should not happen due to min() bounds, but safe-guard
            pass

        logger.info(f"Generated {urgency.value} urgency trajectory for {total_size} shares across {num_bins} bins.")
        
        return ExecutionTrajectory(
            total_size=total_size,
            num_bins=num_bins,
            urgency=urgency,
            child_order_sizes=child_sizes
        )
