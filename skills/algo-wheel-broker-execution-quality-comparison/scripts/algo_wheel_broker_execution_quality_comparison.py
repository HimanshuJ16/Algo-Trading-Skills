"""Broker-wheel allocation from validated implementation-shortfall data."""

from __future__ import annotations

import dataclasses
import logging
import math
from collections.abc import Iterable, Sequence
from numbers import Real
from typing import Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class BrokerExecution:
    broker_id: str
    side: str
    decision_price: float
    fill_price: float
    quantity: float
    fees_usd: float


class AlgoWheelEvaluator:
    """Rank brokers by notional-weighted implementation shortfall.

    The default wheel gives every non-leading broker the configured minimum
    canary allocation and assigns the remaining flow to the leading broker.
    """

    def __init__(self, min_allocation: float = 0.10):
        self._validate_allocation(min_allocation)
        self.min_allocation = float(min_allocation)

    def calculate_implementation_shortfall_bps(
        self, exec_data: BrokerExecution
    ) -> float:
        """Return signed implementation shortfall in basis points.

        Positive values are execution costs; negative values are price
        improvement. Explicit fees must use the same currency as the notional.
        """

        return round(self._calculate_implementation_shortfall_bps(exec_data), 2)

    def evaluate_brokers(
        self, executions: Iterable[BrokerExecution]
    ) -> dict[str, float]:
        """Return deterministic target allocations for the supplied executions.

        Broker scores are weighted by decision notional, so a large execution
        cannot be hidden by a large number of small executions.
        """

        if executions is None:
            raise TypeError("executions must be an iterable of BrokerExecution")

        broker_stats: dict[str, list[float]] = {}
        for execution in executions:
            self._validate_execution(execution)
            broker_id = execution.broker_id.strip()
            shortfall_bps = self._calculate_implementation_shortfall_bps(execution)
            notional = execution.decision_price * execution.quantity
            weighted_shortfall = shortfall_bps * notional

            if broker_id not in broker_stats:
                broker_stats[broker_id] = [0.0, 0.0]
            broker_stats[broker_id][0] += weighted_shortfall
            broker_stats[broker_id][1] += notional

        if not broker_stats:
            return {}

        average_shortfall = {
            broker_id: weighted_sum / total_notional
            for broker_id, (weighted_sum, total_notional) in broker_stats.items()
        }
        ranked_brokers = sorted(
            average_shortfall.items(), key=lambda item: (item[1], item[0])
        )

        logger.info(
            "broker_rankings_calculated",
            extra={"rankings": ranked_brokers},
        )
        return self._assign_wheel_weights(ranked_brokers)

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
        self, ranked_brokers: Sequence[tuple[str, float]]
    ) -> dict[str, float]:
        if not ranked_brokers:
            return {}
        if len(ranked_brokers) == 1:
            return {ranked_brokers[0][0]: 1.0}

        non_leading_allocation = self.min_allocation * (len(ranked_brokers) - 1)
        if non_leading_allocation >= 1.0:
            raise ValueError(
                "min_allocation is too large for the number of brokers; "
                "non-leading allocations must leave flow for the leader"
            )

        allocations = {broker_id: self.min_allocation for broker_id, _ in ranked_brokers[1:]}
        allocations[ranked_brokers[0][0]] = 1.0 - non_leading_allocation
        return allocations

    @staticmethod
    def _validate_allocation(min_allocation: float) -> None:
        if not isinstance(min_allocation, Real) or isinstance(min_allocation, bool):
            raise TypeError("min_allocation must be a finite number")
        if not math.isfinite(float(min_allocation)) or not 0.0 < float(min_allocation) < 1.0:
            raise ValueError("min_allocation must be greater than 0 and less than 1")

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
