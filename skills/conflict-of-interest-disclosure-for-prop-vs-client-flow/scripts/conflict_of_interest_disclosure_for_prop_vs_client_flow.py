import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ClientOrder:
    order_id: str
    symbol: str
    side: str                          # 'BUY' or 'SELL'
    quantity: int
    limit_price: float
    info_barrier_id: str               # Barrier/Desk ID
    is_institutional: bool = False
    opted_out_5320: bool = False       # Annual disclosure opt-out signed

@dataclass
class PropOrder:
    order_id: str
    symbol: str
    side: str                          # 'BUY' or 'SELL'
    quantity: int
    price: float
    info_barrier_id: str

@dataclass
class ConflictAuditResult:
    is_approved: bool
    violation_type: Optional[str]      # e.g. 'FINRA_RULE_5320_FRONT_RUNNING'
    exception_applied: Optional[str]  # e.g. 'NO_KNOWLEDGE_BARRIER', 'INSTITUTIONAL_OPT_OUT'
    reason: str

class PropVsClientConflictEngine:
    """
    Audits Proprietary (PROP) trading orders against unexecuted Client Agency orders
    to enforce FINRA Rule 5320 (Manning Rule) and Information Barrier isolation.
    """
    def __init__(self, pending_client_orders: List[ClientOrder] = None):
        self.pending_client_orders = pending_client_orders or []

    def evaluate_prop_order(self, prop_order: PropOrder) -> ConflictAuditResult:
        """
        Audits a proposed Prop order against all active pending client orders.
        """
        matching_client_orders = [
            co for co in self.pending_client_orders 
            if co.symbol == prop_order.symbol and co.side == prop_order.side
        ]

        if not matching_client_orders:
            return ConflictAuditResult(
                is_approved=True, violation_type=None,
                exception_applied=None, reason="No matching pending client orders found."
            )

        for client_order in matching_client_orders:
            # 1. Price Conflict Check
            # BUY: Prop buying at >= Client limit price
            # SELL: Prop selling at <= Client limit price
            has_price_conflict = False
            if prop_order.side == 'BUY' and prop_order.price >= client_order.limit_price:
                has_price_conflict = True
            elif prop_order.side == 'SELL' and prop_order.price <= client_order.limit_price:
                has_price_conflict = True

            if not has_price_conflict:
                continue

            # Potential Conflict Detected! Check Exceptions:

            # Exception A: No-Knowledge Exception (Information Barrier Isolation)
            if prop_order.info_barrier_id != client_order.info_barrier_id:
                logger.info(f"No-Knowledge Exception applied for Prop Order {prop_order.order_id} vs Client {client_order.order_id}")
                return ConflictAuditResult(
                    is_approved=True,
                    violation_type=None,
                    exception_applied="NO_KNOWLEDGE_BARRIER",
                    reason="Information barrier separation active (different info_barrier_id)."
                )

            # Exception B: Institutional Disclosure Opt-Out Exception (>= 10,000 shares or >= $100k value)
            order_val = client_order.quantity * client_order.limit_price
            if client_order.is_institutional and client_order.opted_out_5320 and (client_order.quantity >= 10000 or order_val >= 100000.0):
                logger.info(f"Institutional Opt-Out Exception applied for Client Order {client_order.order_id}")
                return ConflictAuditResult(
                    is_approved=True,
                    violation_type=None,
                    exception_applied="INSTITUTIONAL_OPT_OUT",
                    reason="Client signed institutional Rule 5320 opt-out disclosure."
                )

            # No exception applies -> FINRA Rule 5320 Front-Running Violation!
            logger.critical(
                f"FINRA Rule 5320 Violation: Prop order {prop_order.order_id} ({prop_order.side} {prop_order.quantity} @ ${prop_order.price}) "
                f"trades ahead of client order {client_order.order_id} (Limit ${client_order.limit_price})."
            )
            return ConflictAuditResult(
                is_approved=False,
                violation_type="FINRA_RULE_5320_FRONT_RUNNING",
                exception_applied=None,
                reason=f"Prop order trades ahead of unexecuted client order {client_order.order_id} on same side."
            )

        return ConflictAuditResult(
            is_approved=True, violation_type=None,
            exception_applied=None, reason="Order approved; no price conflicts with client orders."
        )
