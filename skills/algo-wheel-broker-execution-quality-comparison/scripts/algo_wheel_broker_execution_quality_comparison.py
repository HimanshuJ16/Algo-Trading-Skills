"""Broker-wheel allocation from validated implementation-shortfall data."""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Iterable, Sequence
from numbers import Integral, Real

logger = logging.getLogger(__name__)

#: Allocations are float shares; they sum to 1.0 only within this tolerance.
ALLOCATION_TOLERANCE = 1e-9


@dataclasses.dataclass(frozen=True)
class BrokerExecution:
    """One completed execution measured against its own decision price.

    ``quantity`` is the executed quantity. Quantity that the broker never
    executed is invisible to this record, so the resulting metric is the
    executed-quantity component of implementation shortfall only — it carries
    no opportunity cost for unfilled or cancelled residual. See
    ``references/standards.md``.
    """

    broker_id: str
    side: str
    decision_price: float
    fill_price: float
    quantity: float
    fees_usd: float


@dataclasses.dataclass(frozen=True)
class BrokerScore:
    """Aggregated evidence behind one broker's position in the ranking.

    Retained so the caller can publish the sample count and notional coverage
    that support an allocation change, not just the resulting weights.
    """

    broker_id: str
    average_shortfall_bps: float
    execution_count: int
    decision_notional: float
    eligible_to_lead: bool


class AlgoWheelEvaluator:
    """Rank brokers by notional-weighted implementation shortfall.

    The default wheel gives every non-leading broker the configured minimum
    canary allocation and assigns the remaining flow to the leading broker.

    ``min_observations`` and ``min_notional`` express the data-sufficiency
    policy that ``references/standards.md`` requires: a broker below either
    threshold still receives the canary allocation but cannot be promoted to
    lead the wheel on a thin sample. The defaults (1 execution, no notional
    floor) impose no gate; set them to the desk's approved policy.
    """

    def __init__(
        self,
        min_allocation: float = 0.10,
        min_observations: int = 1,
        min_notional: float = 0.0,
    ):
        self._validate_allocation(min_allocation)
        self._validate_sufficiency(min_observations, min_notional)
        self.min_allocation = float(min_allocation)
        self.min_observations = int(min_observations)
        self.min_notional = float(min_notional)

    def calculate_implementation_shortfall_bps(
        self, exec_data: BrokerExecution
    ) -> float:
        """Return signed implementation shortfall in basis points.

        Positive values are execution costs; negative values are price
        improvement. Explicit fees must use the same currency as the notional.
        Covers the executed quantity only.
        """

        return round(self._calculate_implementation_shortfall_bps(exec_data), 2)

    def rank_brokers(
        self, executions: Iterable[BrokerExecution]
    ) -> list[BrokerScore]:
        """Return brokers ordered best-first with the evidence behind the rank.

        Scores are weighted by decision notional, so a large execution cannot
        be hidden by a large number of small executions. Exact ties are broken
        by broker ID so the same input always produces the same order.
        """

        if executions is None:
            raise TypeError("executions must be an iterable of BrokerExecution")

        totals: dict[str, list[float]] = {}
        for execution in executions:
            shortfall_bps = self._calculate_implementation_shortfall_bps(execution)
            broker_id = execution.broker_id.strip()
            notional = execution.decision_price * execution.quantity
            if not math.isfinite(notional) or notional <= 0.0:
                raise ValueError(
                    f"decision notional for {broker_id!r} must be finite and "
                    "greater than zero"
                )

            entry = totals.setdefault(broker_id, [0.0, 0.0, 0.0])
            entry[0] += shortfall_bps * notional
            entry[1] += notional
            entry[2] += 1.0

        scores = [
            BrokerScore(
                broker_id=broker_id,
                average_shortfall_bps=weighted_sum / total_notional,
                execution_count=int(count),
                decision_notional=total_notional,
                eligible_to_lead=(
                    count >= self.min_observations
                    and total_notional >= self.min_notional
                ),
            )
            for broker_id, (weighted_sum, total_notional, count) in totals.items()
        ]
        scores.sort(key=lambda score: (score.average_shortfall_bps, score.broker_id))
        return scores

    def evaluate_brokers(
        self, executions: Iterable[BrokerExecution]
    ) -> dict[str, float]:
        """Return deterministic target allocations for the supplied executions.

        Shares sum to 1.0 within ``ALLOCATION_TOLERANCE``. When no broker meets
        the data-sufficiency policy the wheel does not promote anyone and
        returns equal weights instead.
        """

        ranked = self.rank_brokers(executions)
        logger.info(
            "broker_rankings_calculated",
            extra={"rankings": [dataclasses.astuple(score) for score in ranked]},
        )
        return self._assign_wheel_weights(ranked)

    def _calculate_implementation_shortfall_bps(
        self, exec_data: BrokerExecution
    ) -> float:
        self._validate_execution(exec_data)
        notional = exec_data.decision_price * exec_data.quantity
        fees_bps = (exec_data.fees_usd / notional) * 10000.0
        side = exec_data.side.strip().upper()

        if side == "BUY":
            slippage_bps = (
                (exec_data.fill_price - exec_data.decision_price)
                / exec_data.decision_price
                * 10000.0
            )
        else:
            slippage_bps = (
                (exec_data.decision_price - exec_data.fill_price)
                / exec_data.decision_price
                * 10000.0
            )

        return slippage_bps + fees_bps

    def _assign_wheel_weights(
        self, ranked_brokers: Sequence[BrokerScore]
    ) -> dict[str, float]:
        if not ranked_brokers:
            return {}

        leader = next(
            (score for score in ranked_brokers if score.eligible_to_lead), None
        )
        if leader is None:
            logger.warning(
                "no_broker_meets_data_sufficiency_policy",
                extra={
                    "min_observations": self.min_observations,
                    "min_notional": self.min_notional,
                },
            )
            return self._equal_weights(
                [score.broker_id for score in ranked_brokers]
            )
        if len(ranked_brokers) == 1:
            return {leader.broker_id: 1.0}

        non_leading_allocation = self.min_allocation * (len(ranked_brokers) - 1)
        leader_allocation = 1.0 - non_leading_allocation
        if leader_allocation < self.min_allocation:
            raise ValueError(
                "min_allocation is too large for the number of brokers; the "
                "leading broker would receive less than the canary minimum "
                f"({leader_allocation!r} < {self.min_allocation!r})"
            )

        allocations = {
            score.broker_id: self.min_allocation
            for score in ranked_brokers
            if score.broker_id != leader.broker_id
        }
        allocations[leader.broker_id] = 1.0 - math.fsum(allocations.values())
        self._verify_distribution(allocations)
        return allocations

    def _equal_weights(self, broker_ids: Sequence[str]) -> dict[str, float]:
        share = 1.0 / len(broker_ids)
        allocations = {broker_id: share for broker_id in broker_ids[1:]}
        allocations[broker_ids[0]] = 1.0 - math.fsum(allocations.values())
        self._verify_distribution(allocations)
        return {broker_id: allocations[broker_id] for broker_id in broker_ids}

    @staticmethod
    def _verify_distribution(allocations: dict[str, float]) -> None:
        total = math.fsum(allocations.values())
        if abs(total - 1.0) > ALLOCATION_TOLERANCE:
            raise ValueError(f"allocations must sum to 1.0, got {total!r}")
        if any(share < 0.0 for share in allocations.values()):
            raise ValueError("allocations must not be negative")

    @staticmethod
    def _validate_allocation(min_allocation: float) -> None:
        if not isinstance(min_allocation, Real) or isinstance(min_allocation, bool):
            raise TypeError("min_allocation must be a finite number")
        if not math.isfinite(float(min_allocation)) or not 0.0 < float(min_allocation) < 1.0:
            raise ValueError("min_allocation must be greater than 0 and less than 1")

    @staticmethod
    def _validate_sufficiency(min_observations: int, min_notional: float) -> None:
        if not isinstance(min_observations, Integral) or isinstance(
            min_observations, bool
        ):
            raise TypeError("min_observations must be an integer")
        if int(min_observations) < 1:
            raise ValueError("min_observations must be at least 1")
        if not isinstance(min_notional, Real) or isinstance(min_notional, bool):
            raise TypeError("min_notional must be a finite number")
        if not math.isfinite(float(min_notional)) or float(min_notional) < 0.0:
            raise ValueError("min_notional must be finite and not negative")

    @staticmethod
    def _validate_execution(exec_data: BrokerExecution) -> None:
        if not isinstance(exec_data, BrokerExecution):
            raise TypeError("execution must be a BrokerExecution")
        if not isinstance(exec_data.broker_id, str) or not exec_data.broker_id.strip():
            raise ValueError("broker_id must be a non-empty string")
        if not isinstance(exec_data.side, str) or exec_data.side.strip().upper() not in {
            "BUY",
            "SELL",
        }:
            raise ValueError("side must be BUY or SELL")

        for field_name in (
            "decision_price",
            "fill_price",
            "quantity",
            "fees_usd",
        ):
            value = getattr(exec_data, field_name)
            if not isinstance(value, Real) or isinstance(value, bool):
                raise TypeError(f"{field_name} must be a finite number")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field_name} must be finite")

        if exec_data.decision_price <= 0:
            raise ValueError("decision_price must be greater than zero")
        if exec_data.fill_price <= 0:
            raise ValueError("fill_price must be greater than zero")
        if exec_data.quantity <= 0:
            raise ValueError("quantity must be greater than zero")
