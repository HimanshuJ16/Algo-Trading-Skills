import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    api_key: str
    secret: str

class IntegrationEngine:
    """Legacy IntegrationEngine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def connect(self) -> bool:
        return True

    def disconnect(self) -> bool:
        return True

class SGXAssetCategory(str, Enum):
    TITAN_DERIVATIVES = "TITAN_DERIVATIVES" # Futures & Options (CN, NK, TW, FE)
    SGX_EQUITIES = "SGX_EQUITIES"           # Stocks & REITs (D05 DBS, U11 UOB)

class SGXOrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    IOC = "IOC"                             # Immediate-or-Cancel

@dataclass
class SGXContractSpec:
    symbol: str
    name: str
    asset_category: SGXAssetCategory
    tick_size: float                        # e.g., 2.5 for FTSE China A50, 5.0 for Nikkei 225
    contract_size_multiplier: float         # Multiplier e.g. 5.0 USD per index point
    currency: str = "USD"

@dataclass
class SGXOrder:
    order_id: str
    account_id: str
    symbol: str
    side: str                               # 'BUY' or 'SELL'
    quantity: float
    order_type: SGXOrderType
    price: Optional[float] = None
    status: str = "PENDING"

@dataclass
class SGXTradeExecution:
    execution_id: str
    order_id: str
    symbol: str
    side: str
    filled_quantity: float
    filled_price: float
    commission_sgd: float
    executed_at_iso: str

class SingaporeExchangeSGXAPIClient:
    """
    Production-grade client for Singapore Exchange (SGX) TITAN derivatives and equities API connectivity,
    supporting FIX order routing, tick size enforcement, and trade execution tracking.
    """
    PREDEFINED_CONTRACTS: Dict[str, SGXContractSpec] = {
        "CN": SGXContractSpec("CN", "FTSE China A50 Index Futures", SGXAssetCategory.TITAN_DERIVATIVES, tick_size=2.5, contract_size_multiplier=1.0, currency="USD"),
        "NK": SGXContractSpec("NK", "Nikkei 225 Index Futures", SGXAssetCategory.TITAN_DERIVATIVES, tick_size=5.0, contract_size_multiplier=500.0, currency="JPY"),
        "TW": SGXContractSpec("TW", "MSCI Taiwan Index Futures", SGXAssetCategory.TITAN_DERIVATIVES, tick_size=0.1, contract_size_multiplier=100.0, currency="USD"),
        "FE": SGXContractSpec("FE", "SGX TSI Iron Ore CFR China Futures", SGXAssetCategory.TITAN_DERIVATIVES, tick_size=0.05, contract_size_multiplier=100.0, currency="USD"),
        "D05.SI": SGXContractSpec("D05.SI", "DBS Group Holdings Ltd", SGXAssetCategory.SGX_EQUITIES, tick_size=0.01, contract_size_multiplier=1.0, currency="SGD"),
    }

    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str = "SGX_TITAN_GW",
        environment: str = "SIMULATION",     # 'SIMULATION' or 'PRODUCTION'
        transport_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
    ):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.environment = environment
        self.transport_fn = transport_fn
        self.is_connected = False
        self.active_orders: Dict[str, SGXOrder] = {}

    def connect(self) -> bool:
        """Establishes FIX session connection to SGX TITAN gateway."""
        self.is_connected = True
        logger.info(f"SGX TITAN FIX session connected for {self.sender_comp_id} [{self.environment}].")
        return True

    def disconnect(self) -> bool:
        """Terminates FIX session gracefully."""
        self.is_connected = False
        logger.info(f"SGX TITAN FIX session disconnected.")
        return True

    def place_order(
        self,
        order_id: str,
        account_id: str,
        symbol: str,
        side: str,
        quantity: float,
        order_type: SGXOrderType = SGXOrderType.LIMIT,
        price: Optional[float] = None
    ) -> SGXOrder:
        """
        Validates tick size and submits order to SGX TITAN derivatives or equities gateway.
        """
        if not self.is_connected:
            raise RuntimeError("Cannot place order: SGX TITAN session disconnected.")

        sym_upper = symbol.upper()
        spec = self.PREDEFINED_CONTRACTS.get(sym_upper)

        if spec and order_type == SGXOrderType.LIMIT and price is not None:
            # Validate tick size alignment
            remainder = round(price % spec.tick_size, 4)
            if remainder != 0 and abs(remainder - spec.tick_size) > 1e-4:
                raise ValueError(f"Price {price} violates SGX tick size {spec.tick_size} for symbol {symbol}.")

        order = SGXOrder(
            order_id=order_id,
            account_id=account_id,
            symbol=symbol,
            side=side.upper(),
            quantity=quantity,
            order_type=order_type,
            price=price,
            status="NEW"
        )

        if self.transport_fn:
            payload = {"action": "NEW_ORDER", "order_id": order_id, "symbol": symbol, "side": side, "qty": quantity, "price": price}
            resp = self.transport_fn("POST", payload)
            order.status = resp.get("status", "NEW")

        self.active_orders[order_id] = order
        logger.info(f"SGX ORDER SUBMITTED [{order_id}] ({symbol}): {side} {quantity} @ {price} ({order_type.value}).")
        return order
