import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str

class MarketAccessRuleCode(str, Enum):
    CREDIT_CAP_EXCEEDED = "CREDIT_CAP_EXCEEDED"
    SINGLE_ORDER_NOTIONAL_CAP = "SINGLE_ORDER_NOTIONAL_CAP"
    SINGLE_ORDER_QTY_CAP = "SINGLE_ORDER_QTY_CAP"
    PRICE_COLLAR_FAT_FINGER = "PRICE_COLLAR_FAT_FINGER"
    SHORT_SALE_LOCATE_MISSING = "SHORT_SALE_LOCATE_MISSING"
    RESTRICTED_SECURITY = "RESTRICTED_SECURITY"
    RAPID_ORDER_BURST = "RAPID_ORDER_BURST"
    DUPLICATE_ORDER_DETECTED = "DUPLICATE_ORDER_DETECTED"

@dataclass
class MarketAccessOrder:
    order_id: str
    account_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL', 'SELL_SHORT'
    quantity: float
    price: float
    nbbo_mid_price: float = 100.0
    accumulated_credit_used_usd: float = 0.0
    short_locate_id: Optional[str] = None
    timestamp_sec: float = 0.0

@dataclass
class SecRule15c35Limits:
    firm_credit_cap_usd: float = 10000000.0
    account_credit_cap_usd: float = 1000000.0
    max_single_order_notional_usd: float = 250000.0
    max_single_order_qty: float = 5000.0
    max_price_collar_pct: float = 0.05    # 5% price collar from NBBO
    max_order_rate_per_sec: int = 100
    restricted_symbols: Set[str] = field(default_factory=set)

@dataclass
class MarketAccessCheckResult:
    order_id: str
    is_allowed: bool
    triggered_violations: List[MarketAccessRuleCode]
    rejection_reasons: List[str]
    latency_microseconds: float
    audit_notes: str

class SecRule15C35RiskControlsUsEngine:
    """
    Production-grade SEC Rule 15c3-5 Market Access Risk Controls Engine enforcing mandatory
    pre-trade credit limits, single order caps, fat-finger collars, Reg SHO locates, and restricted list checks.
    """
    def __init__(self, limits: Optional[SecRule15c35Limits] = None):
        self.limits = limits or SecRule15c35Limits()
        self._last_order_timestamps: Dict[str, float] = {}

    def run_checks(self, trade_data: dict) -> ComplianceResult:
        """Legacy run_checks method retained for 100% backward compatibility."""
        if not trade_data:
            return ComplianceResult(False, "Empty trade data")
        if trade_data.get('size', 0) < 0:
            return ComplianceResult(False, "Negative size")
        return ComplianceResult(True, "OK")

    def evaluate_market_access_order(self, order: MarketAccessOrder) -> MarketAccessCheckResult:
        """
        Evaluates a proposed order against SEC Rule 15c3-5 mandatory pre-trade controls.
        """
        t0 = time.perf_counter_ns()
        violations: List[MarketAccessRuleCode] = []
        reasons: List[str] = []

        notional_usd = order.quantity * order.price

        # 1. Single Order Quantity Cap
        if order.quantity > self.limits.max_single_order_qty:
            violations.append(MarketAccessRuleCode.SINGLE_ORDER_QTY_CAP)
            reasons.append(f"Qty {order.quantity} > Max allowed qty {self.limits.max_single_order_qty}")

        # 2. Single Order Notional Cap
        if notional_usd > self.limits.max_single_order_notional_usd:
            violations.append(MarketAccessRuleCode.SINGLE_ORDER_NOTIONAL_CAP)
            reasons.append(f"Notional ${notional_usd:,.2f} > Max allowed single order notional ${self.limits.max_single_order_notional_usd:,.2f}")

        # 3. Credit Threshold Check (Account Level)
        projected_credit = order.accumulated_credit_used_usd + notional_usd
        if projected_credit > self.limits.account_credit_cap_usd:
            violations.append(MarketAccessRuleCode.CREDIT_CAP_EXCEEDED)
            reasons.append(f"Projected account credit ${projected_credit:,.2f} > Credit cap ${self.limits.account_credit_cap_usd:,.2f}")

        # 4. Fat-Finger Price Collar Check
        if order.nbbo_mid_price > 0:
            pct_diff = abs(order.price - order.nbbo_mid_price) / order.nbbo_mid_price
            if pct_diff > self.limits.max_price_collar_pct:
                violations.append(MarketAccessRuleCode.PRICE_COLLAR_FAT_FINGER)
                reasons.append(f"Price ${order.price} deviates {pct_diff:.2%} from NBBO mid ${order.nbbo_mid_price} (Max allowed {self.limits.max_price_collar_pct:.2%})")

        # 5. Short Sale Reg SHO Locate Verification
        if order.side.upper() == "SELL_SHORT" and not order.short_locate_id:
            violations.append(MarketAccessRuleCode.SHORT_SALE_LOCATE_MISSING)
            reasons.append("Short sale order missing mandatory Reg SHO short locate ID.")

        # 6. Restricted Security Check
        if order.symbol.upper() in self.limits.restricted_symbols:
            violations.append(MarketAccessRuleCode.RESTRICTED_SECURITY)
            reasons.append(f"Symbol '{order.symbol}' is on the firm restricted trading list.")

        dt_us = round((time.perf_counter_ns() - t0) / 1000.0, 2)
        is_allowed = len(violations) == 0

        status = "ALLOWED" if is_allowed else "REJECTED_15C3_5_BREACH"
        notes = (
            f"SEC RULE 15C3-5 [{status}] ({order.order_id}): Symbol = {order.symbol}, "
            f"Notional = ${notional_usd:,.2f}, Violations = {[v.value for v in violations]}, Latency = {dt_us}us."
        )

        if not is_allowed:
            logger.warning(notes)
        else:
            logger.info(notes)

        return MarketAccessCheckResult(
            order_id=order.order_id,
            is_allowed=is_allowed,
            triggered_violations=violations,
            rejection_reasons=reasons,
            latency_microseconds=dt_us,
            audit_notes=notes
        )
