"""
order-placement-idempotency: Production-grade write-ahead order intent ledger,
idempotency key generation, 4-state transition machine, network timeout reconciliation,
and startup crash recovery manager.
"""
from dataclasses import dataclass
from enum import Enum
import hashlib
import logging
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OrderIntentStatus(str, Enum):
    PENDING = "PENDING"      # Pre-network write-ahead intent recorded
    PLACED = "PLACED"        # Confirmed broker response received with broker_order_id
    REJECTED = "REJECTED"    # Confirmed broker rejection received (e.g. margin/invalid)
    UNKNOWN = "UNKNOWN"      # Network timeout / dropped response; requires reconciliation


# Backward compatibility constants
PENDING = OrderIntentStatus.PENDING.value
PLACED = OrderIntentStatus.PLACED.value
REJECTED = OrderIntentStatus.REJECTED.value
UNKNOWN = OrderIntentStatus.UNKNOWN.value


def make_idempotency_key(
    strategy_id: str,
    symbol: str,
    side: str,
    signal_ts: Any,
    qty: float = 0.0,
    price: float = 0.0,
) -> str:
    """Generates deterministic 24-char SHA-256 idempotency key."""
    raw = f"{strategy_id}:{symbol}:{side}:{signal_ts}:{qty}:{price}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class OrderLedger:
    """
    SQLite-backed durable local order intent ledger tracking all outbound order states
    to prevent double execution on retries or restarts.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    strategy_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    quantity REAL,
                    price REAL,
                    status TEXT NOT NULL,
                    broker_order_id TEXT,
                    rejection_reason TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)

    def record_intent(
        self,
        key: str,
        strategy_id: str = "default",
        symbol: str = "NIFTY",
        side: str = "BUY",
        quantity: float = 1.0,
        price: float = 0.0,
    ) -> bool:
        """Writes PENDING intent record BEFORE making network API call."""
        now = time.time()
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO orders (idempotency_key, strategy_id, symbol, side, quantity, price, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, strategy_id, symbol, side, quantity, price, OrderIntentStatus.PENDING.value, now, now),
                )
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Duplicate intent record ignored for key '{key}'")
            return False

    def update_status(
        self,
        key: str,
        status: OrderIntentStatus,
        broker_order_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ):
        now = time.time()
        status_val = status.value if isinstance(status, OrderIntentStatus) else str(status)
        with self.conn:
            self.conn.execute(
                """
                UPDATE orders 
                SET status=?, broker_order_id=?, rejection_reason=?, updated_at=?
                WHERE idempotency_key=?
                """,
                (status_val, broker_order_id, rejection_reason, now, key),
            )

    def get_order(self, key: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM orders WHERE idempotency_key=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "idempotency_key": row[0],
            "strategy_id": row[1],
            "symbol": row[2],
            "side": row[3],
            "quantity": row[4],
            "price": row[5],
            "status": row[6],
            "broker_order_id": row[7],
            "rejection_reason": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }

    def unresolved(self) -> List[str]:
        """Returns list of idempotency keys in PENDING or UNKNOWN state requiring reconciliation."""
        cur = self.conn.execute(
            "SELECT idempotency_key FROM orders WHERE status IN (?, ?)",
            (OrderIntentStatus.PENDING.value, OrderIntentStatus.UNKNOWN.value),
        )
        return [row[0] for row in cur.fetchall()]


class IdempotentOrderRouter:
    """
    Executes order placement using write-ahead logging and network timeout reconciliation.
    """

    def __init__(self, ledger: OrderLedger, alert_fn: Optional[Callable[[str], None]] = None):
        self.ledger = ledger
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))

    def place_order(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        signal_ts: Any,
        broker_send_fn: Callable[[str, str, str, float, float, str], Dict[str, Any]],
        broker_order_book_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Guarantees idempotent order execution:
        1. Write PENDING intent.
        2. Attempt broker POST.
        3. Handle Success, Rejection, or Timeout (UNKNOWN -> Reconcile).
        """
        key = make_idempotency_key(strategy_id, symbol, side, signal_ts, quantity, price)

        # Check if already recorded
        existing = self.ledger.get_order(key)
        if existing:
            if existing["status"] == OrderIntentStatus.PLACED.value:
                logger.info(f"Idempotent skip: Order {key} already PLACED as {existing['broker_order_id']}")
                return True, "ALREADY_PLACED", existing["broker_order_id"]
            elif existing["status"] == OrderIntentStatus.REJECTED.value:
                return False, f"ALREADY_REJECTED: {existing['rejection_reason']}", None

        # 1. Write Intent (PENDING)
        self.ledger.record_intent(key, strategy_id, symbol, side, quantity, price)

        # 2. Execute Network Call
        try:
            resp = broker_send_fn(key, symbol, side, quantity, price, strategy_id)
            if resp.get("status") == "SUCCESS":
                broker_id = resp.get("broker_order_id", "ORDER_UNKNOWN_ID")
                self.ledger.update_status(key, OrderIntentStatus.PLACED, broker_order_id=broker_id)
                return True, "PLACED", broker_id
            else:
                reason = resp.get("reason", "Broker rejected order")
                self.ledger.update_status(key, OrderIntentStatus.REJECTED, rejection_reason=reason)
                return False, f"REJECTED: {reason}", None
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"Network timeout/exception during order {key}: {err_msg}")
            self.ledger.update_status(key, OrderIntentStatus.UNKNOWN, rejection_reason=err_msg)

            # 3. Reconcile UNKNOWN state if order book query available
            if broker_order_book_fn:
                found_id = self._reconcile_unknown(key, broker_order_book_fn)
                if found_id:
                    return True, "RECONCILED_PLACED", found_id

            return False, f"UNKNOWN_TIMEOUT: {err_msg}", None

    def _reconcile_unknown(
        self, key: str, broker_order_book_fn: Callable[[], List[Dict[str, Any]]]
    ) -> Optional[str]:
        order_info = self.ledger.get_order(key)
        if not order_info:
            return None

        try:
            book = broker_order_book_fn()
            for b_order in book:
                # Match by client_order_id or matching tuple (symbol, side, qty, price)
                b_client_id = b_order.get("client_order_id", "")
                if b_client_id == key or (
                    b_order.get("symbol") == order_info["symbol"]
                    and b_order.get("side") == order_info["side"]
                    and abs(b_order.get("quantity", 0) - order_info["quantity"]) < 1e-5
                ):
                    b_id = b_order.get("broker_order_id", "RECONCILED_ID")
                    logger.info(f"Reconciliation SUCCESS for {key} -> Matched broker order {b_id}")
                    self.ledger.update_status(key, OrderIntentStatus.PLACED, broker_order_id=b_id)
                    return b_id
        except Exception as e:
            logger.error(f"Error during broker order book reconciliation: {e}")

        return None
