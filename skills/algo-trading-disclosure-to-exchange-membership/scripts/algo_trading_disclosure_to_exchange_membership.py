"""
algo-trading-disclosure-to-exchange-membership: 
Enforces Pre-Trade Regulatory Compliance for Algo ID tagging and registration.
"""
import dataclasses
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OutboundOrder:
    order_id: str
    symbol: str
    is_algorithmic: bool
    algo_id: Optional[str] = None
    trader_id: Optional[str] = None


@dataclasses.dataclass
class OrderComplianceReport:
    order_id: str
    is_compliant: bool
    rejection_reason: Optional[str] = None


class AlgoDisclosureComplianceEngine:
    """
    Validates outbound orders against exchange membership disclosure rules,
    specifically focusing on Algo ID tagging requirements (e.g., MiFID II, SEBI).
    """
    def __init__(self, approved_algo_registry: Dict[str, str]):
        """
        :param approved_algo_registry: Map of Algo IDs to their approval status 
                                       e.g., {"TWAP_V1.2": "APPROVED"}
        """
        self.registry = approved_algo_registry

    def evaluate_order(self, order: OutboundOrder) -> OrderComplianceReport:
        if not order.is_algorithmic:
            # Human orders require different checks (e.g., short code maps), 
            # but pass the algo disclosure check by default.
            if not order.trader_id:
                return OrderComplianceReport(
                    order_id=order.order_id,
                    is_compliant=False,
                    rejection_reason="Manual order missing trader_id."
                )
            return OrderComplianceReport(order.order_id, True)

        # Rule 1: Algorithmic orders MUST have an Algo ID
        if not order.algo_id:
            msg = "Regulatory Breach Prevented: Algorithmic order missing 'algo_id' tag."
            logger.error(f"[{order.order_id}] {msg}")
            return OrderComplianceReport(order.order_id, False, msg)

        # Rule 2: The Algo ID MUST be pre-registered and approved by the exchange
        status = self.registry.get(order.algo_id)
        if status != "APPROVED":
            msg = f"Regulatory Breach Prevented: Algo ID '{order.algo_id}' is not an approved and disclosed algorithm (Status: {status})."
            logger.error(f"[{order.order_id}] {msg}")
            return OrderComplianceReport(order.order_id, False, msg)

        # Order is fully compliant with algorithmic disclosure rules
        logger.info(f"[{order.order_id}] Order compliant. Algo ID: {order.algo_id}.")
        return OrderComplianceReport(order.order_id, True)
