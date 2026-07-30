import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class RestingBookOrder:
    cl_ord_id: str
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price: float
    smp_id: str                         # FIX Tag 7928

@dataclass
class SmpOrderRequest:
    cl_ord_id: str
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price: float
    smp_id: str                         # FIX Tag 7928
    smp_instruction: str                # 'CANCEL_RESTING', 'CANCEL_AGGRESSIVE', 'CANCEL_BOTH', 'DECREMENT_AND_CANCEL'

@dataclass
class SmpAuditReport:
    cl_ord_id: str
    symbol: str
    smp_id: str
    smp_instruction: str
    has_self_collision: bool
    colliding_resting_ord_id: Optional[str]
    is_order_dispatched: bool
    resting_order_cancelled: bool
    fix_tag_7928_smp_id: str
    fix_tag_8000_smp_instruction: str
    audit_notes: str

class ExchangeSelfMatchPreventionEngine:
    """
    Quantitative exchange order routing engine for configuring native Self-Match Prevention (SMP / STP) FIX attributes
    (Tag 7928 SMP ID, Tag 8000 Instruction), auditing self-collisions, and preventing wash trades.
    """
    def __init__(self, default_smp_instruction: str = "CANCEL_RESTING"):
        self.default_smp_instruction = default_smp_instruction

    def format_fix_smp_tags(self, smp_id: str, instruction: str) -> Tuple[str, str]:
        # FIX Tag 7928 = SMP ID, FIX Tag 8000 = SMP Instruction
        return str(smp_id), str(instruction.upper())

    def audit_and_apply_smp(
        self,
        req: SmpOrderRequest,
        resting_orders: List[RestingBookOrder]
    ) -> SmpAuditReport:
        """
        Audits pre-trade order book for self-collisions with matching smp_id on opposite side.
        """
        tag_7928, tag_8000 = self.format_fix_smp_tags(req.smp_id, req.smp_instruction)

        # Look for opposing resting order with same smp_id
        opp_side = "SELL" if req.side.upper() == "BUY" else "BUY"
        colliding_ord = None

        for r_ord in resting_orders:
            if r_ord.symbol == req.symbol and r_ord.side.upper() == opp_side and r_ord.smp_id == req.smp_id:
                # Check price overlap
                if req.side.upper() == "BUY" and req.price >= r_ord.price:
                    colliding_ord = r_ord
                    break
                elif req.side.upper() == "SELL" and req.price <= r_ord.price:
                    colliding_ord = r_ord
                    break

        if not colliding_ord:
            notes = f"SMP APPROVED [{req.cl_ord_id}]: No self-collision detected. Routed with Tag 7928={tag_7928}, Tag 8000={tag_8000}."
            logger.info(notes)
            return SmpAuditReport(
                cl_ord_id=req.cl_ord_id, symbol=req.symbol, smp_id=req.smp_id, smp_instruction=req.smp_instruction,
                has_self_collision=False, colliding_resting_ord_id=None, is_order_dispatched=True,
                resting_order_cancelled=False, fix_tag_7928_smp_id=tag_7928, fix_tag_8000_smp_instruction=tag_8000, audit_notes=notes
            )

        # Self-collision detected! Apply SMP Instruction
        instruction_mode = req.smp_instruction.upper()
        is_dispatched = True
        resting_cancelled = False

        if instruction_mode == "CANCEL_RESTING":
            is_dispatched = True
            resting_cancelled = True
            notes = f"SMP COLLISION [{req.cl_ord_id}]: CANCEL_RESTING executed. Resting order '{colliding_ord.cl_ord_id}' marked for cancellation; aggressive order dispatched."
            logger.warning(notes)
        elif instruction_mode == "CANCEL_AGGRESSIVE":
            is_dispatched = False
            resting_cancelled = False
            notes = f"SMP COLLISION [{req.cl_ord_id}]: CANCEL_AGGRESSIVE executed. Incoming aggressive order blocked; resting order '{colliding_ord.cl_ord_id}' preserved."
            logger.warning(notes)
        elif instruction_mode == "CANCEL_BOTH":
            is_dispatched = False
            resting_cancelled = True
            notes = f"SMP COLLISION [{req.cl_ord_id}]: CANCEL_BOTH executed. Both incoming '{req.cl_ord_id}' and resting '{colliding_ord.cl_ord_id}' cancelled."
            logger.warning(notes)
        else: # Default CANCEL_RESTING
            is_dispatched = True
            resting_cancelled = True
            notes = f"SMP COLLISION [{req.cl_ord_id}]: Default CANCEL_RESTING applied."

        return SmpAuditReport(
            cl_ord_id=req.cl_ord_id,
            symbol=req.symbol,
            smp_id=req.smp_id,
            smp_instruction=req.smp_instruction,
            has_self_collision=True,
            colliding_resting_ord_id=colliding_ord.cl_ord_id,
            is_order_dispatched=is_dispatched,
            resting_order_cancelled=resting_cancelled,
            fix_tag_7928_smp_id=tag_7928,
            fix_tag_8000_smp_instruction=tag_8000,
            audit_notes=notes
        )
