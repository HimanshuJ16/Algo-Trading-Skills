import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class HkSfcOrderRequest:
    algo_id: str
    stock_code: str                     # e.g. '00700'
    side: str                           # 'BUY' or 'SELL' or 'SHORT_SELL'
    order_price: float
    order_quantity: int
    market_last_price: float
    developer_registration_active: bool = True
    is_short_sell: bool = False
    has_locate_borrow: bool = False     # Mandatory locate check for covered short selling

@dataclass
class HkSfcComplianceReport:
    algo_id: str
    stock_code: str
    order_value_hkd: float
    price_deviation_pct: float
    is_short_sell_legal: bool
    is_developer_authorized: bool
    is_kill_switch_active: bool
    status: str                         # 'SFC_COMPLIANT_APPROVED', 'REJECTED_UNAUTHORIZED_DEVELOPER', 'REJECTED_PRICE_DEVIATION_LIMIT', 'REJECTED_ORDER_VALUE_LIMIT', 'REJECTED_ILLEGAL_NAKED_SHORT'
    audit_notes: str

class HkSfcAlgorithmicTradingEngine:
    """
    Hong Kong Securities and Futures Commission (SFC) compliance engine enforcing Schedule 7 Code of Conduct rules
    for algorithmic trading, pre-trade controls, short selling locate checks, and kill switch mass cancels.
    """
    def __init__(
        self,
        max_order_value_hkd: float = 10_000_000.0,
        max_price_deviation_pct: float = 5.0
    ):
        self.max_order_value_hkd = max_order_value_hkd
        self.max_price_deviation_pct = max_price_deviation_pct
        self.is_kill_switch_active = False

    def trigger_sfc_kill_switch(self) -> str:
        """
        SFC Schedule 7 Para 4 Emergency Kill Switch Trigger.
        """
        self.is_kill_switch_active = True
        msg = "HK SFC KILL SWITCH ACTIVATED: All algo order submissions HALTED. Mass Cancel initiated."
        logger.critical(msg)
        return msg

    def reset_kill_switch(self) -> None:
        self.is_kill_switch_active = False

    def audit_sfc_compliance(self, req: HkSfcOrderRequest) -> HkSfcComplianceReport:
        """
        Audits order against HK SFC Schedule 7 pre-trade rules and short selling laws.
        """
        if self.is_kill_switch_active:
            notes = f"SFC REJECT [{req.algo_id}]: Emergency Kill Switch is ACTIVE. Order blocked."
            logger.critical(notes)
            return HkSfcComplianceReport(
                algo_id=req.algo_id, stock_code=req.stock_code, order_value_hkd=0.0,
                price_deviation_pct=0.0, is_short_sell_legal=False, is_developer_authorized=req.developer_registration_active,
                is_kill_switch_active=True, status="REJECTED_KILL_SWITCH_ACTIVE", audit_notes=notes
            )

        # 1. Qualification & Authorization (Para 1-2)
        if not req.developer_registration_active:
            notes = f"SFC REJECT [{req.algo_id}]: Developer/RO registration inactive or UAT sign-off missing."
            logger.error(notes)
            return HkSfcComplianceReport(
                algo_id=req.algo_id, stock_code=req.stock_code, order_value_hkd=0.0,
                price_deviation_pct=0.0, is_short_sell_legal=False, is_developer_authorized=False,
                is_kill_switch_active=False, status="REJECTED_UNAUTHORIZED_DEVELOPER", audit_notes=notes
            )

        # Calculate metrics
        order_value = round(req.order_price * req.order_quantity, 2)
        dev_pct = round(abs((req.order_price - req.market_last_price) / float(req.market_last_price)) * 100.0, 2)

        # 2. Pre-Trade Price & Value Controls (Para 3)
        if order_value > self.max_order_value_hkd:
            notes = f"SFC REJECT [{req.algo_id}]: Order value HKD ${order_value:,.2f} exceeds max limit HKD ${self.max_order_value_hkd:,.2f}."
            logger.warning(notes)
            return HkSfcComplianceReport(
                algo_id=req.algo_id, stock_code=req.stock_code, order_value_hkd=order_value,
                price_deviation_pct=dev_pct, is_short_sell_legal=True, is_developer_authorized=True,
                is_kill_switch_active=False, status="REJECTED_ORDER_VALUE_LIMIT", audit_notes=notes
            )

        if dev_pct > self.max_price_deviation_pct:
            notes = f"SFC REJECT [{req.algo_id}]: Price deviation {dev_pct:.2f}% exceeds max limit {self.max_price_deviation_pct:.2f}%."
            logger.warning(notes)
            return HkSfcComplianceReport(
                algo_id=req.algo_id, stock_code=req.stock_code, order_value_hkd=order_value,
                price_deviation_pct=dev_pct, is_short_sell_legal=True, is_developer_authorized=True,
                is_kill_switch_active=False, status="REJECTED_PRICE_DEVIATION_LIMIT", audit_notes=notes
            )

        # 3. Covered Short Selling Audit (SFO Sec 170)
        is_short = req.is_short_sell or (req.side.upper() == "SHORT_SELL")
        if is_short and not req.has_locate_borrow:
            notes = f"SFC REJECT [{req.algo_id}]: Illegal naked short selling prohibited under HK law! Missing borrow locate."
            logger.critical(notes)
            return HkSfcComplianceReport(
                algo_id=req.algo_id, stock_code=req.stock_code, order_value_hkd=order_value,
                price_deviation_pct=dev_pct, is_short_sell_legal=False, is_developer_authorized=True,
                is_kill_switch_active=False, status="REJECTED_ILLEGAL_NAKED_SHORT", audit_notes=notes
            )

        notes = (
            f"SFC COMPLIANT APPROVED [{req.algo_id} - {req.stock_code}]: "
            f"{req.side} {req.order_quantity} shares @ HKD ${req.order_price:.2f} (Value = HKD ${order_value:,.2f}, Dev = {dev_pct:.2f}%)."
        )
        logger.info(notes)

        return HkSfcComplianceReport(
            algo_id=req.algo_id,
            stock_code=req.stock_code,
            order_value_hkd=order_value,
            price_deviation_pct=dev_pct,
            is_short_sell_legal=True,
            is_developer_authorized=True,
            is_kill_switch_active=False,
            status="SFC_COMPLIANT_APPROVED",
            audit_notes=notes
        )
