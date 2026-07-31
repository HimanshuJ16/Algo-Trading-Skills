import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    api_key: str
    environment: str = "PRODUCTION"

@dataclass
class PSEOrderRequest:
    symbol: str
    side: str                            # 'BUY', 'SELL'
    price: float
    quantity: int
    prev_close_price: float

@dataclass
class PSEReport:
    symbol: str
    side: str
    price: float
    quantity: int
    required_board_lot: int
    required_tick_size: float
    is_valid_board_lot: bool
    is_valid_tick_size: bool
    is_within_price_band: bool
    status: str                          # 'ORDER_VALID_COMPLIANT', 'INVALID_BOARD_LOT', 'INVALID_TICK_SIZE', 'PRICE_BAND_BREACH'
    audit_notes: str

class IntegrationEngine:
    """
    Legacy integration engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def connect(self) -> bool:
        return True

    def process(self) -> str:
        return "success"

class PhilippineStockExchangeEngine:
    """
    Philippine Stock Exchange (PSE / PSEi) API integration engine validating price-dependent board lot schedules,
    tick size increments, and static 50% price ceiling/floor circuit breakers.
    """

    # Official PSE Board Lot & Tick Size Schedule
    # (max_price, tick_size, min_board_lot)
    PSE_SCHEDULE: List[Tuple[float, float, int]] = [
        (0.0099, 0.0001, 1000000),
        (0.0490, 0.0010, 100000),
        (0.2490, 0.0010, 10000),
        (0.4950, 0.0050, 10000),
        (4.9900, 0.0100, 1000),
        (9.9900, 0.0100, 100),
        (19.9800, 0.0200, 100),
        (49.9500, 0.0500, 100),
        (99.9500, 0.0500, 10),
        (199.9000, 0.1000, 10),
        (499.8000, 0.2000, 10),
        (999.5000, 0.5000, 10),
        (1999.0000, 1.0000, 5),
        (4998.0000, 2.0000, 5),
        (float('inf'), 5.0000, 5),
    ]

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config(api_key="default_pse_key")

    @classmethod
    def get_pse_tier(cls, price: float) -> Tuple[float, int]:
        """Returns (tick_size, min_board_lot) for a given stock price."""
        for max_p, tick, lot in cls.PSE_SCHEDULE:
            if price <= max_p:
                return tick, lot
        return 5.0000, 5

    def validate_pse_order(
        self, order: PSEOrderRequest
    ) -> PSEReport:
        """
        Validates PSE order against board lot schedule, tick increments, and static price bands (+50% / -50%).
        """
        if order.price <= 0 or order.quantity <= 0 or order.prev_close_price <= 0:
            raise ValueError("Price, quantity, and previous close price must be positive.")

        tick_size, min_board_lot = self.get_pse_tier(order.price)

        # 1. Validate Board Lot Divisibility
        is_valid_lot = (order.quantity % min_board_lot) == 0

        # 2. Validate Tick Size Increment
        # Multiply by 10,000 to prevent floating-point modulo precision loss
        price_int = round(order.price * 10000)
        tick_int = round(tick_size * 10000)
        is_valid_tick = (price_int % tick_int) == 0

        # 3. Static Price Band Audit (+50% ceiling, -50% floor)
        price_ceiling = order.prev_close_price * 1.50
        price_floor = order.prev_close_price * 0.50
        is_within_band = (price_floor <= order.price <= price_ceiling)

        if not is_within_band:
            status = "PRICE_BAND_BREACH"
        elif not is_valid_lot:
            status = "INVALID_BOARD_LOT"
        elif not is_valid_tick:
            status = "INVALID_TICK_SIZE"
        else:
            status = "ORDER_VALID_COMPLIANT"

        notes = (
            f"PSE ORDER AUDIT [{order.symbol} {order.side} - {status}]: Price = PHP {order.price:,.4f}, "
            f"Qty = {order.quantity:,} (Req Lot = {min_board_lot:,}, Req Tick = {tick_size}). "
            f"Price Band: Floor = PHP {price_floor:,.2f}, Ceiling = PHP {price_ceiling:,.2f}."
        )

        if status == "ORDER_VALID_COMPLIANT":
            logger.info(notes)
        else:
            logger.warning(f"PSE ORDER REJECTION: {notes}")

        return PSEReport(
            symbol=order.symbol,
            side=order.side.upper(),
            price=order.price,
            quantity=order.quantity,
            required_board_lot=min_board_lot,
            required_tick_size=tick_size,
            is_valid_board_lot=is_valid_lot,
            is_valid_tick_size=is_valid_tick,
            is_within_price_band=is_within_band,
            status=status,
            audit_notes=notes
        )
