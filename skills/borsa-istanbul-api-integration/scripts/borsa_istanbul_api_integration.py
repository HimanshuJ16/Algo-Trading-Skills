"""
borsa-istanbul-api-integration:
Order-lifecycle state machine for Borsa Istanbul (BISTECH) order routing over
FIX 5.0 SP2 semantics.

**Scope boundary — read before using this in anything live.** This module is an
in-memory order-state machine and a *simulation* of the session layer. It does
NOT open sockets, encode or decode FIX messages, manage FIX sequence numbers, or
persist anything across restarts. ``connect()`` flips a flag; ``submit_order()``
records an order in a dict. Use it to model and test the order lifecycle, and to
drive the state transitions from reports your real FIX engine (e.g. QuickFIX)
decodes. Do not mistake it for a gateway.

What it does own — and what makes it more than a dict — is the part integrations
get wrong:

  1. **A cancel request is a request, not a cancellation.** FIX Order Cancel
     Request (MsgType=F) asks the venue to cancel the remaining quantity. The
     order stays live until the venue answers with an ExecutionReport carrying
     ExecType=Canceled, or refuses with an Order Cancel Reject (MsgType=9).
     ``cancel_order()`` therefore moves the order to ``PENDING_CANCEL`` and waits
     for ``confirm_cancel()`` or ``reject_cancel()``. Marking it CANCELED locally
     loses any fill that lands in the race window — the order was still resting
     at the venue the whole time.
  2. **Execution reports are replayed.** After a sequence gap the counterparty
     resends messages, and a resent ExecutionReport is indistinguishable from a
     new one except by its ExecID. Passing ``exec_id`` makes fill application
     idempotent; without it, a resend double-counts the fill.
  3. **Overfills are rejected, not absorbed.** Cumulative filled quantity can
     never exceed order quantity. A report that would overfill signals a
     duplicate that slipped through or a genuine venue error; it is refused and
     logged at ERROR rather than silently corrupting the average price.

See ``references/standards.md`` for BIST protocol and symbology conventions and
``references/workflows.md`` for the full session and order-routing sequence.
"""
import datetime
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Cumulative quantities are accumulated as floats across many partial fills, so
# an exact == comparison against the order quantity can miss by one ulp and leave
# a fully-filled order stuck in PARTIALLY_FILLED. BIST equities trade in integral
# share quantities, where this tolerance is far below one share.
_QTY_TOLERANCE = 1e-9


class OrderType(Enum):
    """FIX OrdType (tag 40) subset used for BIST equity order entry."""
    MARKET = "1"
    LIMIT = "2"


class OrderSide(Enum):
    """FIX Side (tag 54)."""
    BUY = "1"
    SELL = "2"


class TimeInForce(Enum):
    """
    FIX TimeInForce (tag 59).

    These are the standard FIX code points. Which of them BIST accepts for a
    given instrument, market and session phase is venue-specific — confirm
    against the BISTECH certification documentation for your market rather than
    assuming every value here is routable.
    """
    DAY = "0"
    GOOD_TILL_CANCEL = "1"
    IMMEDIATE_OR_CANCEL = "3"
    FILL_OR_KILL = "4"
    GOOD_TILL_DATE = "6"


class OrderStatus(Enum):
    """
    FIX OrdStatus (tag 39) — the *current* state of the order.

    Distinct from ExecType (tag 150), which describes the report that carried the
    news. ``PENDING_CANCEL`` is the state between sending an Order Cancel Request
    and the venue answering it: the order is still live and can still fill.
    """
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    PENDING_CANCEL = "6"
    REJECTED = "8"


# Once here, the order is done and no report may change it further.
TERMINAL_STATUSES = frozenset({
    OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
})

# States from which a cancel request may be sent. PENDING_CANCEL is excluded:
# one outstanding cancel request per order.
CANCELABLE_STATUSES = frozenset({
    OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED,
})

# States in which the order is still working at the venue and may still fill.
# PENDING_CANCEL is deliberately included — that is the whole point.
WORKING_STATUSES = frozenset({
    OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL,
})


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
    time_in_force: TimeInForce = TimeInForce.DAY
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    @property
    def remaining_quantity(self) -> float:
        """Quantity still working at the venue. Never negative."""
        return max(0.0, self.quantity - self.filled_quantity)


class BISTIntegrationEngine:
    """
    Order-lifecycle state machine for Borsa Istanbul (BISTECH) over FIX 5.0 SP2
    semantics: order entry, cancel request/confirm/reject, and idempotent
    execution-report application.

    Session establishment and message transport are simulated — see the module
    docstring for the scope boundary.
    """

    def __init__(self, config: BISTConfig):
        self.config = config
        self.is_connected = False
        self.orders: Dict[str, FIXOrder] = {}
        # Counter of application messages this engine has emitted in the current
        # session. It is NOT a FIX sequence number: real sequence-number
        # assignment, gap fill and cross-restart persistence belong to the FIX
        # engine, and this module does not persist anything.
        self.session_seq_num = 1
        # Keyed by (ClOrdID, ExecID) rather than ExecID alone. FIX ExecIDs are
        # unique per venue, but callers synthesising them per order would
        # otherwise have a fill on one order silently suppress a fill on
        # another. This grows with the number of fills in the session; a
        # long-running process should evict alongside completed orders.
        self._applied_exec_ids: Set[Tuple[str, str]] = set()
        logger.info(
            f"Initialized BIST FIX Engine for {config.sender_comp_id} "
            f"targeting {config.target_comp_id}"
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Simulates establishing a FIX session with BISTECH (Logon, MsgType=A)."""
        if not self.config.host or not self.config.host.strip():
            raise ValueError("Invalid host configuration: host must be a non-empty string")
        if not isinstance(self.config.port, int) or isinstance(self.config.port, bool):
            raise ValueError(f"Invalid port configuration: {self.config.port!r} is not an integer")
        if not 1 <= self.config.port <= 65535:
            raise ValueError(f"Invalid port configuration: {self.config.port} is outside 1-65535")
        # FIX requires both CompIDs on every message; an empty one cannot log on.
        if not self.config.sender_comp_id or not self.config.sender_comp_id.strip():
            raise ValueError("sender_comp_id must be a non-empty string")
        if not self.config.target_comp_id or not self.config.target_comp_id.strip():
            raise ValueError("target_comp_id must be a non-empty string")

        if self.is_connected:
            logger.warning("connect() called while already connected; ignoring.")
            return True

        logger.info(
            f"Connecting to BISTECH FIX Gateway at {self.config.host}:{self.config.port}..."
        )
        self.is_connected = True
        logger.info("FIX Session established. Logon (MsgType=A) successful.")
        return True

    def disconnect(self) -> bool:
        """Simulates graceful logout sequence (Logout, MsgType=5)."""
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
        Submits a NewOrderSingle (MsgType=D) to BISTECH.

        Raises:
            ConnectionError: no session established.
            ValueError: the order would be rejected on protocol or sanity
                grounds — including a duplicate ClOrdID, which must be unique
                per session. Overwriting a live order's record would discard its
                accumulated fill state, so it is refused rather than replaced.
        """
        if not self.is_connected:
            raise ConnectionError("FIX Session is not established. Cannot submit order.")

        if not isinstance(order, FIXOrder):
            raise ValueError(f"order must be a FIXOrder, got {type(order).__name__}")
        if not order.symbol or not order.symbol.strip():
            raise ValueError("Order symbol must be a non-empty string.")
        # NaN fails every comparison, so `quantity <= 0` alone lets it through.
        if not isinstance(order.quantity, (int, float)) or isinstance(order.quantity, bool):
            raise ValueError(f"Order quantity must be a number, got {order.quantity!r}")
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            raise ValueError(f"Order quantity must be finite and positive, got {order.quantity!r}")

        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                raise ValueError("Limit orders must specify a price.")
            if not math.isfinite(order.price) or order.price <= 0:
                raise ValueError(f"Limit price must be finite and positive, got {order.price!r}")
        elif order.order_type == OrderType.MARKET and order.price is not None:
            # FIX: NewOrderSingle with OrdType=Market must not carry Price.
            # Silently dropping it hides a caller bug that changes intent.
            raise ValueError("Market orders must not specify a price.")

        # A FIXOrder carrying fills or a non-NEW status is a previously-used
        # object being resubmitted; accepting it would import stale fill state
        # into what the venue will treat as a brand-new order.
        if order.status is not OrderStatus.NEW or order.filled_quantity != 0:
            raise ValueError(
                f"Order {order.client_order_id} is not in a submittable state "
                f"(status={order.status.name}, filled={order.filled_quantity}). "
                f"Construct a new FIXOrder rather than resubmitting a used one."
            )

        if order.client_order_id in self.orders:
            raise ValueError(
                f"Duplicate ClOrdID '{order.client_order_id}': an order with this ID already "
                f"exists in state {self.orders[order.client_order_id].status.name}. ClOrdID must "
                f"be unique; reusing it would discard the existing order's fill state."
            )

        self.orders[order.client_order_id] = order
        logger.info(
            f"Submitted {order.side.name} order for {order.quantity} {order.symbol} "
            f"@ {order.price or 'MKT'} (TIF={order.time_in_force.name})"
        )
        self.session_seq_num += 1

        return order.client_order_id

    def cancel_order(self, client_order_id: str) -> bool:
        """
        Sends an OrderCancelRequest (MsgType=F) for the remaining quantity.

        This **requests** cancellation; it does not cancel. The order moves to
        ``PENDING_CANCEL`` and stays live at the venue until the venue answers:
        call ``confirm_cancel()`` on an ExecutionReport with ExecType=Canceled,
        or ``reject_cancel()`` on an Order Cancel Reject (MsgType=9). Fills that
        arrive in between are real and are still applied.

        Returns:
            True if the request was sent, False if the order is unknown, already
            terminal, or already has a cancel request outstanding.
        """
        if not self.is_connected:
            raise ConnectionError("FIX Session is not established.")

        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning(f"Order {client_order_id} not found.")
            return False

        if order.status is OrderStatus.PENDING_CANCEL:
            logger.warning(
                f"Order {client_order_id} already has an outstanding cancel request; "
                "not sending a duplicate."
            )
            return False

        if order.status not in CANCELABLE_STATUSES:
            logger.warning(
                f"Cannot cancel order {client_order_id} in terminal state {order.status.name}"
            )
            return False

        order.status = OrderStatus.PENDING_CANCEL
        self.session_seq_num += 1
        logger.info(
            f"OrderCancelRequest (MsgType=F) sent for {client_order_id}; "
            f"awaiting venue confirmation. Order remains live until confirmed."
        )
        return True

    def confirm_cancel(self, client_order_id: str) -> Optional[FIXOrder]:
        """
        Applies a venue cancel confirmation (ExecutionReport, ExecType=Canceled).

        Accepts confirmation for an order in ``PENDING_CANCEL`` and also for one
        still working, since venues issue unsolicited cancels (session end,
        corporate action, risk action) without any request from us.

        Returns the order, or None if unknown. An order already terminal is
        returned unchanged.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning(f"Cancel confirmation for unknown order {client_order_id}.")
            return None

        if order.status in TERMINAL_STATUSES:
            logger.warning(
                f"Cancel confirmation for {client_order_id} already in terminal state "
                f"{order.status.name}; ignoring."
            )
            return order

        was_pending = order.status is OrderStatus.PENDING_CANCEL
        order.status = OrderStatus.CANCELED
        logger.info(
            f"Order {client_order_id} CANCELED at venue"
            f"{'' if was_pending else ' (unsolicited)'}; "
            f"{order.filled_quantity} of {order.quantity} filled."
        )
        return order

    def reject_cancel(self, client_order_id: str, reason: str = "") -> Optional[FIXOrder]:
        """
        Applies an Order Cancel Reject (MsgType=9): the venue refused the cancel.

        The order was never canceled and is still working, so it returns to the
        status its fill state implies — ``PARTIALLY_FILLED`` if anything has
        filled, otherwise ``NEW``. A common cause is that the order filled
        completely before the request arrived, in which case the fills will have
        already moved it to ``FILLED`` and this is a no-op.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning(f"Cancel reject for unknown order {client_order_id}.")
            return None

        if order.status is not OrderStatus.PENDING_CANCEL:
            logger.warning(
                f"Cancel reject for {client_order_id} in state {order.status.name} "
                f"(no cancel request outstanding); ignoring. Reason: {reason!r}"
            )
            return order

        order.status = (
            OrderStatus.PARTIALLY_FILLED if order.filled_quantity > 0 else OrderStatus.NEW
        )
        logger.warning(
            f"Cancel REJECTED for {client_order_id}: {reason!r}. Order is still working "
            f"in state {order.status.name} with {order.remaining_quantity} remaining."
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
        Applies a fill from an ExecutionReport (MsgType=8, ExecType=Trade).

        Args:
            client_order_id: ClOrdID the report refers to.
            filled_qty: LastQty — the quantity of *this* fill, not cumulative.
                Must be finite and positive; a zero-quantity report is an
                acknowledgement (ExecType=New), not a fill, and is not this
                method's job.
            exec_price: LastPx for this fill. Must be finite and positive.
            exec_id: ExecID (tag 17). Strongly recommended: it is the only way to
                tell a resent report from a new one after a sequence gap, and
                without it a resend double-counts the fill.

        Returns:
            The updated order, or None if the ClOrdID is unknown. Orders in a
            terminal state, and duplicate ExecIDs, are returned unchanged.

        A report that would push cumulative filled quantity past the order
        quantity is refused and logged at ERROR — it means a duplicate escaped
        deduplication or the venue sent something impossible. Alert on it; do not
        treat the returned order as authoritative until it is reconciled.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning(f"Execution report for unknown order {client_order_id}.")
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
                f"Execution report for {client_order_id} in terminal state "
                f"{order.status.name}; ignoring."
            )
            return order

        dedup_key = (client_order_id, exec_id) if exec_id is not None else None
        if dedup_key is not None:
            if dedup_key in self._applied_exec_ids:
                logger.warning(
                    f"Duplicate ExecID {exec_id!r} for order {client_order_id}; "
                    "already applied, ignoring (resend after sequence gap)."
                )
                return order
        else:
            logger.warning(
                f"Execution report for {client_order_id} carries no ExecID; fill application "
                "cannot be made idempotent and a resend will double-count."
            )

        new_filled = order.filled_quantity + filled_qty
        if new_filled > order.quantity + _QTY_TOLERANCE:
            logger.error(
                f"REJECTED overfill on {client_order_id}: fill of {filled_qty} would take "
                f"cumulative filled quantity to {new_filled}, exceeding order quantity "
                f"{order.quantity}. Duplicate report or venue error — reconcile before trading on "
                f"this position."
            )
            return order

        previous_filled = order.filled_quantity
        order.filled_quantity = new_filled
        # Quantity-weighted average of the previous average and this fill.
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
            f"Execution Report: {filled_qty} filled @ {exec_price}. "
            f"Status: {order.status.name}, cum {order.filled_quantity}/{order.quantity}, "
            f"avg {order.average_price}"
        )
        return order
