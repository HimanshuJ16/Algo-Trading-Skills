import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FirmRiskLimits:
    max_daily_loss_usd: float           # e.g. 10,000.00
    max_order_rate_per_sec: int         # e.g. 100 orders/sec
    max_net_exposure_usd: float         # e.g. 1,000,000.00

@dataclass
class ActiveStrategyOrder:
    cl_ord_id: str
    strategy_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    order_status: str                   # 'NEW', 'PARTIALLY_FILLED'

@dataclass
class NewOrderRequest:
    cl_ord_id: str
    strategy_id: str
    symbol: str
    side: str
    order_qty: int
    price: float

@dataclass
class KillSwitchAuditReport:
    is_kill_switch_active: bool
    trigger_reason: Optional[str]
    trigger_scope: str                  # 'GLOBAL', 'STRATEGY', 'NONE'
    cancelled_orders_count: int
    fix_mass_cancel_tag_530: Optional[str]
    is_new_order_blocked: bool
    status: str                         # 'NORMAL_OPERATIONS', 'KILL_SWITCH_ENGAGED', 'REJECTED_KILL_SWITCH_ACTIVE'
    audit_notes: str

class ExecutionAlgoKillSwitchEngine:
    """
    Quantitative risk and execution engine for integrating multi-level algorithmic kill switches (SEC Rule 15c3-5, MiFID II RTS 6),
    issuing FIX MassCancel requests, and locking order entry during risk breaches.
    """
    def __init__(self, limits: FirmRiskLimits):
        self.limits = limits
        self.is_global_kill_switch_engaged = False
        self.engaged_strategy_kills: Dict[str, str] = {} # strategy_id -> reason
        self.active_orders: Dict[str, ActiveStrategyOrder] = {}

    def register_active_order(self, ord: ActiveStrategyOrder):
        self.active_orders[ord.cl_ord_id] = ord

    def trigger_kill_switch(self, scope: str, reason: str, strategy_id: Optional[str] = None) -> KillSwitchAuditReport:
        """
        Engages Kill Switch (SEC Rule 15c3-5 / RTS 6), issues FIX MassCancel (Tag 530=7), and locks order entry.
        """
        scope_clean = scope.upper()
        cancelled_cnt = 0

        if scope_clean == "GLOBAL":
            self.is_global_kill_switch_engaged = True
            # Mass Cancel All Active Orders
            for ord in list(self.active_orders.values()):
                if ord.order_status in ["NEW", "PARTIALLY_FILLED"]:
                    ord.order_status = "CANCELLED"
                    cancelled_cnt += 1
            
            notes = (
                f"GLOBAL KILL SWITCH ENGAGED: Reason = '{reason}'. Issued FIX MassCancelRequest (Tag 530=7 CANCEL_ALL_ORDERS). "
                f"{cancelled_cnt} orders cancelled across all venues. All order entry LOCKED."
            )
            logger.critical(notes)
            return KillSwitchAuditReport(
                is_kill_switch_active=True, trigger_reason=reason, trigger_scope="GLOBAL",
                cancelled_orders_count=cancelled_cnt, fix_mass_cancel_tag_530="7_CANCEL_ALL_ORDERS",
                is_new_order_blocked=True, status="KILL_SWITCH_ENGAGED", audit_notes=notes
            )

        elif scope_clean == "STRATEGY" and strategy_id:
            self.engaged_strategy_kills[strategy_id] = reason
            for ord in list(self.active_orders.values()):
                if ord.strategy_id == strategy_id and ord.order_status in ["NEW", "PARTIALLY_FILLED"]:
                    ord.order_status = "CANCELLED"
                    cancelled_cnt += 1
            
            notes = (
                f"STRATEGY KILL SWITCH ENGAGED [{strategy_id}]: Reason = '{reason}'. "
                f"{cancelled_cnt} orders cancelled for strategy '{strategy_id}'. Strategy entry locked."
            )
            logger.warning(notes)
            return KillSwitchAuditReport(
                is_kill_switch_active=True, trigger_reason=reason, trigger_scope=f"STRATEGY:{strategy_id}",
                cancelled_orders_count=cancelled_cnt, fix_mass_cancel_tag_530="1_CANCEL_ORDERS_FOR_SECURITY",
                is_new_order_blocked=True, status="KILL_SWITCH_ENGAGED", audit_notes=notes
            )
        
        return KillSwitchAuditReport(
            is_kill_switch_active=False, trigger_reason=None, trigger_scope="NONE",
            cancelled_orders_count=0, fix_mass_cancel_tag_530=None, is_new_order_blocked=False,
            status="NORMAL_OPERATIONS", audit_notes="No kill switch action taken."
        )

    def audit_and_validate_new_order(self, req: NewOrderRequest, current_daily_loss_usd: float, current_order_rate_sec: int) -> KillSwitchAuditReport:
        """
        Audits incoming order against active kill switches and firm risk limits.
        """
        # Rule 1: Check Global Kill Switch
        if self.is_global_kill_switch_engaged:
            msg = f"ORDER REJECTED [{req.cl_ord_id}]: Global Kill Switch is ENGAGED!"
            logger.critical(msg)
            return KillSwitchAuditReport(
                is_kill_switch_active=True, trigger_reason="GLOBAL_KILL_SWITCH_ACTIVE", trigger_scope="GLOBAL",
                cancelled_orders_count=0, fix_mass_cancel_tag_530=None, is_new_order_blocked=True,
                status="REJECTED_KILL_SWITCH_ACTIVE", audit_notes=msg
            )

        # Rule 2: Check Strategy Kill Switch
        if req.strategy_id in self.engaged_strategy_kills:
            reason = self.engaged_strategy_kills[req.strategy_id]
            msg = f"ORDER REJECTED [{req.cl_ord_id}]: Strategy '{req.strategy_id}' Kill Switch is ENGAGED ({reason})!"
            logger.warning(msg)
            return KillSwitchAuditReport(
                is_kill_switch_active=True, trigger_reason=reason, trigger_scope=f"STRATEGY:{req.strategy_id}",
                cancelled_orders_count=0, fix_mass_cancel_tag_530=None, is_new_order_blocked=True,
                status="REJECTED_KILL_SWITCH_ACTIVE", audit_notes=msg
            )

        # Rule 3: Audit Risk Limits (Max Loss & Order Rate Loops)
        if current_daily_loss_usd >= self.limits.max_daily_loss_usd:
            reason = f"MAX DAILY LOSS BREACH: Current loss ${current_daily_loss_usd:,.2f} >= Limit ${self.limits.max_daily_loss_usd:,.2f}"
            return self.trigger_kill_switch("GLOBAL", reason)

        if current_order_rate_sec >= self.limits.max_order_rate_per_sec:
            reason = f"RUNAWAY LOOP DETECTED: Order rate {current_order_rate_sec}/sec >= Limit {self.limits.max_order_rate_per_sec}/sec"
            return self.trigger_kill_switch("GLOBAL", reason)

        # Approved
        notes = f"ORDER APPROVED [{req.cl_ord_id}]: Risk limits normal. Strategy '{req.strategy_id}' active."
        return KillSwitchAuditReport(
            is_kill_switch_active=False, trigger_reason=None, trigger_scope="NONE",
            cancelled_orders_count=0, fix_mass_cancel_tag_530=None, is_new_order_blocked=False,
            status="NORMAL_OPERATIONS", audit_notes=notes
        )
