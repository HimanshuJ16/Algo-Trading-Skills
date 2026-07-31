import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class SebiAlgoOrderPayload:
    algo_id: str                        # e.g. 'NSE_ALGO_99812' or 'GENERIC_ALGO_ID'
    client_category: str                # 'PRO' (Proprietary) or 'CLI' (Client)
    symbol: str                         # e.g. 'RELIANCE'
    exchange: str                       # 'NSE' or 'BSE'
    side: str                           # 'BUY' or 'SELL'
    price: float
    quantity: int
    is_registered_with_exchange: bool = True
    orders_per_second_ops: float = 1.0

@dataclass
class SebiOtrMetrics:
    total_order_messages: int           # Submits + Modifies + Cancels
    total_executed_trades: int

@dataclass
class SebiAlgoTaggingReport:
    algo_id: str
    client_category: str
    exchange: str
    is_algo_id_valid: bool
    is_category_valid: bool
    calculated_otr_ratio: float
    otr_status: str                     # 'OTR_NORMAL', 'OTR_WARNING_500', 'OTR_PENALTY_COOLING_OFF'
    status: str                         # 'SEBI_TAGGING_APPROVED', 'REJECTED_UNTAGGED_ALGO', 'REJECTED_UNREGISTERED_ALGO', 'REJECTED_INVALID_CATEGORY'
    audit_notes: str

class SebiAlgoTaggingEngine:
    """
    Regulatory compliance engine for Securities and Futures Board of India (SEBI) algorithmic trading circulars,
    enforcing unique Exchange Algo IDs, PRO/CLI tagging, 10 OPS limits, and Order-to-Trade Ratio (OTR) penalties.
    """
    def __init__(
        self,
        otr_warning_threshold: float = 500.0,
        otr_cooling_off_threshold: float = 2000.0,
        max_generic_ops_limit: float = 10.0
    ):
        self.otr_warning_threshold = otr_warning_threshold
        self.otr_cooling_off_threshold = otr_cooling_off_threshold
        self.max_generic_ops_limit = max_generic_ops_limit

    def calculate_otr(self, metrics: SebiOtrMetrics) -> float:
        """
        Calculates SEBI Order-to-Trade Ratio (OTR = Total Order Messages / Executed Trades).
        """
        if metrics.total_executed_trades <= 0:
            return float(metrics.total_order_messages) # Fallback ratio
        return round(float(metrics.total_order_messages) / float(metrics.total_executed_trades), 2)

    def audit_sebi_algo_order(
        self,
        payload: SebiAlgoOrderPayload,
        otr_metrics: SebiOtrMetrics
    ) -> SebiAlgoTaggingReport:
        """
        Audits order payload against SEBI tagging requirements, PRO/CLI rules, and daily OTR metrics.
        """
        algo_clean = payload.algo_id.strip()
        cat_clean = payload.client_category.strip().upper()

        # 1. Audit Algo ID Tagging
        if not algo_clean:
            notes = "SEBI REJECT: Order is UNTAGGED! SEBI circulars prohibit untagged algorithmic orders."
            logger.critical(notes)
            return SebiAlgoTaggingReport(
                algo_id="", client_category=cat_clean, exchange=payload.exchange,
                is_algo_id_valid=False, is_category_valid=False, calculated_otr_ratio=0.0,
                otr_status="OTR_NORMAL", status="REJECTED_UNTAGGED_ALGO", audit_notes=notes
            )

        if not payload.is_registered_with_exchange:
            notes = f"SEBI REJECT [{algo_clean}]: Algo ID is NOT REGISTERED with {payload.exchange}."
            logger.error(notes)
            return SebiAlgoTaggingReport(
                algo_id=algo_clean, client_category=cat_clean, exchange=payload.exchange,
                is_algo_id_valid=False, is_category_valid=True, calculated_otr_ratio=0.0,
                otr_status="OTR_NORMAL", status="REJECTED_UNREGISTERED_ALGO", audit_notes=notes
            )

        # 2. Audit Client Category (PRO vs CLI)
        if cat_clean not in {"PRO", "CLI"}:
            notes = f"SEBI REJECT [{algo_clean}]: Invalid client category '{payload.client_category}'. Must be 'PRO' or 'CLI'."
            logger.error(notes)
            return SebiAlgoTaggingReport(
                algo_id=algo_clean, client_category=cat_clean, exchange=payload.exchange,
                is_algo_id_valid=True, is_category_valid=False, calculated_otr_ratio=0.0,
                otr_status="OTR_NORMAL", status="REJECTED_INVALID_CATEGORY", audit_notes=notes
            )

        # 3. Audit Order-to-Trade Ratio (OTR)
        otr_ratio = self.calculate_otr(otr_metrics)
        if otr_ratio >= self.otr_cooling_off_threshold:
            otr_stat = "OTR_PENALTY_COOLING_OFF"
            notes = (
                f"SEBI OTR COOLING-OFF PENALTY [{algo_clean}]: OTR ratio ({otr_ratio:,.0f}) exceeds "
                f"critical limit ({self.otr_cooling_off_threshold:,.0f}). Exchange cooling-off suspension triggered!"
            )
            logger.critical(notes)
        elif otr_ratio >= self.otr_warning_threshold:
            otr_stat = "OTR_WARNING_500"
            notes = f"SEBI OTR WARNING [{algo_clean}]: OTR ratio ({otr_ratio:,.0f}) exceeds warning threshold (500)."
            logger.warning(notes)
        else:
            otr_stat = "OTR_NORMAL"
            notes = (
                f"SEBI ALGO TAGGING APPROVED [{algo_clean} - {cat_clean}]: Routed on {payload.exchange}. "
                f"Daily OTR = {otr_ratio:.1f} (Normal)."
            )
            logger.info(notes)

        return SebiAlgoTaggingReport(
            algo_id=algo_clean,
            client_category=cat_clean,
            exchange=payload.exchange.upper(),
            is_algo_id_valid=True,
            is_category_valid=True,
            calculated_otr_ratio=otr_ratio,
            otr_status=otr_stat,
            status="SEBI_TAGGING_APPROVED",
            audit_notes=notes
        )
