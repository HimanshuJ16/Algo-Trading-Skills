"""Fractional-Kelly capital reallocation across live trading strategies.

The engine converts each strategy's recent trade statistics into a fractional
Kelly *exposure target*, then resolves those targets against per-strategy
capacity ceilings and the size of the fund.

Key semantics (see SKILL.md for the rationale):

* ``kelly_fraction`` scales gross deployed exposure. It is NOT normalised away:
  a portfolio whose fractional-Kelly targets sum to 0.4 deploys 40% of the fund
  and holds 60% in cash.
* Capital is never levered. If the fractional-Kelly targets sum above 1.0 they
  are scaled down proportionally.
* No strategy is ever funded above ``min(max_capacity, fund * fractional_kelly)``
  -- capital freed by a capacity-capped strategy is redistributed only up to the
  other strategies' own Kelly targets, never beyond them.
* Any capital that cannot be deployed within those constraints stays in cash.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Set

logger = logging.getLogger(__name__)


def _check_finite(value: float, field: str, context: str) -> float:
    """Reject NaN/Inf, which would otherwise propagate silently into weights."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{context}: '{field}' must be a finite number, got {value!r}")
    return numeric


@dataclass
class StrategyMetrics:
    """Trailing performance statistics for one strategy.

    ``avg_win`` and ``avg_loss`` are positive *magnitudes* of the average
    winning and losing trade, expressed in the same currency unit.

    Validation runs at construction: an allocation engine must never be handed
    NaN, a negative capacity, or a win rate outside [0, 1].
    """

    strategy_id: str
    current_capital: float
    max_capacity: float
    # Recent performance metrics
    win_rate: float
    avg_win: float
    avg_loss: float

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("StrategyMetrics: 'strategy_id' must be a non-empty string")
        ctx = f"StrategyMetrics({self.strategy_id})"

        for field in ("current_capital", "max_capacity", "win_rate", "avg_win", "avg_loss"):
            setattr(self, field, _check_finite(getattr(self, field), field, ctx))

        if not 0.0 <= self.win_rate <= 1.0:
            raise ValueError(f"{ctx}: 'win_rate' must be in [0.0, 1.0], got {self.win_rate}")
        for field in ("current_capital", "max_capacity", "avg_win", "avg_loss"):
            if getattr(self, field) < 0.0:
                raise ValueError(
                    f"{ctx}: '{field}' must be non-negative, got {getattr(self, field)}")


@dataclass
class ReallocationInstruction:
    """Target capital for a strategy and the delta from its current funding.

    ``delta_capital`` is an instruction to the OMS risk layer to change the
    strategy's *buying power*. It is not an order: a negative delta means stop
    granting new risk and let existing positions exit naturally, not liquidate.
    """

    strategy_id: str
    delta_capital: float
    new_target_capital: float


class CapitalReallocationEngine:
    """Reallocates capital across strategies using a fractional Kelly criterion,
    bounded by per-strategy capacity limits and by the size of the fund.

    The Kelly weight for a strategy is the classic binary-bet form
    ``f* = p - q/b`` (Thorp 2007), where ``p`` is the win rate, ``q = 1 - p`` and
    ``b`` is the reward/risk ratio ``avg_win / avg_loss``.

    Limitation -- correlation: this engine sizes each strategy independently and
    sums the results. The true multi-asset Kelly solution is ``Sigma^-1 mu``,
    which accounts for the covariance between strategies. Summing independent
    Kelly fractions therefore *overstates* safe gross exposure whenever the
    strategies are positively correlated. Use a lower ``kelly_fraction`` and an
    external gross-exposure cap when running correlated strategies.
    """

    def __init__(self, total_fund_capital: float, kelly_fraction: float = 0.25):
        """
        Args:
            total_fund_capital: Capital available to the whole portfolio. The sum
                of all targets never exceeds this value.
            kelly_fraction: Fraction of full Kelly to bet -- 0.5 for Half-Kelly,
                0.25 for Quarter-Kelly. Must be in (0.0, 1.0]. Values above full
                Kelly are rejected: under the standard quadratic growth
                approximation, betting ``c`` times Kelly retains ``2c - c^2`` of
                the optimal growth rate, so growth falls away above c = 1 and
                turns negative beyond c = 2.
        """
        total_fund_capital = _check_finite(
            total_fund_capital, "total_fund_capital", "CapitalReallocationEngine")
        kelly_fraction = _check_finite(
            kelly_fraction, "kelly_fraction", "CapitalReallocationEngine")

        if total_fund_capital < 0.0:
            raise ValueError(
                f"total_fund_capital must be non-negative, got {total_fund_capital}")
        if not 0.0 < kelly_fraction <= 1.0:
            raise ValueError(f"kelly_fraction must be in (0.0, 1.0], got {kelly_fraction}")

        self.total_fund_capital = total_fund_capital
        self.kelly_fraction = kelly_fraction
        # Currency-scale tolerance: absolute for small funds, relative for large.
        self._tolerance = max(1e-6, total_fund_capital * 1e-12)

    def _calculate_kelly_weight(self, metrics: StrategyMetrics) -> float:
        """Full Kelly fraction for one strategy: ``f* = W - (1 - W) / R``.

        ``W`` is the win rate and ``R = avg_win / avg_loss`` the reward/risk
        ratio. Returns 0.0 (no allocation) when the edge cannot be estimated or
        is non-positive. Because ``W <= 1`` and ``(1 - W)/R >= 0``, the result is
        bounded above by 1.0.
        """
        if metrics.avg_loss <= 0.0:
            # No observed downside: R is undefined, so the edge is unestimable.
            # Refuse to size rather than assume an infinite edge.
            logger.warning(
                "Strategy %s reports avg_loss=%s; edge is unestimable, allocating 0.",
                metrics.strategy_id, metrics.avg_loss,
            )
            return 0.0

        r = metrics.avg_win / metrics.avg_loss
        if r <= 0.0:
            return 0.0

        w = metrics.win_rate
        kelly = w - ((1 - w) / r)

        # Floor at 0 (don't short the strategy)
        return max(0.0, kelly)

    def _fractional_kelly_targets(
        self, strategies: Dict[str, StrategyMetrics]
    ) -> Dict[str, float]:
        """Fractional-Kelly exposure of each strategy as a fraction of the fund."""
        return {
            sid: self._calculate_kelly_weight(metrics) * self.kelly_fraction
            for sid, metrics in strategies.items()
        }

    def _water_fill(
        self,
        budget: float,
        weights: Dict[str, float],
        ceilings: Dict[str, float],
    ) -> Dict[str, float]:
        """Distribute ``budget`` pro-rata by ``weights``, capped by ``ceilings``.

        Capital freed by a strategy that hits its ceiling is redistributed among
        the strategies still below theirs, repeating until the budget is spent or
        every strategy is capped. The result depends only on the inputs, never on
        dictionary iteration order.
        """
        targets: Dict[str, float] = {sid: 0.0 for sid in weights}
        active: Set[str] = {
            sid for sid in weights if weights[sid] > 0.0 and ceilings[sid] > 0.0
        }
        remaining = budget

        while remaining > self._tolerance and active:
            weight_sum = sum(weights[sid] for sid in active)
            if weight_sum <= 0.0:
                break

            capped: List[str] = [
                sid for sid in sorted(active)
                if remaining * (weights[sid] / weight_sum) > ceilings[sid] + self._tolerance
            ]

            if not capped:
                for sid in active:
                    targets[sid] = remaining * (weights[sid] / weight_sum)
                remaining = 0.0
                break

            for sid in capped:
                targets[sid] = ceilings[sid]
                remaining -= ceilings[sid]
                active.discard(sid)

        return targets

    def reallocate(
        self, strategies: Dict[str, StrategyMetrics]
    ) -> Dict[str, ReallocationInstruction]:
        """Compute target capital and deltas for every registered strategy.

        Strategies with no measurable edge are targeted to zero capital. Capital
        that cannot be deployed within the Kelly targets and capacity ceilings is
        left in cash rather than being forced into the remaining strategies.
        """
        if not strategies:
            return {}

        for sid, metrics in strategies.items():
            if sid != metrics.strategy_id:
                raise ValueError(
                    f"Strategy key {sid!r} does not match "
                    f"metrics.strategy_id {metrics.strategy_id!r}")

        fractions = self._fractional_kelly_targets(strategies)
        gross_exposure = sum(fractions.values())

        if gross_exposure <= 0.0:
            logger.warning("No edge detected across portfolio. Reverting to cash.")
            return {
                sid: ReallocationInstruction(sid, -metrics.current_capital, 0.0)
                for sid, metrics in strategies.items()
            }

        # Never lever: if fractional-Kelly demand exceeds the fund, scale it back.
        # Otherwise the shortfall is deliberately held as cash.
        budget = min(self.total_fund_capital, self.total_fund_capital * gross_exposure)
        if gross_exposure > 1.0:
            logger.info(
                "Gross fractional-Kelly demand %.4fx exceeds fund capital; "
                "scaling targets down.", gross_exposure,
            )

        # A strategy is never funded beyond its own Kelly target or its capacity.
        ceilings = {
            sid: min(strategies[sid].max_capacity, self.total_fund_capital * fractions[sid])
            for sid in strategies
        }
        final_targets = self._water_fill(budget, fractions, ceilings)

        deployed = sum(final_targets.values())
        cash_reserve = self.total_fund_capital - deployed
        logger.info(
            "Reallocation: deployed=%.2f (%.2f%% of fund), cash reserve=%.2f, "
            "gross Kelly demand=%.4fx",
            deployed,
            100.0 * deployed / self.total_fund_capital if self.total_fund_capital > 0 else 0.0,
            cash_reserve,
            gross_exposure,
        )
        if budget - deployed > self._tolerance:
            logger.warning(
                "Capacity limits stranded %.2f of the %.2f Kelly budget; held as cash.",
                budget - deployed, budget,
            )

        instructions: Dict[str, ReallocationInstruction] = {}
        for sid in sorted(strategies):
            metrics = strategies[sid]
            target = final_targets.get(sid, 0.0)
            delta = target - metrics.current_capital
            instructions[sid] = ReallocationInstruction(sid, delta, target)
            logger.info(
                "Strategy %s: Current=%.2f -> Target=%.2f (Delta: %.2f)",
                sid, metrics.current_capital, target, delta,
            )

        return instructions
