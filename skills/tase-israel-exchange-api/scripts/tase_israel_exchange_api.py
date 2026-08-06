import logging
import uuid
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from enum import Enum

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "1"
    SELL = "2"


class OrderType(Enum):
    MARKET = "1"
    LIMIT = "2"
    STOP_LIMIT = "3"
    ICEBERG = "L"


class OrderStatus(Enum):
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    REJECTED = "8"


class MarketPhase(Enum):
    PRE_OPEN = "1"
    OPENING_AUCTION = "2"
    CONTINUOUS_TRADING = "3"
    CLOSING_AUCTION = "4"
    CLOSED = "5"


class PriceDenomination(Enum):
    AGOROT = "AGOROT"  # 100 Agorot = 1 ILS / NIS (Equities & Mutual Funds)
    ILS = "ILS"        # Israeli New Shekel (NIS)
    PERCENTAGE = "PCT" # Bond & Makam quotation in % of par value


class InstrumentType(Enum):
    EQUITY = "EQUITY"
    BOND = "BOND"
    MAKAM = "MAKAM"  # Israeli Treasury Bills
    DERIVATIVE = "DERIVATIVE"
    ETF = "ETF"


class TASEError(Exception):
    """Base exception class for TASE Integration Engine errors."""
    pass


class TASEConnectionError(TASEError):
    """Raised when FIX connection is not established or fails."""
    pass


class TASEValidationError(TASEError):
    """Raised when order parameters or security specifications fail validation."""
    pass


class TASERiskLimitError(TASEError):
    """Raised when pre-trade risk controls or order limits are breached."""
    pass


@dataclass
class TASEConfig:
    sender_comp_id: str
    target_comp_id: str
    host: str
    port: int
    trader_id: str = "QUAL_TRADER_1"
    heartbeat_interval: int = 30
    protocol_version: str = "FIX.5.0SP2"
    max_order_value_ils: float = 1_000_000.0
    max_order_qty: float = 100_000.0
    max_price_collar_pct: float = 10.0  # Max deviation from reference price (%)
    enforce_sunday_calendar: bool = True


@dataclass
class TASESecurity:
    symbol: str  # e.g., 'TEVA.TA' or 'LUMI.TA'
    security_id: str  # TASE 6 or 7 digit code (e.g., '1082511')
    isin: str  # e.g., 'IL0001082511'
    instrument_type: InstrumentType = InstrumentType.EQUITY
    price_denomination: PriceDenomination = PriceDenomination.AGOROT
    tick_size_agorot: float = 0.1
    reference_price_ils: float = 0.0


@dataclass
class TASEOrder:
    symbol: str
    security_id: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None  # Quoted in Agorot for Equities, ILS/PCT for Bonds
    price_denomination: PriceDenomination = PriceDenomination.AGOROT
    display_qty: Optional[float] = None  # For Iceberg orders
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    average_price: float = 0.0
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class TASEIntegrationEngine:
    """
    Institutional Grade Integration Engine for the Tel Aviv Stock Exchange (TASE).
    
    Provides FIX protocol session simulation, Agorot-ILS currency conversion,
    pre-trade risk validation, Israel trading calendar checks (Sunday-Thursday),
    and order execution lifecycle tracking.
    """
    AGOROT_PER_ILS = 100.0

    def __init__(self, config: TASEConfig):
        self.config = config
        self.is_connected = False
        self.orders: Dict[str, TASEOrder] = {}
        self.securities: Dict[str, TASESecurity] = {}
        self.session_seq_num = 1
        logger.info(
            f"Initialized TASE FIX Engine [Sender: {config.sender_comp_id}, Target: {config.target_comp_id}]"
        )

    def register_security(self, security: TASESecurity) -> None:
        """Register security reference metadata into local master."""
        self.securities[security.symbol] = security
        logger.info(f"Registered TASE Security: {security.symbol} (ID: {security.security_id}, ISIN: {security.isin})")

    @classmethod
    def convert_price_agorot_to_ils(cls, price_agorot: float) -> float:
        """Converts price from Agorot (cents) to Israeli New Shekels (ILS)."""
        return price_agorot / cls.AGOROT_PER_ILS

    @classmethod
    def convert_price_ils_to_agorot(cls, price_ils: float) -> float:
        """Converts price from Israeli New Shekels (ILS) to Agorot (cents)."""
        return price_ils * cls.AGOROT_PER_ILS

    def calculate_order_value_ils(self, order: TASEOrder) -> float:
        """
        Calculates the estimated total order value in ILS based on price denomination.
        """
        price = order.price if order.price is not None else 0.0
        if order.price_denomination == PriceDenomination.AGOROT:
            price_ils = self.convert_price_agorot_to_ils(price)
        else:
            price_ils = price
        return price_ils * order.quantity

    def connect(self) -> bool:
        """Establishes FIX session connection with TASE Gateway."""
        if not self.config.host or not self.config.port:
            raise TASEValidationError("Host and port must be specified in TASEConfig.")
        if not self.config.sender_comp_id or not self.config.target_comp_id:
            raise TASEValidationError("SenderCompID and TargetCompID are required.")

        logger.info(f"Connecting to TASE FIX Gateway at {self.config.host}:{self.config.port}...")
        self.is_connected = True
        logger.info("TASE FIX Session established successfully (Logon MsgType=A acknowledged).")
        return True

    def disconnect(self) -> bool:
        """Terminates FIX session gracefully."""
        if self.is_connected:
            logger.info("Sending FIX Logout (MsgType=5)...")
            self.is_connected = False
            return True
        return False

    def get_market_phase(self, dt: Optional[datetime.datetime] = None) -> MarketPhase:
        """
        Determines TASE market phase based on Israel Standard Time (IST).
        Note: TASE operates Sunday through Thursday (Friday & Saturday closed).
        Standard trading schedule:
        - Sunday: Pre-Open 08:30 IST, Opening Auction ~09:50-10:00 IST, Continuous 10:00-15:50 IST, Closing 15:50-16:00 IST.
        - Mon-Thu: Pre-Open 08:30 IST, Opening Auction ~09:50-10:00 IST, Continuous 10:00-17:15 IST, Closing 17:15-17:25 IST.
        """
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        
        # Approximate Israel Time as UTC+2 (Standard) or UTC+3 (DST)
        # Assuming UTC+2 for baseline estimation
        ist_dt = dt + datetime.timedelta(hours=2)
        weekday = ist_dt.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6

        # Friday and Saturday are weekend days in Israel
        if weekday in (4, 5):
            return MarketPhase.CLOSED

        minute_of_day = ist_dt.hour * 60 + ist_dt.minute

        # Sunday schedule
        if weekday == 6:
            if minute_of_day < 510:  # Before 08:30
                return MarketPhase.CLOSED
            elif minute_of_day < 590:  # 08:30 - 09:50
                return MarketPhase.PRE_OPEN
            elif minute_of_day < 600:  # 09:50 - 10:00
                return MarketPhase.OPENING_AUCTION
            elif minute_of_day < 950:  # 10:00 - 15:50
                return MarketPhase.CONTINUOUS_TRADING
            elif minute_of_day < 960:  # 15:50 - 16:00
                return MarketPhase.CLOSING_AUCTION
            else:
                return MarketPhase.CLOSED
        else:
            # Mon-Thu schedule
            if minute_of_day < 510:  # Before 08:30
                return MarketPhase.CLOSED
            elif minute_of_day < 590:  # 08:30 - 09:50
                return MarketPhase.PRE_OPEN
            elif minute_of_day < 600:  # 09:50 - 10:00
                return MarketPhase.OPENING_AUCTION
            elif minute_of_day < 1035:  # 10:00 - 17:15
                return MarketPhase.CONTINUOUS_TRADING
            elif minute_of_day < 1045:  # 17:15 - 17:25
                return MarketPhase.CLOSING_AUCTION
            else:
                return MarketPhase.CLOSED

    def validate_order(self, order: TASEOrder) -> None:
        """Executes pre-trade risk and parameter validation checks."""
        if order.quantity <= 0:
            raise TASEValidationError(f"Order quantity must be positive, got {order.quantity}")

        if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            if order.price is None or order.price <= 0:
                raise TASEValidationError(f"Limit/Stop orders require positive price, got {order.price}")

        if order.order_type == OrderType.ICEBERG:
            if order.display_qty is None or order.display_qty <= 0:
                raise TASEValidationError("Iceberg orders require positive display_qty.")
            if order.display_qty > order.quantity:
                raise TASEValidationError("Iceberg display_qty cannot exceed total order quantity.")

        # Check maximum order quantity
        if order.quantity > self.config.max_order_qty:
            raise TASERiskLimitError(
                f"Order quantity {order.quantity} exceeds max limit {self.config.max_order_qty}"
            )

        # Check maximum order value in ILS
        order_val_ils = self.calculate_order_value_ils(order)
        if order_val_ils > self.config.max_order_value_ils:
            raise TASERiskLimitError(
                f"Order value {order_val_ils:.2f} ILS exceeds max allowed limit {self.config.max_order_value_ils:.2f} ILS"
            )

        # Check price collar against reference price if security registered
        if order.symbol in self.securities and order.price is not None:
            sec = self.securities[order.symbol]
            if sec.reference_price_ils > 0:
                price_ils = (
                    self.convert_price_agorot_to_ils(order.price)
                    if order.price_denomination == PriceDenomination.AGOROT
                    else order.price
                )
                dev_pct = abs(price_ils - sec.reference_price_ils) / sec.reference_price_ils * 100.0
                if dev_pct > self.config.max_price_collar_pct:
                    raise TASERiskLimitError(
                        f"Order price deviation {dev_pct:.2f}% exceeds price collar limit {self.config.max_price_collar_pct}%"
                    )

    def submit_order(self, order: TASEOrder) -> str:
        """
        Submits a NewOrderSingle (MsgType=D) to the TASE FIX Gateway.
        """
        if not self.is_connected:
            raise TASEConnectionError("Cannot submit order: FIX session is disconnected.")

        self.validate_order(order)

        self.orders[order.client_order_id] = order
        self.session_seq_num += 1

        logger.info(
            f"Submitted TASE Order {order.client_order_id}: {order.side.name} {order.quantity} "
            f"{order.symbol} @ {order.price if order.price else 'MKT'} {order.price_denomination.value}"
        )
        return order.client_order_id

    def cancel_order(self, client_order_id: str) -> bool:
        """
        Submits an OrderCancelRequest (MsgType=F) for an active order.
        """
        if not self.is_connected:
            raise TASEConnectionError("Cannot cancel order: FIX session is disconnected.")

        if client_order_id not in self.orders:
            logger.warning(f"Cancel request failed: Order ID {client_order_id} not found.")
            return False

        order = self.orders[client_order_id]
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
            logger.warning(f"Cannot cancel order {client_order_id}: Already in terminal state {order.status.name}.")
            return False

        order.status = OrderStatus.CANCELED
        self.session_seq_num += 1
        logger.info(f"Order {client_order_id} canceled successfully.")
        return True

    def simulate_execution_report(
        self, client_order_id: str, filled_qty: float, exec_price: float
    ) -> Optional[TASEOrder]:
        """
        Simulates receiving an ExecutionReport (MsgType=8) from TASE matching engine.
        Updates filled quantity, cumulative average price, and order status.
        """
        if client_order_id not in self.orders:
            logger.error(f"Execution Report error: Unknown order ID {client_order_id}")
            return None

        order = self.orders[client_order_id]
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED):
            logger.warning(f"Execution Report ignored for order {client_order_id} in state {order.status.name}")
            return order

        if filled_qty <= 0:
            raise TASEValidationError("Filled quantity must be positive.")

        prev_filled = order.filled_quantity
        new_filled = prev_filled + filled_qty
        if new_filled > order.quantity:
            raise TASEValidationError(
                f"Cumulative filled quantity {new_filled} exceeds order quantity {order.quantity}"
            )

        # Update VWAP
        total_val = (order.average_price * prev_filled) + (exec_price * filled_qty)
        order.average_price = total_val / new_filled
        order.filled_quantity = new_filled

        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        logger.info(
            f"ExecutionReport for Order {client_order_id}: Filled {filled_qty} @ {exec_price}. "
            f"Total Filled: {order.filled_quantity}/{order.quantity}. Status: {order.status.name}"
        )
        return order

