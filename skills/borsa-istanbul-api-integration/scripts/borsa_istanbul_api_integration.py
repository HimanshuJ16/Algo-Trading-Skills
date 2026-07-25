import logging
import uuid
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from enum import Enum

logger = logging.getLogger(__name__)

class OrderType(Enum):
    MARKET = "1"
    LIMIT = "2"

class OrderSide(Enum):
    BUY = "1"
    SELL = "2"

class OrderStatus(Enum):
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    REJECTED = "8"

@dataclass
class BISTConfig:
    sender_comp_id: str
    target_comp_id: str
    host: str
    port: int
    heartbeat_interval: int = 30
    protocol_version: str = "FIX.5.0SP2"

@dataclass
class FIXOrder:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    average_price: float = 0.0
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)

class BISTIntegrationEngine:
    """
    Institutional grade FIX integration engine for Borsa Istanbul (BISTECH).
    Handles session management and order routing simulation over FIX 5.0 SP2.
    """
    def __init__(self, config: BISTConfig):
        self.config = config
        self.is_connected = False
        self.orders: Dict[str, FIXOrder] = {}
        self.session_seq_num = 1
        logger.info(f"Initialized BIST FIX Engine for {config.sender_comp_id} targeting {config.target_comp_id}")

    def connect(self) -> bool:
        """Simulates establishing a FIX session with BISTECH."""
        logger.info(f"Connecting to BISTECH FIX Gateway at {self.config.host}:{self.config.port}...")
        if not self.config.host or not self.config.port:
            raise ValueError("Invalid host or port configuration")
        self.is_connected = True
        logger.info("FIX Session established. Logon (MsgType=A) successful.")
        return True

    def disconnect(self) -> bool:
        """Simulates graceful logout sequence."""
        if self.is_connected:
            logger.info("Sending Logout (MsgType=5)...")
            self.is_connected = False
            return True
        return False

    def submit_order(self, order: FIXOrder) -> str:
        """Submits a NewOrderSingle (MsgType=D) to BISTECH."""
        if not self.is_connected:
            raise ConnectionError("FIX Session is not established. Cannot submit order.")
        
        if order.order_type == OrderType.LIMIT and order.price is None:
            raise ValueError("Limit orders must specify a price.")
        
        if order.quantity <= 0:
            raise ValueError("Order quantity must be positive.")
            
        self.orders[order.client_order_id] = order
        logger.info(f"Submitted {order.side.name} order for {order.quantity} {order.symbol} @ {order.price or 'MKT'}")
        self.session_seq_num += 1
        
        return order.client_order_id

    def cancel_order(self, client_order_id: str) -> bool:
        """Submits an OrderCancelRequest (MsgType=F)."""
        if not self.is_connected:
            raise ConnectionError("FIX Session is not established.")
            
        if client_order_id not in self.orders:
            logger.warning(f"Order {client_order_id} not found.")
            return False
            
        order = self.orders[client_order_id]
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED]:
            logger.warning(f"Cannot cancel order {client_order_id} in terminal state {order.status.name}")
            return False
            
        order.status = OrderStatus.CANCELED
        self.session_seq_num += 1
        logger.info(f"Order {client_order_id} canceled successfully.")
        return True

    def simulate_execution_report(self, client_order_id: str, filled_qty: float, exec_price: float) -> Optional[FIXOrder]:
        """Simulates receiving an ExecutionReport (MsgType=8) from the exchange."""
        if client_order_id not in self.orders:
            return None
            
        order = self.orders[client_order_id]
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED]:
            return order
            
        order.filled_quantity += filled_qty
        
        total_value = (order.average_price * (order.filled_quantity - filled_qty)) + (exec_price * filled_qty)
        order.average_price = total_value / order.filled_quantity if order.filled_quantity > 0 else 0
        
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
            
        logger.info(f"Execution Report: {filled_qty} filled @ {exec_price}. Status: {order.status.name}")
        return order
