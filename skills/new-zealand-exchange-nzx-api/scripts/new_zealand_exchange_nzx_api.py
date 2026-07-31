import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class NZXOrderRequest:
    cl_ord_id: str
    symbol: str                          # e.g., 'FPH', 'AIA'
    side: str                            # 'BUY' or 'SELL'
    quantity: int
    price: float                         # Limit price in NZD
    order_type: str                      # 'LIMIT' or 'MARKET'
    time_in_force: str                   # 'DAY', 'IOC', 'FOK'

@dataclass
class NZXOrderReport:
    cl_ord_id: str
    symbol: str
    status: str                          # 'NEW', 'REJECTED', 'FILLED', 'CANCELED'
    fix_msg_type: str                    # 'D' (NewOrderSingle), '8' (ExecutionReport), 'F' (OrderCancelRequest)
    fix_raw_payload: str
    audit_notes: str

class NewZealandExchangeNZXEngine:
    """
    New Zealand Exchange (NZX) FIX 4.4 trading engine enforcing NZX tick size schedules,
    FIX order lifecycle management (NewOrderSingle 'D', ExecutionReport '8', OrderCancelRequest 'F'), and order validation.
    """
    SENDER_COMP_ID = "QUANT_FIRM_NZ"
    TARGET_COMP_ID = "NZX_TRADING"

    def __init__(self):
        pass

    def get_nzx_tick_size(self, price: float) -> float:
        """
        Returns minimum price step (tick size) based on NZX equities schedule:
        Price < 0.20 NZD -> 0.001 NZD
        Price 0.20 - 1.995 NZD -> 0.005 NZD
        Price >= 2.00 NZD -> 0.01 NZD
        """
        if price < 0.20:
            return 0.001
        elif price < 2.00:
            return 0.005
        else:
            return 0.01

    def validate_price_tick(self, price: float) -> Tuple[bool, float]:
        """
        Validates if price complies with NZX tick size increments.
        """
        tick_size = self.get_nzx_tick_size(price)
        # Round to 6 decimal places to avoid floating point precision noise
        remainder = round(price % tick_size, 6)
        is_valid = remainder == 0.0 or abs(remainder - tick_size) < 1e-6
        return is_valid, tick_size

    def build_fix_new_order_single(self, order: NZXOrderRequest) -> NZXOrderReport:
        """
        Validates order price tick and builds a valid FIX 4.4 NewOrderSingle (35=D) message string.
        """
        # Validate order type and tick size
        if order.order_type.upper() == "LIMIT":
            is_valid_tick, req_step = self.validate_price_tick(order.price)
            if not is_valid_tick:
                reject_notes = (
                    f"NZX REJECT: Price {order.price:.4f} NZD violates tick size schedule. "
                    f"Required minimum tick step for price level = {req_step} NZD."
                )
                logger.error(reject_notes)
                return NZXOrderReport(
                    cl_ord_id=order.cl_ord_id,
                    symbol=order.symbol,
                    status="REJECTED",
                    fix_msg_type="8",
                    fix_raw_payload="",
                    audit_notes=reject_notes
                )

        side_fix = "1" if order.side.upper() == "BUY" else "2"
        ord_type_fix = "2" if order.order_type.upper() == "LIMIT" else "1"

        tif_map = {"DAY": "0", "IOC": "3", "FOK": "4"}
        tif_fix = tif_map.get(order.time_in_force.upper(), "0")

        # Build FIX Tag=Value string (SOH delimiter replaced with '|')
        fix_tags = [
            "8=FIX.4.4",
            "35=D",
            f"49={self.SENDER_COMP_ID}",
            f"56={self.TARGET_COMP_ID}",
            f"11={order.cl_ord_id}",
            f"55={order.symbol.upper()}",
            f"54={side_fix}",
            f"38={order.quantity}",
            f"44={order.price:.4f}",
            f"40={ord_type_fix}",
            f"59={tif_fix}",
            "15=NZD",                           # NZD Settlement Currency Tag
            f"60={int(time.time() * 1000)}"
        ]
        raw_payload = "|".join(fix_tags) + "|"

        notes = f"NZX FIX NewOrderSingle built for {order.symbol}: {order.quantity} shares @ {order.price:.4f} NZD."
        logger.info(notes)

        return NZXOrderReport(
            cl_ord_id=order.cl_ord_id,
            symbol=order.symbol,
            status="NEW",
            fix_msg_type="D",
            fix_raw_payload=raw_payload,
            audit_notes=notes
        )
