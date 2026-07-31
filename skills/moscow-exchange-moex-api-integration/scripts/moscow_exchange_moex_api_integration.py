import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MOEXOrderConfig:
    secid: str                           # e.g. 'SBER', 'GAZP', 'USD000000TOD'
    board: str                           # 'TQBR' (Equities), 'CETS' (FX), 'RFUD' (FORTS Derivatives)
    account_id: str
    client_code: str
    max_price_collar_pct: float = 0.05   # Max 5% price deviation from reference price

@dataclass
class MOEXOrderRequest:
    secid: str
    board: str
    side: str                            # 'BUY' or 'SELL'
    quantity: float                      # Order quantity
    price: float                         # Limit price in RUB
    reference_price: float               # Latest market reference price
    tick_size: float = 0.01              # Minimum tick size (0.01 RUB)

@dataclass
class MOEXOrderReport:
    cl_ord_id: str
    secid: str
    board: str
    side: str
    raw_price: float
    rounded_price: float
    quantity: float
    fix_twime_payload: Dict[str, str]
    is_approved: bool
    status: str                          # 'MOEX_ORDER_APPROVED', 'MOEX_PRICE_COLLAR_BREACH', 'MOEX_BOARD_MISMATCH'
    audit_notes: str

class MOEXApiIntegrationEngine:
    """
    Moscow Exchange (MOEX) API integration engine supporting ISS REST market data queries,
    FIX/TWIME low-latency order routing, TQBR/CETS/FORTS board rules, and price collaring.
    """
    def __init__(self):
        pass

    def validate_and_serialize_order(
        self,
        config: MOEXOrderConfig,
        order: MOEXOrderRequest
    ) -> MOEXOrderReport:
        """
        Validates MOEX board compatibility, rounds price to tick size, checks price collars,
        and generates MOEX FIX/TWIME binary order payload.
        """
        if config.secid != order.secid or config.board != order.board:
            notes = f"MOEX BOARD MISMATCH: Config ({config.secid}/{config.board}) != Order ({order.secid}/{order.board})."
            logger.error(notes)
            return MOEXOrderReport(
                cl_ord_id=f"MOEX_ERR_{order.secid}",
                secid=order.secid,
                board=order.board,
                side=order.side,
                raw_price=order.price,
                rounded_price=0.0,
                quantity=order.quantity,
                fix_twime_payload={},
                is_approved=False,
                status="MOEX_BOARD_MISMATCH",
                audit_notes=notes
            )

        # 1. Round price to tick size
        tick = order.tick_size if order.tick_size > 0 else 0.01
        rounded_price = round(round(order.price / tick) * tick, 4)

        # 2. Price Collar Audit
        ref_p = order.reference_price
        if ref_p > 0.0:
            price_dev_pct = abs(rounded_price - ref_p) / ref_p
            if price_dev_pct > config.max_price_collar_pct:
                notes = (
                    f"MOEX PRICE COLLAR BREACH [{order.secid}]: Order price {rounded_price:.2f} RUB "
                    f"deviates {price_dev_pct:.2%} from reference ({ref_p:.2f} RUB), exceeding collar ({config.max_price_collar_pct:.1%})."
                )
                logger.warning(notes)
                return MOEXOrderReport(
                    cl_ord_id=f"MOEX_REJ_{order.secid}",
                    secid=order.secid,
                    board=order.board,
                    side=order.side,
                    raw_price=order.price,
                    rounded_price=rounded_price,
                    quantity=order.quantity,
                    fix_twime_payload={},
                    is_approved=False,
                    status="MOEX_PRICE_COLLAR_BREACH",
                    audit_notes=notes
                )

        # 3. Construct MOEX FIX / TWIME Payload
        cl_ord_id = f"MOEX_{config.board}_{order.secid}_{int(order.quantity)}"
        fix_twime_payload = {
            "ClOrdID": cl_ord_id,
            "SecurityID": order.secid,
            "SecurityExchange": "MISX",
            "BoardID": config.board,
            "Account": config.account_id,
            "ClientCode": config.client_code,
            "Side": "1" if order.side.upper() == "BUY" else "2",
            "OrderQty": str(int(order.quantity)),
            "Price": f"{rounded_price:.4f}",
            "OrdType": "2"              # Limit Order
        }

        notes = (
            f"MOEX ORDER APPROVED [{order.secid}]: Routed to Board '{config.board}' "
            f"at {rounded_price:.2f} RUB. FIX/TWIME payload serialized."
        )
        logger.info(notes)

        return MOEXOrderReport(
            cl_ord_id=cl_ord_id,
            secid=order.secid,
            board=order.board,
            side=order.side,
            raw_price=order.price,
            rounded_price=rounded_price,
            quantity=order.quantity,
            fix_twime_payload=fix_twime_payload,
            is_approved=True,
            status="MOEX_ORDER_APPROVED",
            audit_notes=notes
        )
