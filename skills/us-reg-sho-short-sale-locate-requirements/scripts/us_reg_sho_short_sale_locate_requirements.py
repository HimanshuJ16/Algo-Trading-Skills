import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class OrderMarking(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    SHORT_EXEMPT = "SHORT_EXEMPT"  # Statutory exemption under SEC Rule 201 (e.g. VWAP, Arbitrage)


class LocateStatus(Enum):
    LOCATE_GRANTED = "LOCATE_GRANTED"
    LOCATE_EXHAUSTED = "LOCATE_EXHAUSTED"
    LOCATE_EXPIRED = "LOCATE_EXPIRED"
    LOCATE_NOT_FOUND = "LOCATE_NOT_FOUND"


class RegSHOError(Exception):
    """Base exception for SEC Regulation SHO compliance processing errors."""
    pass


@dataclass
class LocateRecord:
    locate_id: str
    symbol: str
    quantity_allocated: int
    quantity_used: int = 0
    lender_id: str = "PRIME_BROKER_DESK"
    granted_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    expires_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.utcnow() + datetime.timedelta(hours=8))

    @property
    def remaining_quantity(self) -> int:
        return self.quantity_allocated - self.quantity_used

    @property
    def is_expired(self) -> bool:
        return datetime.datetime.utcnow() > self.expires_at


@dataclass
class OrderIntent:
    order_id: str
    symbol: str
    marking: OrderMarking
    quantity: int
    price: float
    nbb_price: float  # National Best Bid (for Rule 201 SSR verification)
    nbo_price: float
    locate_id: Optional[str] = None


@dataclass
class RegSHOValidationResult:
    order_id: str
    is_compliant: bool
    marking: OrderMarking
    locate_id: Optional[str]
    ssr_active: bool
    rejection_reason: Optional[str]
    audit_timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


class RegSHOShortSaleEngine:
    """
    Institutional SEC Regulation SHO Compliance & Short Sale Locate Engine.
    
    Enforces 17 CFR § 242.200-204 rules:
    - Rule 200: Order Marking (LONG, SHORT, SHORT_EXEMPT).
    - Rule 203(b)(1): Mandatory Short Sale Locate verification prior to order entry.
    - Rule 201: Short Sale Restriction (SSR / Alternative Uptick Rule) price test enforcement.
    - Locate pool inventory allocation and expiration tracking.
    """

    def __init__(self):
        self.locate_registry: Dict[str, LocateRecord] = {}       # locate_id -> LocateRecord
        self.ssr_active_symbols: Dict[str, bool] = {}            # symbol -> ssr_active (Rule 201)
        logger.info("Initialized SEC Regulation SHO Short Sale Locate Engine")

    def grant_locate(
        self, locate_id: str, symbol: str, quantity: int, lender_id: str = "PRIME_BROKER_1", ttl_hours: int = 8
    ) -> LocateRecord:
        """Grants a short sale locate for a specified symbol and quantity."""
        now = datetime.datetime.utcnow()
        rec = LocateRecord(
            locate_id=locate_id,
            symbol=symbol.upper(),
            quantity_allocated=quantity,
            quantity_used=0,
            lender_id=lender_id,
            granted_at=now,
            expires_at=now + datetime.timedelta(hours=ttl_hours),
        )
        self.locate_registry[locate_id] = rec
        logger.info(f"Reg SHO Locate GRANTED [{locate_id}]: Symbol={symbol}, Qty={quantity}, Lender={lender_id}")
        return rec

    def trigger_rule_201_ssr(self, symbol: str) -> None:
        """Triggers Rule 201 Short Sale Circuit Breaker (SSR) after 10% intraday price drop."""
        sym = symbol.upper()
        self.ssr_active_symbols[sym] = True
        logger.warning(f"SEC Reg SHO Rule 201 SSR ACTIVATED for symbol '{sym}' (Alternative Uptick Rule Active)")

    def deactivate_rule_201_ssr(self, symbol: str) -> None:
        """Deactivates Rule 201 SSR for a symbol."""
        sym = symbol.upper()
        if sym in self.ssr_active_symbols:
            del self.ssr_active_symbols[sym]
            logger.info(f"SEC Reg SHO Rule 201 SSR DEACTIVATED for symbol '{sym}'")

    def is_ssr_active(self, symbol: str) -> bool:
        """Checks if Rule 201 Short Sale Restriction is active for symbol."""
        return self.ssr_active_symbols.get(symbol.upper(), False)

    def validate_order_intent(self, order: OrderIntent) -> RegSHOValidationResult:
        """
        Validates an order intent against SEC Regulation SHO Rule 200, 203, and 201 mandates.
        """
        sym = order.symbol.upper()
        ssr_active = self.is_ssr_active(sym)

        # 1. LONG Orders: Require no locate and bypass Rule 201 SSR
        if order.marking == OrderMarking.LONG:
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=True,
                marking=order.marking,
                locate_id=None,
                ssr_active=ssr_active,
                rejection_reason=None,
            )

        # 2. SHORT / SHORT_EXEMPT Orders: Must verify Rule 203 Locate Requirement
        if not order.locate_id:
            logger.error(f"Reg SHO REJECTION [{order.order_id}]: Short sale missing mandatory Locate ID.")
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=False,
                marking=order.marking,
                locate_id=None,
                ssr_active=ssr_active,
                rejection_reason="Rule 203(b)(1) Violation: Short sale missing mandatory Locate Identifier.",
            )

        locate = self.locate_registry.get(order.locate_id)
        if not locate:
            logger.error(f"Reg SHO REJECTION [{order.order_id}]: Locate ID '{order.locate_id}' not found.")
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=False,
                marking=order.marking,
                locate_id=order.locate_id,
                ssr_active=ssr_active,
                rejection_reason=f"Rule 203 Violation: Locate ID '{order.locate_id}' invalid or not found.",
            )

        if locate.symbol != sym:
            logger.error(f"Reg SHO REJECTION [{order.order_id}]: Locate symbol mismatch ({locate.symbol} vs {sym}).")
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=False,
                marking=order.marking,
                locate_id=order.locate_id,
                ssr_active=ssr_active,
                rejection_reason=f"Rule 203 Violation: Locate symbol '{locate.symbol}' does not match order '{sym}'.",
            )

        if locate.is_expired:
            logger.error(f"Reg SHO REJECTION [{order.order_id}]: Locate ID '{order.locate_id}' has expired.")
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=False,
                marking=order.marking,
                locate_id=order.locate_id,
                ssr_active=ssr_active,
                rejection_reason="Rule 203 Violation: Short sale locate has expired.",
            )

        if locate.remaining_quantity < order.quantity:
            logger.error(
                f"Reg SHO REJECTION [{order.order_id}]: Insufficient locate capacity "
                f"(Req={order.quantity}, Available={locate.remaining_quantity})."
            )
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=False,
                marking=order.marking,
                locate_id=order.locate_id,
                ssr_active=ssr_active,
                rejection_reason=f"Rule 203 Violation: Insufficient locate quantity available ({locate.remaining_quantity} < {order.quantity}).",
            )

        # 3. Rule 201 Short Sale Restriction (SSR / Alternative Uptick Rule) Price Test Check
        if ssr_active and order.marking == OrderMarking.SHORT:
            # Rule 201 requires short sales to be executed strictly above current National Best Bid (NBB)
            if order.price <= order.nbb_price + 1e-6:
                logger.error(
                    f"Reg SHO REJECTION [{order.order_id}]: Rule 201 SSR Active! "
                    f"Short price ${order.price:.4f} must be ABOVE NBB ${order.nbb_price:.4f}."
                )
                return RegSHOValidationResult(
                    order_id=order.order_id,
                    is_compliant=False,
                    marking=order.marking,
                    locate_id=order.locate_id,
                    ssr_active=True,
                    rejection_reason=f"Rule 201 SSR Violation: Short price ${order.price:.4f} <= NBB ${order.nbb_price:.4f}.",
                )

        # 4. Successful Short Sale Authorization -> Reserve Locate Quantity
        locate.quantity_used += order.quantity
        logger.info(
            f"Reg SHO COMPLIANT [{order.order_id}]: Marking={order.marking.value}, "
            f"Locate={order.locate_id}, Reserved={order.quantity} (Rem={locate.remaining_quantity})"
        )

        return RegSHOValidationResult(
            order_id=order.order_id,
            is_compliant=True,
            marking=order.marking,
            locate_id=order.locate_id,
            ssr_active=ssr_active,
            rejection_reason=None,
        )

