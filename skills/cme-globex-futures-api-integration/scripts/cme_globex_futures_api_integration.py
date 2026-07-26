import logging
from dataclasses import dataclass
from typing import Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class ContractSpec:
    symbol: str
    tick_size: float
    price_band_points: float        # Maximum allowed deviation from reference price
    protection_points: float        # Market-With-Protection (MWP) offset points

@dataclass
class CmeOrder:
    symbol: str
    side: str                        # 'BUY' or 'SELL'
    quantity: int
    order_type: str                  # 'LIMIT' or 'MARKET'
    operator_id: str                 # Tag 50 (Rule 576)
    account: str
    price: Optional[float] = None    # Limit price (if LIMIT order)

@dataclass
class FormattedIlinkMessage:
    msg_type: str
    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    operator_id: str                 # Tag 50
    account: str
    is_mwp_converted: bool

class CmeGlobexOrderEngine:
    """
    CME Globex Futures Order Entry Engine.
    Enforces CME Rule 576 Tag 50 Operator ID compliance, pre-trade price banding,
    and Market-With-Protection (MWP) limit conversions.
    """
    def __init__(self, contract_specs: Dict[str, ContractSpec]):
        self.contract_specs = contract_specs

    def validate_operator_id(self, operator_id: str) -> bool:
        """
        CME Rule 576: Operator ID (Tag 50) must be 2 to 18 characters long.
        """
        if not operator_id or not isinstance(operator_id, str):
            return False
        clean_id = operator_id.strip()
        return 2 <= len(clean_id) <= 18

    def process_order(
        self, 
        order: CmeOrder, 
        cl_ord_id: str, 
        current_bid: float, 
        current_ask: float, 
        reference_price: float
    ) -> FormattedIlinkMessage:
        """
        Validates and formats order for CME Globex iLink 3 / FIX transmission.
        """
        # 1. Rule 576 Tag 50 Validation
        if not self.validate_operator_id(order.operator_id):
            raise ValueError(f"Rule 576 Violation: Invalid Operator ID (Tag 50) '{order.operator_id}'. Must be 2-18 chars.")

        # 2. Spec lookup
        if order.symbol not in self.contract_specs:
            raise KeyError(f"Unknown contract specification for symbol: {order.symbol}")
            
        spec = self.contract_specs[order.symbol]
        
        # 3. Market-With-Protection (MWP) Conversion
        is_mwp_converted = False
        target_price = order.price
        
        if order.order_type.upper() == 'MARKET':
            is_mwp_converted = True
            if order.side.upper() == 'BUY':
                # Market Buy -> Protect above Ask
                target_price = current_ask + spec.protection_points
            else:
                # Market Sell -> Protect below Bid
                target_price = current_bid - spec.protection_points
                
            logger.info(f"MWP Converted MARKET {order.side} order to LIMIT @ {target_price:.4f}")

        if target_price is None:
            raise ValueError("LIMIT order must specify a price.")

        # 4. Price Banding Check
        min_band = reference_price - spec.price_band_points
        max_band = reference_price + spec.price_band_points
        
        if not (min_band <= target_price <= max_band):
            raise ValueError(
                f"Price Banding Violation: Price {target_price:.4f} outside allowed band "
                f"[{min_band:.4f}, {max_band:.4f}] for reference {reference_price:.4f}"
            )

        # Return formatted CME Globex iLink message
        return FormattedIlinkMessage(
            msg_type="NewOrderSingle",
            cl_ord_id=cl_ord_id,
            symbol=order.symbol,
            side=order.side.upper(),
            order_qty=order.quantity,
            price=round(target_price, 4),
            operator_id=order.operator_id.strip(),
            account=order.account,
            is_mwp_converted=is_mwp_converted
        )
