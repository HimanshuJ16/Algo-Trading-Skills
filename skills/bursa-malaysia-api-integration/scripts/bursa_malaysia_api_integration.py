"""
bursa-malaysia-api-integration:
Order-lifecycle state machine for Bursa Malaysia BTS2 (Bursa Trade Securities 2)
order entry over FIXT.1.1 / FIX 5.0 SP1 semantics.

**Scope boundary — read before using this in anything live.** This module is an
in-memory order-state machine and a *simulation* of the session layer. It does
NOT open sockets, encode or decode FIX messages, manage FIX sequence numbers,
authenticate against BTS2, or persist anything across restarts. ``connect()``
flips a flag; ``submit_order()`` records an order in a dict. Use it to model and
test the order lifecycle and the BTS2-specific pre-send validations, and to drive
state transitions from reports your real FIX engine decodes. Do not mistake it
for a gateway.

What it does own — the parts BTS2 integrations get wrong:

  1. **BTS2 does not enforce ClOrdID uniqueness; you must.** The Order Management
     specification states BTS2 "will not check for uniqueness of ClOrdId(11)" and
     that when an action is requested on a duplicated ClOrdID "only the last order
     identified by ClOrderId is affected". A duplicate is therefore refused here
     rather than silently overwriting a working order's fill state. ClOrdID is
     also String(20) on the wire, so over-long identifiers (a bare ``uuid4()``
     string is 36 characters) are rejected before they reach the gateway.
  2. **A cancel request is a request, not a cancellation.** BTS2 accepts an Order
     Cancel Request (MsgType=F) only "if the order can successfully be withdrawn
     from the Exchange without executing", and may answer with an Order Cancel
     Reject (MsgType=9). ``cancel_order()`` moves the order to ``PENDING_CANCEL``
     and waits for ``confirm_cancel()`` or ``reject_cancel()``. The cancel request
     carries its own ClOrdID, which the specification requires to be unique
     amongst order ClOrdIDs.
  3. **Execution reports are replayed.** After a sequence gap the counterparty
     resends application messages; a resent ExecutionReport differs from a new one
     only by ExecID (tag 17). Passing ``exec_id`` makes fill application
     idempotent; without it a resend double-counts the fill.
  4. **Overfills are rejected, not absorbed.** Cumulative filled quantity may never
     exceed order quantity; a report that would overfill is refused and logged at
     ERROR rather than corrupting the position and average price.
  5. **The connection type constrains what you may send.** FIXTRADER and
     FIXNEGDEAL are BTS2 *FIX connection types*, each issued with broker codes in
     a distinct format — not TargetCompID values. Routing a Normal-board order
     down a FIXNEGDEAL connection, or quoting a market-maker broker code on the
     Odd-Lot board, is caught here instead of at the exchange.

See ``references/standards.md`` for the protocol, symbology and broker-code
evidence and ``references/workflows.md`` for the full session and order-routing
sequence.
"""
import datetime
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# BTS2 session-layer constants, from the BTS2 FIX Specification: Order Management.
# BeginString is FIXT.1.1 — the transport version. The *application* version is
# carried separately in DefaultApplVerID(1137)/ApplVerID(1128), whose only valid
# value is '8' = FIX50SP1. Configuring a FIX engine with BeginString=FIX.5.0SP1
# is a logon failure, not a version mismatch you discover later.
BEGIN_STRING = "FIXT.1.1"
DEFAULT_APPL_VER_ID = "8"

# ClOrdID(11) is String(20) on New Order Single, Order Cancel Request and Order
# Cancel/Replace Request.
MAX_CLORDID_LENGTH = 20
# SenderCompID(49) and Username(553) are capped at 30 characters; the session
# password at 12 when sent in plain text (BTS2 FIX does not encrypt).
MAX_COMP_ID_LENGTH = 30
MAX_USERNAME_LENGTH = 30
MAX_PASSWORD_LENGTH = 12

# "The FIX gateway accepts HeartBtInt(108) range from 10 to 60. If client
# HeartBtInt is out of this range, the server will reply with the last valid
# value, or the default value (60) if it is the first logon of the day." The
# silent substitution is the danger: your engine's staleness detection then runs
# on a different interval than the venue's.
MIN_HEARTBEAT_SECONDS = 10
MAX_HEARTBEAT_SECONDS = 60

# "If the session initiator fails to authenticate with the BTS2 system within a
# defined number of attempts [default is 3 times], the account will be locked."
# Unlocking requires marketplace operations to reset the account and issue a new
# password, so an automatic reconnect loop costs a trading session.
MAX_LOGON_ATTEMPTS = 3

# Account(1) is the 9-digit CDS account, left-padded with "0" when required.
CDS_ACCOUNT_LENGTH = 9

# OrderRestrictions(529) is required on New Order Single and capped at 5
# characters; multiple restrictions are separated by a space. 'E' (Algorithmic)
# is the tag that declares an order as algorithm-generated.
VALID_ORDER_RESTRICTIONS: FrozenSet[str] = frozenset({
    "9",  # ASEAN Link
    "E",  # Algorithmic
    "I",  # Internet            (Bursa extension)
    "M",  # DMA                 (Bursa extension)
    "R",  # Broker Assisted     (Bursa extension)
})
MAX_ORDER_RESTRICTIONS_LENGTH = 5

# Cumulative quantities accumulate as floats across partial fills, so an exact
# == against order quantity can miss by one ulp and strand a fully-filled order
# in PARTIALLY_FILLED. Bursa equities trade in whole shares; this tolerance is
# far below one share.
_QTY_TOLERANCE = 1e-9

_BROKER_CODE_RE = re.compile(r"^\d{6}$")
_CDS_ACCOUNT_RE = re.compile(r"^\d{1,9}$")


class Environment(Enum):
    """
    Which BTS2 platform the session targets.

    The broker-code branch formats below are documented as applying to the BTS2
    **Production** platform only — the Certification (UAT) platform issues its
    own codes. Enforcing production formats against a UAT code would block valid
    testing, so the branch-digit rule is environment-scoped.
    """
    CERTIFICATION = "CERT"
    PRODUCTION = "PROD"


class ConnectionType(Enum):
    """
    BTS2 FIX connection type.

    This is *not* TargetCompID. It is the type of FIX connection Bursa issues to
    a Participating Organisation, and it determines which boards the session may
    trade and which broker-code format its orders must carry.
    """
    FIXTRADER = "FIXTRADER"
    FIXNEGDEAL = "FIXNEGDEAL"


class Board(Enum):
    """
    SecuritySubType (tag 762) — the board on which the SecurityID is listed.

    These are the values the Order Management specification enumerates for order
    entry. Direct Business Transactions carry no documented value here; see
    ``ConnectionType.FIXNEGDEAL`` and ``submit_order``.
    """
    NORMAL = "NM"
    ODD_LOT = "OD"
    BUY_IN = "BI"


# Boards each connection type may send New Order Single messages for. FIXNEGDEAL
# is deliberately empty: it carries Direct Business Transaction / off-market
# business, whose entry path this module does not model, and guessing a
# SecuritySubType for it would be inventing a wire value.
CONNECTION_BOARDS: Dict[ConnectionType, FrozenSet[Board]] = {
    ConnectionType.FIXTRADER: frozenset({Board.NORMAL, Board.ODD_LOT, Board.BUY_IN}),
    ConnectionType.FIXNEGDEAL: frozenset(),
}

# Broker Code (6 digits) = Firm Code (3 digits) + Branch Code (3 digits). The
# first digit of the branch code identifies what the order is: '9' ordinary
# orders on the Normal/Odd-Lot/Buy-In boards, '1' market-maker orders on the
# Normal board, '2' Direct Business Transactions. Codes '9' and '1' are issued
# with the FIXTRADER connection, '2' with FIXNEGDEAL.
BRANCH_DIGIT_ORDINARY = "9"
BRANCH_DIGIT_MARKET_MAKER = "1"
BRANCH_DIGIT_DBT = "2"

CONNECTION_BRANCH_DIGITS: Dict[ConnectionType, FrozenSet[str]] = {
    ConnectionType.FIXTRADER: frozenset({BRANCH_DIGIT_ORDINARY, BRANCH_DIGIT_MARKET_MAKER}),
    ConnectionType.FIXNEGDEAL: frozenset({BRANCH_DIGIT_DBT}),
}


class OrderType(Enum):
    """
    FIX OrdType (tag 40), as enumerated for BTS2 order entry.

    ``MARKET_AT_BEST`` is a Bursa-defined value. ``STOP`` and ``STOP_LIMIT`` are
    triggered types: they carry TriggerPrice (1102), and STOP_LIMIT additionally
    carries the limit Price (44).
    """
    MARKET = "1"
    LIMIT = "2"
    STOP = "3"
    STOP_LIMIT = "4"
    MARKET_AT_BEST = "Z"


# Types that must carry Price(44), and types that must carry TriggerPrice(1102).
_PRICED_ORDER_TYPES = frozenset({OrderType.LIMIT, OrderType.STOP_LIMIT})
_TRIGGERED_ORDER_TYPES = frozenset({OrderType.STOP, OrderType.STOP_LIMIT})


class OrderSide(Enum):
    """
    FIX Side (tag 54), as enumerated for BTS2.

    The short-sell values are not decoration: Bursa distinguishes Regulated Short
    Sell, Intraday Short Sell and Permitted Short Sell from an ordinary SELL, and
    a short sale sent as ``SELL`` misdeclares the trade to the exchange. Which of
    them your account may use is an entitlement question this module cannot
    answer — confirm before routing.
    """
    BUY = "1"
    SELL = "2"
    REGULATED_SHORT_SELL = "5"
    PROPRIETARY_DAY_TRADING = "6"
    INTRADAY_SHORT_SELL = "I"
    PERMITTED_SHORT_SELL = "V"


SHORT_SELL_SIDES = frozenset({
    OrderSide.REGULATED_SHORT_SELL,
    OrderSide.INTRADAY_SHORT_SELL,
    OrderSide.PERMITTED_SHORT_SELL,
})


class TimeInForce(Enum):
    """
    FIX TimeInForce (tag 59) values BTS2 accepts inbound.

    Absence of the field means Day. Value 'S' (Session) exists but is documented
    as outbound-only — the marketplace sets it in response to 59=2 or 59=7 — so it
    is deliberately absent here.
    """
    DAY = "0"
    GOOD_TILL_CANCEL = "1"
    AT_THE_OPENING = "2"
    IMMEDIATE_OR_CANCEL = "3"
    FILL_OR_KILL = "4"
    GOOD_TILL_DATE = "6"
    AT_THE_CLOSE = "7"


class OrderCapacity(Enum):
    """FIX OrderCapacity (tag 528)."""
    AGENCY = "A"
    PRINCIPAL = "P"
    MARKET_MAKER = "M"
    RISKLESS_PRINCIPAL = "R"


class OrderStatus(Enum):
    """
    Local order state, aligned to FIX OrdStatus (tag 39) where BTS2 publishes one.

    ``PENDING_CANCEL`` is the exception and is a *local* state: BTS2's OrdStatus
    enumeration has no Pending Cancel value, but ExecType(150)=6 is Pending
    Cancel, and the interval between sending an Order Cancel Request and the
    venue answering is exactly the window in which an order is most often
    mishandled. It means "cancel requested, order still live at the venue".
    """
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    PENDING_CANCEL = "6"
    REJECTED = "8"
    EXPIRED = "C"


# Once here the order is done and no report may change it further.
TERMINAL_STATUSES = frozenset({
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
})

# States from which a cancel request may be sent. PENDING_CANCEL is excluded:
# one outstanding cancel request per order.
CANCELABLE_STATUSES = frozenset({OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED})

# States in which the order is still working at the venue and may still fill.
# PENDING_CANCEL is deliberately included — that is the whole point.
WORKING_STATUSES = frozenset({
    OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
})


def new_client_order_id() -> str:
    """
    Generates a ClOrdID that fits BTS2's String(20) limit.

    ``str(uuid.uuid4())`` is 36 characters and does not. 20 hex characters carry
    80 bits of entropy, ample for uniqueness across a trading day — but BTS2
    checks nothing, so if you generate IDs some other way, guaranteeing
    day-uniqueness remains your responsibility.
    """
    return uuid.uuid4().hex[:MAX_CLORDID_LENGTH]


def _validate_cl_ord_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}")
    if len(value) > MAX_CLORDID_LENGTH:
        raise ValueError(
            f"{label} {value!r} is {len(value)} characters; BTS2 ClOrdID(11) is "
            f"String({MAX_CLORDID_LENGTH}). Use new_client_order_id()."
        )
    return value


@dataclass
class BursaConfig:
    """
    BTS2 FIX session configuration.

    Validated at construction so a misconfiguration fails at start-up rather than
    on the first order. ``target_comp_id`` is the CompID Bursa assigns for the
    session — it is *not* the connection type; ``connection_type`` carries that.
    """
    sender_comp_id: str
    target_comp_id: str
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    connection_type: ConnectionType = ConnectionType.FIXTRADER
    broker_code: str = ""
    environment: Environment = Environment.CERTIFICATION
    heartbeat_interval: int = 30
    begin_string: str = BEGIN_STRING
    default_appl_ver_id: str = DEFAULT_APPL_VER_ID

    def __post_init__(self) -> None:
        if self.begin_string != BEGIN_STRING:
            raise ValueError(
                f"BTS2 requires BeginString(8)={BEGIN_STRING!r}, got {self.begin_string!r}. "
                f"FIX 5.0 SP1 is the *application* version and travels in "
                f"DefaultApplVerID(1137)/ApplVerID(1128), not BeginString."
            )
        if self.default_appl_ver_id != DEFAULT_APPL_VER_ID:
            raise ValueError(
                f"BTS2 accepts only DefaultApplVerID(1137)={DEFAULT_APPL_VER_ID!r} "
                f"(FIX50SP1), got {self.default_appl_ver_id!r}"
            )

        for name, value, limit in (
            ("sender_comp_id", self.sender_comp_id, MAX_COMP_ID_LENGTH),
            ("target_comp_id", self.target_comp_id, MAX_COMP_ID_LENGTH),
            ("username", self.username, MAX_USERNAME_LENGTH),
            ("password", self.password, MAX_PASSWORD_LENGTH),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if len(value) > limit:
                # Never echo the password itself into an exception or a log line.
                shown = "<redacted>" if name == "password" else repr(value)
                raise ValueError(
                    f"{name} {shown} exceeds the BTS2 maximum of {limit} characters"
                )

        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise ValueError(f"port must be an integer, got {self.port!r}")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"port {self.port} is outside 1-65535")

        if not isinstance(self.heartbeat_interval, int) or isinstance(self.heartbeat_interval, bool):
            raise ValueError(
                f"heartbeat_interval must be an integer, got {self.heartbeat_interval!r}"
            )
        if not MIN_HEARTBEAT_SECONDS <= self.heartbeat_interval <= MAX_HEARTBEAT_SECONDS:
            raise ValueError(
                f"heartbeat_interval {self.heartbeat_interval} is outside the BTS2 accepted "
                f"range {MIN_HEARTBEAT_SECONDS}-{MAX_HEARTBEAT_SECONDS}s. Out-of-range values "
                f"are not rejected by the gateway — it silently answers with the last valid "
                f"value (or 60 on the first logon of the day), leaving your session timers "
                f"disagreeing with the venue's."
            )

        if not isinstance(self.connection_type, ConnectionType):
            raise ValueError(
                f"connection_type must be a ConnectionType, got {self.connection_type!r}"
            )
        if not isinstance(self.environment, Environment):
            raise ValueError(f"environment must be an Environment, got {self.environment!r}")

        self._validate_broker_code()

    def _validate_broker_code(self) -> None:
        """
        Checks the broker code's shape, and on Production its branch digit.

        Broker Code = 3-digit Firm Code + 3-digit Branch Code. The branch code's
        first digit (the 4th of the six) says what kind of order it is, and must
        match the connection type the order goes out on. Those formats are
        documented for the Production platform only.
        """
        if not self.broker_code:
            raise ValueError(
                "broker_code is required: every BTS2 order must be submitted with a broker "
                "code, and its format is tied to the FIX connection type."
            )
        if not _BROKER_CODE_RE.match(self.broker_code):
            raise ValueError(
                f"broker_code {self.broker_code!r} must be 6 digits "
                f"(3-digit firm code + 3-digit branch code)"
            )

        if self.environment is not Environment.PRODUCTION:
            # Certification issues its own codes; the production branch formats
            # do not apply there.
            return

        branch_digit = self.broker_code[3]
        allowed = CONNECTION_BRANCH_DIGITS[self.connection_type]
        if branch_digit not in allowed:
            raise ValueError(
                f"broker_code {self.broker_code!r} has branch digit {branch_digit!r}, which is "
                f"not valid on a {self.connection_type.value} connection (expected one of "
                f"{sorted(allowed)}). '9' = ordinary orders, '1' = market maker, "
                f"'2' = Direct Business Transactions."
            )

    @property
    def is_market_maker_code(self) -> bool:
        """True when the broker code is a market-maker code (branch digit '1')."""
        return bool(self.broker_code) and self.broker_code[3:4] == BRANCH_DIGIT_MARKET_MAKER


@dataclass
class FIXOrder:
    """
    A BTS2 New Order Single (MsgType=D).

    ``security_id`` is SecurityID(48) — the marketplace-assigned stock code (for
    example "1818", "1818WA") sent with SecurityIDSource(22)=99. It is not a name
    ticker; sending "MAYBANK" gets the order rejected as an unknown security.
    """
    security_id: str
    board: Board
    side: OrderSide
    order_type: OrderType
    quantity: float
    account: str
    order_restrictions: str
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    order_capacity: Optional[OrderCapacity] = None
    client_order_id: str = field(default_factory=new_client_order_id)
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    average_price: float = 0.0
    pending_cancel_cl_ord_id: Optional[str] = None
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    @property
    def remaining_quantity(self) -> float:
        """Quantity still working at the venue (FIX LeavesQty). Never negative."""
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def padded_account(self) -> str:
        """Account(1) as BTS2 wants it: 9 digits, left-padded with '0'."""
        return self.account.zfill(CDS_ACCOUNT_LENGTH)

    @property
    def is_short_sell(self) -> bool:
        """True for the RSS / IDSS / PSS short-sell sides."""
        return self.side in SHORT_SELL_SIDES


class BursaMalaysiaFixEngine:
    """
    Order-lifecycle state machine for Bursa Malaysia BTS2 over FIXT.1.1 /
    FIX 5.0 SP1 semantics: pre-send validation, order entry, cancel
    request/confirm/reject, expiry, and idempotent execution-report application.

    Session establishment and message transport are simulated — see the module
    docstring for the scope boundary.
    """

    def __init__(self, config: BursaConfig):
        self.config = config
        self.is_connected = False
        self.orders: Dict[str, FIXOrder] = {}
        # Counter of application messages emitted this session. It is NOT a FIX
        # sequence number: sequence assignment, gap fill, Resend Request handling
        # and cross-restart persistence belong to the FIX engine.
        self.session_seq_num = 1
        self.failed_logon_attempts = 0
        # Every ClOrdID this session has put on the wire — orders and cancel
        # requests alike. BTS2 does not check uniqueness, and the specification
        # requires a cancel request's ClOrdID to be unique amongst order ClOrdIDs.
        self._used_cl_ord_ids: Set[str] = set()
        # Keyed by (ClOrdID, ExecID) rather than ExecID alone, so a caller
        # synthesising ExecIDs per order cannot have a fill on one order suppress
        # a fill on another. Grows with fills; evict alongside completed orders in
        # a long-running process.
        self._applied_exec_ids: Set[Tuple[str, str]] = set()
        logger.info(
            "Initialized BTS2 FIX engine: sender=%s target=%s connection=%s env=%s "
            "broker_code=%s heartbeat=%ss",
            config.sender_comp_id, config.target_comp_id, config.connection_type.value,
            config.environment.value, config.broker_code, config.heartbeat_interval,
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Simulates establishing a FIX session with BTS2 (Logon, MsgType=A).

        Raises:
            ConnectionError: the local logon-attempt budget is exhausted. BTS2
                locks the account after a defined number of failed
                authentications (default 3), and unlocking needs marketplace
                operations to reset it and issue a new password — so this refuses
                to keep trying rather than costing you the trading session.
        """
        if self.failed_logon_attempts >= MAX_LOGON_ATTEMPTS:
            raise ConnectionError(
                f"Refusing to log on: {self.failed_logon_attempts} consecutive failures already "
                f"recorded and BTS2 locks the account at {MAX_LOGON_ATTEMPTS}. Resolve the "
                f"credential problem with Bursa operations before retrying."
            )

        if self.is_connected:
            logger.warning("connect() called while already connected; ignoring.")
            return True

        logger.info(
            "Connecting to BTS2 FIX gateway at %s:%s (BeginString=%s, DefaultApplVerID=%s)...",
            self.config.host, self.config.port,
            self.config.begin_string, self.config.default_appl_ver_id,
        )
        self.is_connected = True
        self.failed_logon_attempts = 0
        logger.info("FIX session established. Logon (MsgType=A) successful.")
        return True

    def record_logon_failure(self, reason: str = "") -> int:
        """
        Records a rejected logon (BTS2 answers a failed logon with a Logout).

        Call this from your FIX engine's logon-failure path. Returns the number of
        consecutive failures; at ``MAX_LOGON_ATTEMPTS`` further ``connect()``
        calls raise instead of burning the attempt that locks the account.
        """
        self.failed_logon_attempts += 1
        self.is_connected = False
        logger.error(
            "BTS2 logon failed (attempt %s of %s): %s",
            self.failed_logon_attempts, MAX_LOGON_ATTEMPTS, reason or "no reason given",
        )
        return self.failed_logon_attempts

    def disconnect(self) -> bool:
        """Simulates the graceful logout sequence (Logout, MsgType=5)."""
        if self.is_connected:
            logger.info("Sending Logout (MsgType=5)...")
            self.is_connected = False
            return True
        return False

    # ------------------------------------------------------------------
    # Order entry
    # ------------------------------------------------------------------

    def submit_order(self, order: FIXOrder) -> str:
        """
        Submits a New Order Single (MsgType=D) to BTS2.

        Every check here mirrors something BTS2 or the Bursa broker-code rules
        require, so a rejection surfaces locally instead of as an exchange reject.

        Raises:
            ConnectionError: no session established.
            ValueError: the order would be rejected on protocol, board,
                broker-code or sanity grounds — including a duplicate ClOrdID.
        """
        if not self.is_connected:
            raise ConnectionError("FIX session is not established. Cannot submit order.")
        if not isinstance(order, FIXOrder):
            raise ValueError(f"order must be a FIXOrder, got {type(order).__name__}")

        _validate_cl_ord_id(order.client_order_id, "client_order_id")
        self._validate_instrument(order)
        self._validate_quantities_and_prices(order)
        self._validate_account_and_tagging(order)

        # A FIXOrder carrying fills or a non-NEW status is a used object being
        # resubmitted; accepting it would import stale fill state into what the
        # venue treats as a brand-new order.
        if order.status is not OrderStatus.NEW or order.filled_quantity != 0:
            raise ValueError(
                f"Order {order.client_order_id} is not in a submittable state "
                f"(status={order.status.name}, filled={order.filled_quantity}). "
                f"Construct a new FIXOrder rather than resubmitting a used one."
            )

        if order.client_order_id in self._used_cl_ord_ids:
            existing = self.orders.get(order.client_order_id)
            state = (
                f" in state {existing.status.name}" if existing
                else " (used by a cancel request)"
            )
            raise ValueError(
                f"Duplicate ClOrdID {order.client_order_id!r}: already used this session{state}. "
                f"BTS2 does not check ClOrdID uniqueness — when an action targets a duplicated "
                f"ClOrdID only the last order is affected, and reusing it here would discard the "
                f"existing order's fill state."
            )

        self.orders[order.client_order_id] = order
        self._used_cl_ord_ids.add(order.client_order_id)
        self.session_seq_num += 1
        logger.info(
            "NewOrderSingle sent: ClOrdID=%s %s %s %s on board %s @ %s (TIF=%s, account=%s, "
            "OrderRestrictions=%s)",
            order.client_order_id, order.side.name, order.quantity, order.security_id,
            order.board.value, order.price if order.price is not None else "MKT",
            order.time_in_force.name, order.padded_account, order.order_restrictions,
        )
        if order.is_short_sell:
            logger.info(
                "ClOrdID=%s is a short sale (Side=%s); confirm the account is entitled to it and "
                "that any exchange short-sell limits still have room.",
                order.client_order_id, order.side.name,
            )
        return order.client_order_id

    def _validate_instrument(self, order: FIXOrder) -> None:
        if not isinstance(order.security_id, str) or not order.security_id.strip():
            raise ValueError(
                "security_id must be a non-empty string: BTS2 identifies instruments by "
                "SecurityID(48), the marketplace-assigned stock code, with "
                "SecurityIDSource(22)=99."
            )
        if not isinstance(order.board, Board):
            raise ValueError(f"board must be a Board, got {order.board!r}")

        allowed_boards = CONNECTION_BOARDS[self.config.connection_type]
        if not allowed_boards:
            raise ValueError(
                f"This engine is configured for a {self.config.connection_type.value} connection, "
                f"which carries Direct Business Transaction / off-market business. Its entry path "
                f"is not modelled here (BTS2 handles privately negotiated trades through Trade "
                f"Capture Reporting), and no SecuritySubType(762) value is documented for it. "
                f"Use a FIXTRADER connection for Normal, Odd-Lot and Buy-In board orders."
            )
        if order.board not in allowed_boards:
            raise ValueError(
                f"Board {order.board.value} is not tradeable on a "
                f"{self.config.connection_type.value} connection "
                f"(allowed: {sorted(b.value for b in allowed_boards)})."
            )
        # Market-maker broker codes are issued for the Normal board only.
        if self.config.is_market_maker_code and order.board is not Board.NORMAL:
            raise ValueError(
                f"Broker code {self.config.broker_code} is a market-maker code (branch digit "
                f"'{BRANCH_DIGIT_MARKET_MAKER}'), which is issued for Normal-board orders; "
                f"this order is on the {order.board.value} board."
            )

    def _validate_quantities_and_prices(self, order: FIXOrder) -> None:
        if not isinstance(order.quantity, (int, float)) or isinstance(order.quantity, bool):
            raise ValueError(f"Order quantity must be a number, got {order.quantity!r}")
        # NaN fails every comparison, so `quantity <= 0` alone lets it through.
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            raise ValueError(
                f"Order quantity must be finite and positive, got {order.quantity!r}"
            )
        if not isinstance(order.order_type, OrderType):
            raise ValueError(f"order_type must be an OrderType, got {order.order_type!r}")
        if not isinstance(order.side, OrderSide):
            raise ValueError(f"side must be an OrderSide, got {order.side!r}")
        if not isinstance(order.time_in_force, TimeInForce):
            raise ValueError(
                f"time_in_force must be a TimeInForce, got {order.time_in_force!r}"
            )
        if order.order_capacity is not None and not isinstance(order.order_capacity, OrderCapacity):
            raise ValueError(
                f"order_capacity must be an OrderCapacity or None, got {order.order_capacity!r}"
            )

        needs_price = order.order_type in _PRICED_ORDER_TYPES
        needs_trigger = order.order_type in _TRIGGERED_ORDER_TYPES

        if needs_price:
            self._require_positive_price(order.price, "Price(44)", order.order_type)
        elif order.price is not None:
            # Price(44) "is not used" on Market and Market at Best. Some gateways
            # reject it, others ignore it and fill you at a price you never meant.
            raise ValueError(
                f"{order.order_type.name} orders must not specify a price; BTS2 does not use "
                f"Price(44) for them."
            )

        if needs_trigger:
            self._require_positive_price(
                order.trigger_price, "TriggerPrice(1102)", order.order_type
            )
        elif order.trigger_price is not None:
            raise ValueError(
                f"{order.order_type.name} orders must not specify a trigger_price; "
                f"TriggerPrice(1102) belongs to Stop and Stop Limit orders."
            )

    @staticmethod
    def _require_positive_price(value: Optional[float], tag: str, order_type: OrderType) -> None:
        if value is None:
            raise ValueError(f"{order_type.name} orders must specify {tag}.")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{tag} must be a number, got {value!r}")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{tag} must be finite and positive, got {value!r}")

    def _validate_account_and_tagging(self, order: FIXOrder) -> None:
        if not isinstance(order.account, str) or not _CDS_ACCOUNT_RE.match(order.account):
            raise ValueError(
                f"account {order.account!r} must be a CDS account of up to "
                f"{CDS_ACCOUNT_LENGTH} digits; Account(1) goes on the wire left-padded to "
                f"{CDS_ACCOUNT_LENGTH} digits."
            )

        restrictions = order.order_restrictions
        if not isinstance(restrictions, str) or not restrictions.strip():
            raise ValueError(
                "order_restrictions is required: OrderRestrictions(529) is a mandatory tagging "
                "field on BTS2 New Order Single. Algorithm-generated orders carry 'E'."
            )
        if len(restrictions) > MAX_ORDER_RESTRICTIONS_LENGTH:
            raise ValueError(
                f"order_restrictions {restrictions!r} is {len(restrictions)} characters; "
                f"OrderRestrictions(529) is capped at {MAX_ORDER_RESTRICTIONS_LENGTH}."
            )
        tokens = restrictions.split(" ")
        unknown = [t for t in tokens if t not in VALID_ORDER_RESTRICTIONS]
        if unknown:
            raise ValueError(
                f"order_restrictions {restrictions!r} contains unsupported value(s) {unknown}; "
                f"valid values are {sorted(VALID_ORDER_RESTRICTIONS)} separated by spaces."
            )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_order(
        self, client_order_id: str, cancel_cl_ord_id: Optional[str] = None
    ) -> bool:
        """
        Sends an Order Cancel Request (MsgType=F) for the remaining quantity.

        This **requests** cancellation; it does not cancel. BTS2 accepts the
        request only if the order can be withdrawn without executing, so the order
        stays live and can still fill until the venue answers: call
        ``confirm_cancel()`` on an ExecutionReport with ExecType=Canceled, or
        ``reject_cancel()`` on an Order Cancel Reject (MsgType=9).

        Args:
            client_order_id: ClOrdID of the order to cancel (sent as OrigClOrdID).
            cancel_cl_ord_id: ClOrdID for the cancel request itself. BTS2 requires
                it to be unique amongst order ClOrdIDs; one is generated if not
                supplied.

        Returns:
            True if the request was sent; False if the order is unknown, already
            terminal, or already has a cancel request outstanding.

        Raises:
            ValueError: ``cancel_cl_ord_id`` is malformed or already used.
        """
        if not self.is_connected:
            raise ConnectionError("FIX session is not established.")

        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning("Cancel requested for unknown order %s.", client_order_id)
            return False

        if order.status is OrderStatus.PENDING_CANCEL:
            logger.warning(
                "Order %s already has an outstanding cancel request; not sending a duplicate.",
                client_order_id,
            )
            return False
        if order.status not in CANCELABLE_STATUSES:
            logger.warning(
                "Cannot cancel order %s in terminal state %s.",
                client_order_id, order.status.name,
            )
            return False

        if cancel_cl_ord_id is None:
            cancel_cl_ord_id = new_client_order_id()
        else:
            _validate_cl_ord_id(cancel_cl_ord_id, "cancel_cl_ord_id")
        if cancel_cl_ord_id in self._used_cl_ord_ids:
            raise ValueError(
                f"Cancel request ClOrdID {cancel_cl_ord_id!r} is already in use this session. "
                f"BTS2 requires the cancel request's ClOrdID to be unique amongst those assigned "
                f"to orders and replacement orders."
            )

        self._used_cl_ord_ids.add(cancel_cl_ord_id)
        order.pending_cancel_cl_ord_id = cancel_cl_ord_id
        order.status = OrderStatus.PENDING_CANCEL
        self.session_seq_num += 1
        logger.info(
            "OrderCancelRequest (MsgType=F) sent: ClOrdID=%s OrigClOrdID=%s. Order remains live "
            "at BTS2 and may still fill until the venue answers.",
            cancel_cl_ord_id, client_order_id,
        )
        return True

    def confirm_cancel(self, client_order_id: str) -> Optional[FIXOrder]:
        """
        Applies a venue cancel confirmation (ExecutionReport, ExecType=Canceled).

        Accepts confirmation both for an order in ``PENDING_CANCEL`` and for one
        still working: BTS2 issues unsolicited cancels — a supervisor cancelling
        the order, a cancel entered through the native protocol, or a
        market-control action — and the FIX client must handle them.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning("Cancel confirmation for unknown order %s.", client_order_id)
            return None

        if order.status in TERMINAL_STATUSES:
            logger.warning(
                "Cancel confirmation for %s already in terminal state %s; ignoring.",
                client_order_id, order.status.name,
            )
            return order

        was_pending = order.status is OrderStatus.PENDING_CANCEL
        order.status = OrderStatus.CANCELED
        order.pending_cancel_cl_ord_id = None
        logger.info(
            "Order %s CANCELED at BTS2%s; %s of %s filled.",
            client_order_id, "" if was_pending else " (unsolicited)",
            order.filled_quantity, order.quantity,
        )
        return order

    def reject_cancel(self, client_order_id: str, reason: str = "") -> Optional[FIXOrder]:
        """
        Applies an Order Cancel Reject (MsgType=9): BTS2 refused the cancel.

        The order was never cancelled and is still working, so it returns to the
        status its fill state implies. BTS2 returns CxlRejReason(102)=99 (Other)
        for most rejections and puts the real reason in Text(58) — log it.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning("Cancel reject for unknown order %s.", client_order_id)
            return None

        if order.status is not OrderStatus.PENDING_CANCEL:
            logger.warning(
                "Cancel reject for %s in state %s (no cancel request outstanding); ignoring. "
                "Reason: %r",
                client_order_id, order.status.name, reason,
            )
            return order

        order.status = (
            OrderStatus.PARTIALLY_FILLED if order.filled_quantity > 0 else OrderStatus.NEW
        )
        order.pending_cancel_cl_ord_id = None
        logger.warning(
            "Cancel REJECTED for %s: %r. Order is still working in state %s with %s remaining.",
            client_order_id, reason, order.status.name, order.remaining_quantity,
        )
        return order

    def expire_order(self, client_order_id: str, reason: str = "") -> Optional[FIXOrder]:
        """
        Applies an expiry (ExecutionReport, ExecType=C / OrdStatus=Expired).

        BTS2 generates these without any request from us: the unfilled remainder
        of an IOC or FoK order, a Good-Till order carried past its date or outside
        a dynamic limit, and other ExecRestatementReason(378) cases. An order
        already in a terminal state is left alone.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning("Expiry for unknown order %s.", client_order_id)
            return None

        if order.status in TERMINAL_STATUSES:
            logger.warning(
                "Expiry for %s already in terminal state %s; ignoring.",
                client_order_id, order.status.name,
            )
            return order

        order.status = OrderStatus.EXPIRED
        order.pending_cancel_cl_ord_id = None
        logger.info(
            "Order %s EXPIRED at BTS2 (%s); %s of %s filled, %s never traded.",
            client_order_id, reason or "no reason given",
            order.filled_quantity, order.quantity, order.remaining_quantity,
        )
        return order

    # ------------------------------------------------------------------
    # Execution reports
    # ------------------------------------------------------------------

    def simulate_execution_report(
        self,
        client_order_id: str,
        filled_qty: float,
        exec_price: float,
        exec_id: Optional[str] = None,
    ) -> Optional[FIXOrder]:
        """
        Applies a fill from an ExecutionReport (MsgType=8, ExecType=F Trade).

        Args:
            client_order_id: ClOrdID(11) the report refers to.
            filled_qty: LastQty(32) — the quantity of *this* fill, not CumQty(14).
                Must be finite and positive; a zero-quantity report is an
                acknowledgement (ExecType=0 New), not a fill.
            exec_price: LastPx(31) for this fill. Must be finite and positive.
            exec_id: ExecID(17). Strongly recommended: after a sequence gap BTS2
                resends application messages, and ExecID is the only thing
                distinguishing a resent report from a new one. Without it a
                resend double-counts the fill.

        Returns:
            The updated order, or None if the ClOrdID is unknown. Orders in a
            terminal state, and duplicate ExecIDs, are returned unchanged.

        A report that would push cumulative filled quantity past the order
        quantity is refused and logged at ERROR: a duplicate escaped
        deduplication or the venue sent something impossible. Alert on it and
        reconcile against BTS2's own trade records before trading on the position.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning("Execution report for unknown order %s.", client_order_id)
            return None

        if not isinstance(filled_qty, (int, float)) or isinstance(filled_qty, bool):
            raise ValueError(f"filled_qty must be a number, got {filled_qty!r}")
        if not math.isfinite(filled_qty) or filled_qty <= 0:
            raise ValueError(f"filled_qty must be finite and positive, got {filled_qty!r}")
        if not isinstance(exec_price, (int, float)) or isinstance(exec_price, bool):
            raise ValueError(f"exec_price must be a number, got {exec_price!r}")
        if not math.isfinite(exec_price) or exec_price <= 0:
            raise ValueError(f"exec_price must be finite and positive, got {exec_price!r}")

        if order.status in TERMINAL_STATUSES:
            logger.warning(
                "Execution report for %s in terminal state %s; ignoring.",
                client_order_id, order.status.name,
            )
            return order

        dedup_key = (client_order_id, exec_id) if exec_id is not None else None
        if dedup_key is not None:
            if dedup_key in self._applied_exec_ids:
                logger.warning(
                    "Duplicate ExecID %r for order %s; already applied, ignoring "
                    "(resend after a sequence gap).",
                    exec_id, client_order_id,
                )
                return order
        else:
            logger.warning(
                "Execution report for %s carries no ExecID; fill application cannot be made "
                "idempotent and a resend will double-count.",
                client_order_id,
            )

        new_filled = order.filled_quantity + filled_qty
        if new_filled > order.quantity + _QTY_TOLERANCE:
            logger.error(
                "REJECTED overfill on %s: fill of %s would take cumulative filled quantity to %s, "
                "exceeding order quantity %s. Duplicate report or venue error — reconcile before "
                "trading on this position.",
                client_order_id, filled_qty, new_filled, order.quantity,
            )
            return order

        previous_filled = order.filled_quantity
        order.filled_quantity = new_filled
        # Quantity-weighted average of the previous average and this fill — the
        # same figure BTS2 reports in AvgPx(6).
        order.average_price = (
            (order.average_price * previous_filled) + (exec_price * filled_qty)
        ) / new_filled

        if dedup_key is not None:
            self._applied_exec_ids.add(dedup_key)

        if new_filled >= order.quantity - _QTY_TOLERANCE:
            order.status = OrderStatus.FILLED
        elif order.status is not OrderStatus.PENDING_CANCEL:
            # A fill does not answer an outstanding cancel request; only the
            # venue's cancel confirmation or reject may leave PENDING_CANCEL.
            order.status = OrderStatus.PARTIALLY_FILLED

        logger.info(
            "ExecutionReport applied: ClOrdID=%s LastQty=%s LastPx=%s -> status=%s, "
            "CumQty=%s/%s, AvgPx=%s",
            client_order_id, filled_qty, exec_price, order.status.name,
            order.filled_quantity, order.quantity, order.average_price,
        )
        return order
