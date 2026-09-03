"""
order-placement-idempotency: durable write-ahead order-intent ledger, stable
idempotency-key derivation, an enforced intent state machine, conservative
broker-response classification, tri-state reconciliation of ambiguous sends,
and startup crash recovery.

Design invariants (each is covered by a test in ``test_order_ledger.py``):

1. The ``PENDING`` intent row is committed to durable storage *before* the
   broker call is issued.
2. ``IdempotentOrderRouter.place_order`` invokes ``broker_send_fn`` **at most
   once per call**, and never at all while a prior intent for the same key is
   unresolved and reconciliation has not proven the order absent.
3. An outcome the broker did not state unambiguously is ``UNKNOWN``, never
   ``REJECTED`` and never ``PLACED``. A failure *word* is not a refusal: Kite's
   ``{"status": "error", "error_type": "NetworkException"}`` is a gateway fault
   whose order outcome is unknown, while ``InputException`` is a real refusal.
4. ``UNKNOWN`` is resolved to ``ABSENT`` (safe to re-send) only when the broker
   echoes the client key back in its order book, so absence is evidence. An
   ``ABSENT`` verdict releases the claim on the key — archived, not discarded —
   so the re-send it authorises can actually proceed.
5. The intent state machine rejects illegal transitions; ``PLACED`` and
   ``REJECTED`` are terminal.

None of this makes an order *itself* idempotent at the broker. No broker in
this skill's coverage table documents an idempotency guarantee for order
placement (see ``references/standards.md``). This module makes the *client*
idempotent: it can always answer "did I already send this, and what happened?"
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import logging
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "OrderIntentStatus",
    "ReconcileOutcome",
    "ReconcileResult",
    "IllegalStateTransition",
    "PENDING",
    "PLACED",
    "REJECTED",
    "UNKNOWN",
    "BROKER_KEY_MAX_LEN",
    "make_idempotency_key",
    "classify_broker_response",
    "OrderLedger",
    "IdempotentOrderRouter",
]


class OrderIntentStatus(str, Enum):
    """Lifecycle of a single order intent held in the local ledger."""

    PENDING = "PENDING"      # Write-ahead intent committed; broker call not yet resolved
    PLACED = "PLACED"        # Broker unambiguously acknowledged, with a broker order id
    REJECTED = "REJECTED"    # Broker unambiguously refused the order
    UNKNOWN = "UNKNOWN"      # Outcome indeterminate; requires reconciliation


# Backward-compatible string constants for callers that imported them directly.
PENDING = OrderIntentStatus.PENDING.value
PLACED = OrderIntentStatus.PLACED.value
REJECTED = OrderIntentStatus.REJECTED.value
UNKNOWN = OrderIntentStatus.UNKNOWN.value

#: Terminal states. Once an intent reaches one of these it is never re-sent.
_TERMINAL = frozenset({OrderIntentStatus.PLACED, OrderIntentStatus.REJECTED})

#: Allowed intent state transitions. Anything not listed here is refused rather
#: than silently applied, because a ledger that accepts PLACED -> PENDING can be
#: talked into re-sending an order that is already live.
_ALLOWED_TRANSITIONS: Dict[OrderIntentStatus, frozenset] = {
    OrderIntentStatus.PENDING: frozenset(
        {OrderIntentStatus.PENDING, OrderIntentStatus.PLACED,
         OrderIntentStatus.REJECTED, OrderIntentStatus.UNKNOWN}
    ),
    # UNKNOWN -> PENDING is deliberately absent. Re-arming an intent whose
    # outcome is unknown is how one client key ends up with two live broker
    # orders. The only way back to a sendable state is `release_intent`, which
    # requires reconciliation to have proved the order ABSENT and leaves an
    # archived record of the release.
    OrderIntentStatus.UNKNOWN: frozenset(
        {OrderIntentStatus.UNKNOWN,
         OrderIntentStatus.PLACED, OrderIntentStatus.REJECTED}
    ),
    OrderIntentStatus.PLACED: frozenset({OrderIntentStatus.PLACED}),
    OrderIntentStatus.REJECTED: frozenset({OrderIntentStatus.REJECTED}),
}


class IllegalStateTransition(RuntimeError):
    """Raised when a caller attempts a transition the state machine refuses.

    That is a transition out of a terminal state, or one that would re-arm an
    unresolved intent for a second send.
    """


class ReconcileOutcome(str, Enum):
    """Result of comparing one unresolved intent against the broker order book."""

    FOUND_PLACED = "FOUND_PLACED"      # The order exists at the broker and is working/filled
    FOUND_REJECTED = "FOUND_REJECTED"  # The order reached the broker and was refused
    ABSENT = "ABSENT"                  # Proven not to exist; re-sending the key is safe
    INCONCLUSIVE = "INCONCLUSIVE"      # Cannot tell — never re-send on this outcome


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of a reconciliation attempt for a single idempotency key."""

    outcome: ReconcileOutcome
    broker_order_id: Optional[str] = None
    detail: str = ""

    @property
    def resolved(self) -> bool:
        """True when the broker's state for this intent is now known."""
        return self.outcome in (
            ReconcileOutcome.FOUND_PLACED,
            ReconcileOutcome.FOUND_REJECTED,
            ReconcileOutcome.ABSENT,
        )


# --------------------------------------------------------------------------
# Idempotency key derivation
# --------------------------------------------------------------------------

#: Documented client-tag length limits, used when validating ``max_len``.
#: Zerodha Kite Connect documents ``tag`` as "alphanumeric, max 20 chars", so
#: this helper's 24-character default does **not** fit it. Alpaca documents
#: ``client_order_id`` at 128 characters. Verify per broker and per API version
#: before relying on any entry (see ``references/standards.md``).
BROKER_KEY_MAX_LEN: Dict[str, int] = {
    "zerodha_kite": 20,
    "alpaca": 128,
}

_MIN_KEY_LEN = 8
_MAX_KEY_LEN = 64


def _canonical_number(value: Any) -> str:
    """Renders a quantity/price so ``50`` and ``50.0`` hash identically.

    Key stability across process restarts is the whole point: a strategy that
    passes ``qty=50`` on the hot path and ``qty=50.0`` after a restart would
    otherwise derive two different keys for one order and re-send it.
    """
    if isinstance(value, bool):  # bool is an int subclass; refuse it explicitly
        raise TypeError("quantity/price must be numeric, not bool")
    if value is None:
        return "None"
    if isinstance(value, (int, float)):
        return "%.8f" % float(value)
    return str(value)


def _canonical_timestamp(value: Any) -> str:
    """Renders a signal timestamp canonically.

    A naive ``datetime`` and its ISO string must not produce different keys.
    Naive datetimes are interpreted as UTC and that assumption is logged,
    because guessing a local zone here would silently shift the key between
    hosts in different regions.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            logger.debug("Naive signal_ts %s interpreted as UTC for key derivation", value)
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        raise TypeError("signal_ts must not be a bool")
    if isinstance(value, (int, float)):
        return "%.6f" % float(value)
    return str(value)


def make_idempotency_key(
    strategy_id: str,
    symbol: str,
    side: str,
    signal_ts: Any,
    qty: float = 0.0,
    price: float = 0.0,
    sequence: int = 0,
    max_len: int = 24,
) -> str:
    """Derives a deterministic, restart-stable idempotency key.

    Inputs are canonicalised before hashing so that equivalent-but-differently-
    typed arguments (``50`` vs ``50.0``, a ``datetime`` vs its ISO string,
    ``"buy"`` vs ``"BUY"``) map to the same key.

    Args:
        strategy_id: Stable identifier of the strategy instance.
        symbol: Broker symbol; whitespace-trimmed and upper-cased.
        side: ``BUY``/``SELL``; case-insensitive.
        signal_ts: Signal timestamp — ``datetime``, ``date``, epoch number, or
            string. Naive datetimes are treated as UTC.
        qty: Order quantity.
        price: Order price (``0`` for market orders).
        sequence: Discriminator for orders that are *legitimately* identical —
            e.g. two child slices of one parent at the same signal timestamp.
            Without it those collapse onto one key and the second is silently
            suppressed as a duplicate.
        max_len: Truncation length of the hex digest. Must fit the broker's
            client-tag field: Kite Connect's ``tag`` is capped at 20 characters.

    Returns:
        A lowercase hex string of length ``max_len``.

    Raises:
        ValueError: If ``strategy_id``/``symbol``/``side`` is empty, ``sequence``
            is negative, or ``max_len`` is outside ``[8, 64]``.
    """
    if not str(strategy_id).strip():
        raise ValueError("strategy_id must be a non-empty string")
    if not str(symbol).strip():
        raise ValueError("symbol must be a non-empty string")
    if not str(side).strip():
        raise ValueError("side must be a non-empty string")
    if sequence < 0:
        raise ValueError("sequence must be >= 0")
    if not _MIN_KEY_LEN <= max_len <= _MAX_KEY_LEN:
        raise ValueError(
            "max_len must be between %d and %d (got %r); Kite Connect's `tag` "
            "field allows at most 20 characters" % (_MIN_KEY_LEN, _MAX_KEY_LEN, max_len)
        )

    raw = ":".join(
        (
            str(strategy_id).strip(),
            str(symbol).strip().upper(),
            str(side).strip().upper(),
            _canonical_timestamp(signal_ts),
            _canonical_number(qty),
            _canonical_number(price),
            str(int(sequence)),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:max_len]


# --------------------------------------------------------------------------
# Broker response classification
# --------------------------------------------------------------------------

_SUCCESS_TOKENS = frozenset({"success", "ok", "placed", "accepted", "complete", "completed"})

#: Status tokens that are an explicit refusal on their own: the broker states
#: that the order did not become live. Nothing ambiguous belongs here.
_REJECT_TOKENS = frozenset({"rejected", "reject", "cancelled", "canceled"})

#: Status tokens that *read* like a refusal but do not say where the failure
#: happened. Zerodha Kite returns ``{"status": "error", "error_type": …}`` for a
#: deterministic refusal (``InputException``) and for a gateway fault
#: (``NetworkException``, ``GatewayTimeout``) alike — and in the second case the
#: order may well be live. The token alone is not evidence; ``error_type`` and
#: the HTTP status decide, and when neither settles it the answer is ``UNKNOWN``.
_AMBIGUOUS_FAILURE_TOKENS = frozenset({"error", "failed", "failure"})

#: Broker exception classes that mean the broker evaluated the request and
#: declined it, so no order was created. Anything not listed — notably
#: ``NetworkException``, ``GatewayTimeout``, ``DataException`` and
#: ``GeneralException`` — is treated as indeterminate and reconciled.
_REFUSAL_ERROR_TYPES = frozenset({
    "inputexception",       # malformed/invalid request; never reached the book
    "orderexception",       # order placement refused
    "marginexception",      # insufficient margin
    "permissionexception",  # not permitted to trade this instrument/segment
    "tokenexception",       # session invalid; the request was not authorised
})

_ORDER_ID_FIELDS: Tuple[str, ...] = ("broker_order_id", "order_id", "orderId", "id")
_REASON_FIELDS: Tuple[str, ...] = ("reason", "message", "error", "errorMessage", "emsg")
_ERROR_TYPE_FIELDS: Tuple[str, ...] = ("error_type", "errorType", "exception")
#: Only fields that unambiguously carry an *HTTP* status. Alpaca's ``code`` is a
#: numeric error code, not an HTTP status, so reading it here would misread
#: ``40310000`` as a 5xx.
_HTTP_STATUS_FIELDS: Tuple[str, ...] = (
    "status_code", "statusCode", "http_status", "httpStatus",
)


def _extract_order_ids(payload: Any) -> List[str]:
    """Pulls broker order ids out of a response body.

    Handles the flat shape, the Kite Connect ``{"data": {"order_id": ...}}``
    shape, and the Kite auto-slice shape where ``data`` is a *list* of order
    ids returned for a single placement request.
    """
    ids: List[str] = []
    if isinstance(payload, Mapping):
        for field in _ORDER_ID_FIELDS:
            value = payload.get(field)
            if value not in (None, ""):
                ids.append(str(value))
                break
        nested = payload.get("data")
        if nested is not None and not ids:
            ids.extend(_extract_order_ids(nested))
        elif isinstance(nested, (list, tuple)):
            ids.extend(_extract_order_ids(nested))
    elif isinstance(payload, (list, tuple)) and not isinstance(payload, (str, bytes)):
        for item in payload:
            ids.extend(_extract_order_ids(item))
    return list(dict.fromkeys(ids))  # de-duplicate, preserve order


def _extract_reason(payload: Mapping[str, Any]) -> str:
    for field in _REASON_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Broker rejected order (no reason supplied)"


def _extract_error_type(payload: Mapping[str, Any]) -> str:
    """The broker's exception class, lower-cased, or ``""`` when absent."""
    for field in _ERROR_TYPE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _extract_http_status(payload: Mapping[str, Any]) -> Optional[int]:
    """The HTTP status of the placement response, or None if not reported."""
    for field in _HTTP_STATUS_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def classify_broker_response(
    response: Any,
) -> Tuple[OrderIntentStatus, Optional[str], str]:
    """Conservatively classifies a broker place-order response.

    The rule that matters: a response the broker did not make unambiguous is
    ``UNKNOWN``. Classifying it ``REJECTED`` writes "no order exists" into the
    ledger while an order may be working, and the next signal then places a
    duplicate — the precise failure this skill exists to prevent.

    That rule is why a failure *word* is not enough to reach ``REJECTED``.
    Zerodha Kite answers a gateway fault with ``{"status": "error",
    "error_type": "NetworkException"}`` — the same ``status`` token it uses for
    a flat refusal such as ``InputException``, but an outcome that is genuinely
    unknown: the request may have reached the matching engine before the
    gateway gave up. Only an explicit refusal reaches ``REJECTED``:

    * an explicit rejection ``status`` (``rejected``/``cancelled``), or
    * an ``error_type`` naming a class the broker uses to decline a request
      (see ``_REFUSAL_ERROR_TYPES``).

    Transport and gateway classes, unrecognised error classes and any 5xx are
    ``UNKNOWN`` and must be reconciled against the order book.

    Returns:
        ``(status, broker_order_id, detail)``. ``broker_order_id`` is non-None
        only for ``PLACED``. When one placement request yields several broker
        orders (Kite auto-slicing), the ids are joined with ``,``.
    """
    if not isinstance(response, Mapping):
        return (
            OrderIntentStatus.UNKNOWN,
            None,
            "Unrecognised broker response type %s" % type(response).__name__,
        )

    raw_status = response.get("status", response.get("stat", ""))
    status_token = str(raw_status).strip().lower()
    order_ids = _extract_order_ids(response)
    error_type = _extract_error_type(response)
    http_status = _extract_http_status(response)

    if status_token in _REJECT_TOKENS:
        return OrderIntentStatus.REJECTED, None, _extract_reason(response)

    if http_status is not None and http_status >= 500:
        # A 5xx is the server failing to answer, not the server declining the
        # order. The request may have been processed before the fault.
        return (
            OrderIntentStatus.UNKNOWN,
            None,
            "Broker returned HTTP %d (%s); the order may still have been accepted"
            % (http_status, _extract_reason(response)),
        )

    if error_type in _REFUSAL_ERROR_TYPES:
        return OrderIntentStatus.REJECTED, None, _extract_reason(response)

    if error_type or status_token in _AMBIGUOUS_FAILURE_TOKENS:
        # A failure the broker did not attribute to a refusal it made. Treating
        # a NetworkException/GatewayTimeout as REJECTED records "no order
        # exists" while one may be working at the exchange.
        return (
            OrderIntentStatus.UNKNOWN,
            None,
            "Broker reported a failure it did not attribute to a refusal "
            "(status=%r, error_type=%r): %s — reconcile before re-sending"
            % (raw_status, error_type or None, _extract_reason(response)),
        )

    if status_token in _SUCCESS_TOKENS and not order_ids:
        # An acknowledgement with no order id cannot be reconciled later.
        # Treating it as PLACED would fabricate an id and lose the order.
        return (
            OrderIntentStatus.UNKNOWN,
            None,
            "Broker reported success but returned no order id",
        )

    if order_ids:
        # An order id came back and the broker did not say "rejected": an order
        # exists. This branch also covers the working-state acknowledgements
        # brokers actually send — Kite `OPEN`, Alpaca `new`/`pending_new`,
        # Binance `NEW` — which are placements, not indeterminate outcomes.
        if len(order_ids) > 1:
            logger.warning(
                "Placement returned %d broker order ids (%s) — the broker sliced this "
                "request; one ledger row now maps to several live orders",
                len(order_ids), ",".join(order_ids),
            )
        return OrderIntentStatus.PLACED, ",".join(order_ids), "Broker acknowledged placement"

    return (
        OrderIntentStatus.UNKNOWN,
        None,
        "Indeterminate broker response (status=%r)" % (raw_status,),
    )


# --------------------------------------------------------------------------
# Durable ledger
# --------------------------------------------------------------------------

class OrderLedger:
    """SQLite-backed durable write-ahead ledger of outbound order intents.

    The ``idempotency_key`` primary key is the atomic claim: the row insert
    either succeeds (this caller owns the send) or raises ``IntegrityError``
    (someone else already owns it). That is what makes ``record_intent`` safe
    across threads *and* across processes sharing one database file.

    Usable as a context manager. ``check_same_thread=False`` is set because
    every method serialises on an instance lock.
    """

    def __init__(self, db_path: str = ":memory:", durable: bool = True):
        """Opens (and initialises) the ledger database.

        Args:
            db_path: SQLite path. ``":memory:"`` is for tests only — an
                in-memory ledger cannot survive the process crash it exists to
                protect against.
            durable: When True and ``db_path`` is a real file, set
                ``synchronous=FULL`` so a committed intent survives an abrupt
                host failure. Costs one fsync per intent write.
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        if db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=%s" % ("FULL" if durable else "NORMAL"))
        else:
            logger.warning(
                "OrderLedger opened in-memory: intents will NOT survive a restart. "
                "Use a file path anywhere but tests."
            )
        self._init_db()

    def __enter__(self) -> "OrderLedger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """Closes the underlying connection."""
        with self._lock:
            self.conn.close()

    def _init_db(self) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
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
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status)"
            )
            # Releasing a claim removes the row from `orders` so the key can be
            # claimed again. The row is archived here first: an auditor asking
            # why one client tag was sent twice needs the record that says the
            # first send was proven never to have reached the broker.
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS released_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    strategy_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    quantity REAL,
                    price REAL,
                    status_at_release TEXT,
                    rejection_reason TEXT,
                    release_reason TEXT,
                    created_at REAL,
                    released_at REAL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_released_key "
                "ON released_intents (idempotency_key)"
            )

    def record_intent(
        self,
        key: str,
        strategy_id: str,
        symbol: str,
        side: str = "BUY",
        quantity: float = 1.0,
        price: float = 0.0,
    ) -> bool:
        """Commits a ``PENDING`` intent *before* the network call.

        ``strategy_id`` and ``symbol`` are required. They used to default to
        ``"default"``/``"NIFTY"``, which silently wrote a row describing an
        order nobody placed — and attribute reconciliation (the fallback for
        brokers that do not echo the client key) matches on exactly those
        fields, so a wrong symbol there quietly makes the intent unmatchable.

        Returns:
            True if this caller created the row and therefore owns the send.
            False if an intent for ``key`` already exists — the caller must
            **not** send, and must reconcile instead.

        Raises:
            ValueError: If ``strategy_id`` or ``symbol`` is empty.
        """
        if not str(strategy_id).strip():
            raise ValueError("strategy_id must be a non-empty string")
        if not str(symbol).strip():
            raise ValueError("symbol must be a non-empty string")
        now = time.time()
        try:
            with self._lock, self.conn:
                self.conn.execute(
                    """
                    INSERT INTO orders (idempotency_key, strategy_id, symbol, side,
                                        quantity, price, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, strategy_id, symbol, side, quantity, price,
                     OrderIntentStatus.PENDING.value, now, now),
                )
            return True
        except sqlite3.IntegrityError:
            logger.warning("Intent already exists for key '%s'; refusing to claim it twice", key)
            return False

    def update_status(
        self,
        key: str,
        status: Any,
        broker_order_id: Optional[str] = None,
        rejection_reason: Optional[str] = None,
    ) -> bool:
        """Applies a state transition, enforcing the intent state machine.

        ``broker_order_id`` and ``rejection_reason`` are only overwritten when
        supplied; passing None preserves what is already recorded, so a late
        status write cannot erase the broker order id needed to reconcile.

        Returns:
            True if the row exists (and was updated where the transition was
            not a terminal no-op), False if ``key`` is unknown.

        Raises:
            IllegalStateTransition: On a transition out of a terminal state
                (``PLACED``/``REJECTED``), or on ``UNKNOWN -> PENDING`` — both
                would re-arm a possibly-live order for re-sending. Use
                ``release_intent`` for the one case where that is provably safe.
            ValueError: If ``status`` is not a valid ``OrderIntentStatus``.
        """
        raw = status.value if isinstance(status, OrderIntentStatus) else str(status)
        try:
            new_status = OrderIntentStatus(raw)
        except ValueError:
            raise ValueError("Unknown order intent status: %r" % (status,)) from None

        now = time.time()
        with self._lock, self.conn:
            cur = self.conn.execute(
                "SELECT status FROM orders WHERE idempotency_key=?", (key,)
            )
            row = cur.fetchone()
            if row is None:
                logger.error("update_status called for unknown key '%s'", key)
                return False

            current = OrderIntentStatus(row[0])
            if new_status not in _ALLOWED_TRANSITIONS[current]:
                why = (
                    "%s is terminal" % current.value if current in _TERMINAL
                    else "an unresolved intent is re-armed only via release_intent(), "
                         "and only once reconciliation has proved it ABSENT"
                )
                raise IllegalStateTransition(
                    "Refusing %s -> %s for key '%s': %s"
                    % (current.value, new_status.value, key, why)
                )
            if current in _TERMINAL:
                # Same-state rewrite of a terminal row: no-op rather than churn.
                return True

            self.conn.execute(
                """
                UPDATE orders
                SET status=?,
                    broker_order_id=COALESCE(?, broker_order_id),
                    rejection_reason=COALESCE(?, rejection_reason),
                    updated_at=?
                WHERE idempotency_key=?
                """,
                (new_status.value, broker_order_id, rejection_reason, now, key),
            )
        return True

    def get_order(self, key: str) -> Optional[Dict[str, Any]]:
        """Returns the ledger row for ``key``, or None."""
        with self._lock:
            cur = self.conn.execute(
                """
                SELECT idempotency_key, strategy_id, symbol, side, quantity, price,
                       status, broker_order_id, rejection_reason, created_at, updated_at
                FROM orders WHERE idempotency_key=?
                """,
                (key,),
            )
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
        """Returns keys in ``PENDING`` or ``UNKNOWN`` state, oldest first.

        These are exactly the intents that must be reconciled against the
        broker before the strategy is allowed to generate new orders.
        """
        with self._lock:
            cur = self.conn.execute(
                "SELECT idempotency_key FROM orders WHERE status IN (?, ?) "
                "ORDER BY created_at ASC",
                (OrderIntentStatus.PENDING.value, OrderIntentStatus.UNKNOWN.value),
            )
            return [row[0] for row in cur.fetchall()]

    def linked_broker_order_ids(self) -> Set[str]:
        """Broker order ids already claimed by some ledger row.

        Reconciliation must not attach an order another intent already owns, or
        one broker order gets credited to two intents and the second order is
        silently presumed placed.
        """
        with self._lock:
            cur = self.conn.execute(
                "SELECT broker_order_id FROM orders WHERE broker_order_id IS NOT NULL"
            )
            claimed: Set[str] = set()
            for (value,) in cur.fetchall():
                claimed.update(part for part in str(value).split(",") if part)
            return claimed

    def release_intent(self, key: str, reason: str = "") -> bool:
        """Releases a claim on ``key`` that was *proven* never to have landed.

        This is the only way an intent leaves ``PENDING``/``UNKNOWN`` without a
        broker outcome, and it exists so the "absent, safe to re-send" path can
        actually re-send: while the row is present, ``record_intent`` refuses to
        claim the key, so a caller told to re-invoke ``place_order`` would loop
        on the same ``ABSENT`` verdict forever.

        Call it **only** after reconciliation returned ``ABSENT`` — that is, the
        broker echoes client keys and this key is not in its order book. Calling
        it on an intent whose outcome is merely unknown re-arms a possibly-live
        order for a second send, which is the duplicate this module exists to
        prevent.

        The row is copied into ``released_intents`` before deletion, so the
        placement history of a key survives the release.

        Returns:
            True if a row was released, False if ``key`` is unknown.

        Raises:
            IllegalStateTransition: If the row is ``PLACED`` or ``REJECTED``.
                A terminal row is a settled outcome; releasing it would allow a
                live order to be sent again under the same key.
        """
        now = time.time()
        with self._lock, self.conn:
            cur = self.conn.execute(
                """
                SELECT idempotency_key, strategy_id, symbol, side, quantity, price,
                       status, rejection_reason, created_at
                FROM orders WHERE idempotency_key=?
                """,
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                logger.error("release_intent called for unknown key '%s'", key)
                return False

            current = OrderIntentStatus(row[6])
            if current in _TERMINAL:
                raise IllegalStateTransition(
                    "Refusing to release intent '%s': it is %s, a settled outcome; "
                    "releasing it would permit a second send of a live order"
                    % (key, current.value)
                )

            self.conn.execute(
                """
                INSERT INTO released_intents (
                    idempotency_key, strategy_id, symbol, side, quantity, price,
                    status_at_release, rejection_reason, release_reason,
                    created_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row[0], row[1], row[2], row[3], row[4], row[5],
                 row[6], row[7], reason, row[8], now),
            )
            self.conn.execute("DELETE FROM orders WHERE idempotency_key=?", (key,))

        logger.info("Released intent '%s' (was %s): %s", key, row[6], reason)
        return True

    def released_history(self, key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Archived intents released after being proven absent, oldest first."""
        sql = (
            "SELECT idempotency_key, strategy_id, symbol, side, quantity, price, "
            "status_at_release, rejection_reason, release_reason, created_at, "
            "released_at FROM released_intents"
        )
        params: Tuple[Any, ...] = ()
        if key is not None:
            sql += " WHERE idempotency_key=?"
            params = (key,)
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "idempotency_key": r[0],
                "strategy_id": r[1],
                "symbol": r[2],
                "side": r[3],
                "quantity": r[4],
                "price": r[5],
                "status_at_release": r[6],
                "rejection_reason": r[7],
                "release_reason": r[8],
                "created_at": r[9],
                "released_at": r[10],
            }
            for r in rows
        ]


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

BrokerSendFn = Callable[[str, str, str, float, float, str], Any]
OrderBookFn = Callable[[], Sequence[Mapping[str, Any]]]

_BOOK_TIMESTAMP_FIELDS: Tuple[str, ...] = (
    "order_timestamp", "timestamp", "created_at", "transaction_time", "submitted_at",
)
_BOOK_CLIENT_KEY_FIELDS: Tuple[str, ...] = (
    "client_order_id", "clientOrderId", "tag", "tags", "orderTag", "user_remark",
)
#: Order-book statuses that state the broker refused the order.
_BOOK_TERMINAL_REJECT = frozenset({"rejected", "reject"})

#: Order-book statuses that report a failure without saying what happened to the
#: order. The entry is in the book, so the order exists and must never be
#: re-sent; but calling it REJECTED asserts more than the broker said, so these
#: escalate instead.
_BOOK_AMBIGUOUS_STATUS = frozenset({"error", "failed", "failure"})


class IdempotentOrderRouter:
    """Places orders exactly once across retries, timeouts, and restarts.

    The router owns three guarantees:

    * ``broker_send_fn`` is called **at most once per ``place_order`` call**.
    * It is not called at all while an earlier intent for the same key is
      unresolved, unless reconciliation proved the order ``ABSENT``.
    * Any outcome the broker did not state unambiguously becomes ``UNKNOWN``.

    One router serialises its sends behind a single lock, so placement latency
    is additive across a symbol universe. That is deliberate: reconciling an
    intent whose send is still in flight would find the order missing from the
    book and wrongly conclude ``ABSENT``. Run one router per broker session and
    shard by symbol group if throughput matters more than a shared ledger view.
    """

    def __init__(
        self,
        ledger: OrderLedger,
        alert_fn: Optional[Callable[[str], None]] = None,
        broker_echoes_key: bool = True,
        fuzzy_window_s: Optional[float] = 300.0,
        response_classifier: Optional[
            Callable[[Any], Tuple[OrderIntentStatus, Optional[str], str]]
        ] = None,
    ):
        """Configures the router for one broker.

        Args:
            ledger: Durable intent ledger.
            alert_fn: Called with a human-readable message whenever an intent is
                left unresolved and needs operator attention. Defaults to a
                warning log — wire it to a paging channel in production.
            broker_echoes_key: True only if the broker returns the client tag /
                client order id in its order book. This flag decides whether
                *absence* from the book counts as evidence. Set it False for
                brokers that accept a client tag but do not echo it back (ICICI
                Breeze's ``user_remark`` is reported to behave this way); the
                router then never concludes ``ABSENT`` and never re-sends.
            fuzzy_window_s: When ``broker_echoes_key`` is False, only book
                entries timestamped within this many seconds of the intent may
                be matched on ``(symbol, side, quantity, price)``. Setting it to
                None removes the time constraint and materially raises the
                chance of matching an unrelated identical order.
            response_classifier: Override for ``classify_broker_response``.
        """
        self.ledger = ledger
        self.alert_fn = alert_fn or (lambda msg: logger.warning("%s", msg))
        self.broker_echoes_key = broker_echoes_key
        self.fuzzy_window_s = fuzzy_window_s
        self.response_classifier = response_classifier or classify_broker_response
        self._send_lock = threading.RLock()

    # -- public API --------------------------------------------------------

    def place_order(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        signal_ts: Any,
        broker_send_fn: BrokerSendFn,
        broker_order_book_fn: Optional[OrderBookFn] = None,
        sequence: int = 0,
        max_key_len: int = 24,
    ) -> Tuple[bool, str, Optional[str]]:
        """Places one order idempotently.

        Args:
            strategy_id: Stable strategy instance id.
            symbol: Broker symbol.
            side: ``BUY``/``SELL``.
            quantity: Order quantity; must be > 0.
            price: Order price; must be >= 0 (``0`` for market orders).
            signal_ts: Signal timestamp, hashed into the idempotency key.
            broker_send_fn: ``(key, symbol, side, quantity, price, strategy_id)
                -> response``. Must attach ``key`` to the broker's client-tag
                field. Raising is interpreted as an *indeterminate* outcome.
            broker_order_book_fn: Returns the broker's current order book.
                Without it an ambiguous send can never be resolved and the
                intent is parked as ``UNKNOWN`` for an operator.
            sequence: Discriminator for legitimately identical repeat orders.
            max_key_len: Key length; must fit the broker's tag field.

        Returns:
            ``(ok, status, broker_order_id)``. ``status`` is one of ``PLACED``,
            ``ALREADY_PLACED``, ``RECONCILED_PLACED``, ``REJECTED: …``,
            ``ALREADY_REJECTED: …``, ``RECONCILED_REJECTED: …``,
            ``ABSENT_SAFE_TO_RESEND: …``, or
            ``UNRESOLVED_REQUIRES_RECONCILIATION: …``.

            When a prior intent exists and reconciliation proves the order
            absent at the broker, this call re-sends it and returns the outcome
            of that send (``PLACED``/``REJECTED: …``/…) — still one broker call.
            ``ABSENT_SAFE_TO_RESEND`` is therefore only returned when a send has
            already been made *in this call* and then proved not to have landed;
            the claim has been released, so calling ``place_order`` again with
            the same arguments issues the re-send.

        Raises:
            ValueError: If ``quantity <= 0`` or ``price < 0``.

        Note:
            ``ok=False`` with an ``UNRESOLVED_*`` status does **not** mean the
            order failed. It means the outcome is unknown. Do not treat it as a
            rejection, and do not re-issue the signal under a new key.
        """
        if quantity <= 0:
            raise ValueError("quantity must be > 0 (got %r)" % (quantity,))
        if price < 0:
            raise ValueError("price must be >= 0 (got %r)" % (price,))

        key = make_idempotency_key(
            strategy_id, symbol, side, signal_ts, quantity, price,
            sequence=sequence, max_len=max_key_len,
        )

        # The whole claim-then-send sequence is serialised: two threads racing
        # on one key must not both reach broker_send_fn.
        with self._send_lock:
            claimed = self.ledger.record_intent(
                key, strategy_id, symbol, side, quantity, price
            )
            if not claimed:
                return self._handle_existing_intent(
                    key, broker_send_fn, broker_order_book_fn
                )
            return self._dispatch(
                key, strategy_id, symbol, side, quantity, price,
                broker_send_fn, broker_order_book_fn,
            )

    def recover_unresolved(
        self,
        broker_order_book_fn: OrderBookFn,
        stale_after_s: float = 0.0,
    ) -> Dict[str, ReconcileResult]:
        """Startup sweep: reconciles every unresolved intent before trading resumes.

        Call this after a restart or reconnect and *before* the strategy is
        allowed to emit new signals. An intent left ``PENDING`` by a crash is an
        order that may or may not be live; generating fresh signals on top of it
        is how a restart doubles a position.

        Args:
            broker_order_book_fn: Returns the broker's current order book.
            stale_after_s: Only sweep intents older than this many seconds. 0
                sweeps everything, which is the right default at startup.

        Returns:
            Mapping of idempotency key to its ``ReconcileResult``. Any entry
            whose ``resolved`` is False still needs a human.
        """
        results: Dict[str, ReconcileResult] = {}
        now = time.time()
        # Held for the same reason place_order holds it: reconciling an intent
        # whose send is still in flight would see the order missing from the
        # book and wrongly conclude ABSENT.
        with self._send_lock:
            for key in self.ledger.unresolved():
                row = self.ledger.get_order(key)
                if row is None:  # pragma: no cover - concurrent deletion
                    continue
                if now - (row.get("created_at") or now) < stale_after_s:
                    continue
                results[key] = self._reconcile(key, broker_order_book_fn)

        unresolved = [k for k, r in results.items() if not r.resolved]
        if unresolved:
            self.alert_fn(
                "Startup reconciliation left %d intent(s) unresolved: %s — do not "
                "resume order generation until these are settled manually"
                % (len(unresolved), ", ".join(unresolved))
            )
        logger.info(
            "Startup reconciliation swept %d intent(s); %d unresolved",
            len(results), len(unresolved),
        )
        return results

    # -- internals ---------------------------------------------------------

    def _handle_existing_intent(
        self,
        key: str,
        broker_send_fn: BrokerSendFn,
        broker_order_book_fn: Optional[OrderBookFn],
    ) -> Tuple[bool, str, Optional[str]]:
        """Decides what to do when an intent for ``key`` already exists."""
        existing = self.ledger.get_order(key)
        if existing is None:  # pragma: no cover - row vanished between calls
            return False, "UNRESOLVED_REQUIRES_RECONCILIATION: intent row missing", None

        status = existing["status"]
        if status == OrderIntentStatus.PLACED.value:
            logger.info("Idempotent skip: %s already PLACED as %s", key, existing["broker_order_id"])
            return True, "ALREADY_PLACED", existing["broker_order_id"]
        if status == OrderIntentStatus.REJECTED.value:
            return False, "ALREADY_REJECTED: %s" % existing["rejection_reason"], None

        # PENDING or UNKNOWN: an earlier attempt's outcome is not known.
        # Re-sending here is the classic duplicate-order bug, so the order goes
        # out again only once reconciliation proves it is not at the broker.
        if broker_order_book_fn is None:
            msg = ("Intent %s is %s and no order book function was supplied; refusing "
                   "to re-send" % (key, status))
            self.alert_fn(msg)
            return False, "UNRESOLVED_REQUIRES_RECONCILIATION: %s" % msg, None

        result = self._reconcile(key, broker_order_book_fn)
        if result.outcome is ReconcileOutcome.FOUND_PLACED:
            return True, "RECONCILED_PLACED", result.broker_order_id
        if result.outcome is ReconcileOutcome.FOUND_REJECTED:
            return False, "RECONCILED_REJECTED: %s" % result.detail, None
        if result.outcome is ReconcileOutcome.ABSENT:
            # Proven absent: the order never reached the broker, reconciliation
            # released the claim, and re-sending under the same key is safe.
            # The send happens here rather than being handed back to the caller
            # as advice — no broker call has been made in *this* place_order
            # invocation, so issuing one now still means exactly one send per
            # call, and it is the only way the re-send ever actually happens.
            reclaimed = self.ledger.record_intent(
                key, existing["strategy_id"], existing["symbol"], existing["side"],
                existing["quantity"], existing["price"],
            )
            if not reclaimed:  # pragma: no cover - needs a second process
                msg = ("Intent %s was proven absent but another writer re-claimed the "
                       "key before it could be re-sent" % key)
                self.alert_fn(msg)
                return False, "UNRESOLVED_REQUIRES_RECONCILIATION: %s" % msg, None
            logger.info(
                "Intent %s proven absent at the broker; re-sending under the same key", key
            )
            return self._dispatch(
                key, existing["strategy_id"], existing["symbol"], existing["side"],
                existing["quantity"], existing["price"],
                broker_send_fn, broker_order_book_fn,
            )

        self.alert_fn("Intent %s remains unresolved: %s" % (key, result.detail))
        return False, "UNRESOLVED_REQUIRES_RECONCILIATION: %s" % result.detail, None

    def _dispatch(
        self,
        key: str,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        broker_send_fn: BrokerSendFn,
        broker_order_book_fn: Optional[OrderBookFn],
    ) -> Tuple[bool, str, Optional[str]]:
        """Issues exactly one broker call and classifies its outcome."""
        try:
            response = broker_send_fn(key, symbol, side, quantity, price, strategy_id)
        except Exception as exc:  # noqa: BLE001 - any transport failure is indeterminate
            # Deliberately broad: a client library can raise almost anything, and
            # every one of those cases means "outcome unknown", never "failed".
            # Narrowing this to timeouts would silently mishandle the rest.
            detail = "%s: %s" % (type(exc).__name__, exc)
            logger.warning("Indeterminate outcome for order %s: %s", key, detail)
            self.ledger.update_status(key, OrderIntentStatus.UNKNOWN, rejection_reason=detail)
            return self._resolve_unknown(key, detail, broker_order_book_fn)

        status, broker_order_id, detail = self.response_classifier(response)

        if status is OrderIntentStatus.PLACED:
            self.ledger.update_status(key, status, broker_order_id=broker_order_id)
            return True, "PLACED", broker_order_id
        if status is OrderIntentStatus.REJECTED:
            self.ledger.update_status(key, status, rejection_reason=detail)
            return False, "REJECTED: %s" % detail, None

        logger.warning("Indeterminate broker response for order %s: %s", key, detail)
        self.ledger.update_status(key, OrderIntentStatus.UNKNOWN, rejection_reason=detail)
        return self._resolve_unknown(key, detail, broker_order_book_fn)

    def _resolve_unknown(
        self,
        key: str,
        detail: str,
        broker_order_book_fn: Optional[OrderBookFn],
    ) -> Tuple[bool, str, Optional[str]]:
        if broker_order_book_fn is None:
            msg = ("Order %s is UNKNOWN (%s) and no order book function was supplied; it "
                   "must be reconciled manually before this strategy trades again"
                   % (key, detail))
            self.alert_fn(msg)
            return False, "UNRESOLVED_REQUIRES_RECONCILIATION: %s" % detail, None

        result = self._reconcile(key, broker_order_book_fn)
        if result.outcome is ReconcileOutcome.FOUND_PLACED:
            return True, "RECONCILED_PLACED", result.broker_order_id
        if result.outcome is ReconcileOutcome.FOUND_REJECTED:
            return False, "RECONCILED_REJECTED: %s" % result.detail, None
        if result.outcome is ReconcileOutcome.ABSENT:
            # A broker call has already been issued in this place_order
            # invocation, so the re-send is not made here — that would be two
            # sends in one call. Reconciliation released the claim, so the
            # caller's next place_order with the same arguments claims the key
            # afresh and sends exactly once.
            return False, "ABSENT_SAFE_TO_RESEND: %s" % result.detail, None

        self.alert_fn("Order %s unresolved after reconciliation: %s" % (key, result.detail))
        return False, "UNRESOLVED_REQUIRES_RECONCILIATION: %s" % result.detail, None

    def _reconcile(self, key: str, broker_order_book_fn: OrderBookFn) -> ReconcileResult:
        """Compares one intent against the broker order book.

        ``ABSENT`` is returned only when the broker echoes the client key, so
        that "not in the book" is actual evidence the order never landed. When
        the broker does not echo the key, a miss is ``INCONCLUSIVE`` — the order
        may be sitting there under an id this client cannot recognise.
        """
        intent = self.ledger.get_order(key)
        if intent is None:
            return ReconcileResult(ReconcileOutcome.INCONCLUSIVE, detail="unknown idempotency key")

        try:
            book = list(broker_order_book_fn())
        except Exception as exc:  # noqa: BLE001 - a failed query proves nothing
            logger.error("Order book query failed while reconciling %s: %s", key, exc)
            return ReconcileResult(
                ReconcileOutcome.INCONCLUSIVE,
                detail="order book query failed: %s: %s" % (type(exc).__name__, exc),
            )

        match = self._match_by_key(key, book)
        if match is None and not self.broker_echoes_key:
            match = self._match_by_attributes(intent, book)
            if match is None:
                return ReconcileResult(
                    ReconcileOutcome.INCONCLUSIVE,
                    detail=("broker does not echo client keys and no attribute match was "
                            "found; absence is not evidence here"),
                )

        if match is None:
            detail = "client key not present in broker order book"
            # Absence is proof the order never landed, so the claim on this key
            # is released here — and only here. Leaving the row in place would
            # make the "safe to re-send" verdict unusable: record_intent would
            # keep refusing the key and every re-invocation would loop back to
            # this same ABSENT.
            self._release_absent(key, detail)
            return ReconcileResult(ReconcileOutcome.ABSENT, detail=detail)

        return self._apply_match(key, match)

    def _apply_match(self, key: str, match: Mapping[str, Any]) -> ReconcileResult:
        book_status = str(match.get("status", "")).strip().lower()
        ids = _extract_order_ids(match)
        broker_id = ",".join(ids) if ids else None

        if book_status in _BOOK_TERMINAL_REJECT:
            reason = _extract_reason(match)
            if not self._safe_update(key, OrderIntentStatus.REJECTED, rejection_reason=reason):
                return ReconcileResult(
                    ReconcileOutcome.INCONCLUSIVE,
                    broker_id,
                    "broker reports REJECTED but the ledger row is already terminal",
                )
            logger.info("Reconciled %s -> REJECTED at broker (%s)", key, reason)
            return ReconcileResult(ReconcileOutcome.FOUND_REJECTED, broker_id, reason)

        if book_status in _BOOK_AMBIGUOUS_STATUS:
            return ReconcileResult(
                ReconcileOutcome.INCONCLUSIVE,
                broker_id,
                "book entry reports %r, which states neither working nor refused: %s"
                % (book_status, _extract_reason(match)),
            )

        if broker_id is None:
            # The order is in the book but carries no usable id — it exists, so
            # re-sending is unsafe, but it cannot be linked either.
            return ReconcileResult(
                ReconcileOutcome.INCONCLUSIVE,
                detail="matched a book entry that carries no broker order id",
            )

        if not self._safe_update(key, OrderIntentStatus.PLACED, broker_order_id=broker_id):
            return ReconcileResult(
                ReconcileOutcome.INCONCLUSIVE,
                broker_id,
                "broker reports the order working but the ledger row is already terminal",
            )
        logger.info("Reconciled %s -> broker order %s", key, broker_id)
        return ReconcileResult(
            ReconcileOutcome.FOUND_PLACED, broker_id, "matched in broker order book"
        )

    def _release_absent(self, key: str, detail: str) -> bool:
        """Releases a claim reconciliation proved absent, surfacing conflicts."""
        try:
            return self.ledger.release_intent(
                key, "reconciliation proved absent: %s" % detail
            )
        except IllegalStateTransition as exc:  # pragma: no cover - defensive
            # The row turned terminal between the reconcile read and this write.
            # The broker's book and the ledger now disagree; a human decides.
            logger.error("Release conflict for %s: %s", key, exc)
            self.alert_fn("Release conflict for %s: %s" % (key, exc))
            return False

    def _safe_update(self, key: str, status: OrderIntentStatus, **fields: Any) -> bool:
        """Applies a reconciliation transition, surfacing terminal-state conflicts.

        A ledger row that is already terminal while the broker reports something
        else is a genuine disagreement between two records of the same order. It
        must be escalated, not resolved by overwriting one of them.
        """
        try:
            return self.ledger.update_status(key, status, **fields)
        except IllegalStateTransition as exc:
            logger.error("Reconciliation conflict for %s: %s", key, exc)
            self.alert_fn("Reconciliation conflict for %s: %s" % (key, exc))
            return False

    @staticmethod
    def _client_keys(entry: Mapping[str, Any]) -> List[str]:
        """Every client-supplied tag a book entry echoes back."""
        values: List[str] = []
        for field in _BOOK_CLIENT_KEY_FIELDS:
            value = entry.get(field)
            if isinstance(value, str) and value:
                values.append(value)
            elif isinstance(value, (list, tuple)):
                values.extend(str(v) for v in value if v)
        return values

    def _match_by_key(
        self, key: str, book: Iterable[Mapping[str, Any]]
    ) -> Optional[Mapping[str, Any]]:
        for entry in book:
            if key in self._client_keys(entry):
                return entry
        return None

    def _match_by_attributes(
        self, intent: Mapping[str, Any], book: Iterable[Mapping[str, Any]]
    ) -> Optional[Mapping[str, Any]]:
        """Last-resort match for brokers that do not echo the client key.

        Deliberately strict: symbol, side, quantity *and* price must agree, the
        book entry must be timestamped inside ``fuzzy_window_s`` of the intent,
        and any broker order already linked to another intent is excluded. A
        looser match happily attaches an earlier identical order to this intent
        and reports a position the account does not hold.
        """
        claimed = self.ledger.linked_broker_order_ids()
        created_at = intent.get("created_at") or 0.0
        want_symbol = str(intent["symbol"]).strip().upper()
        want_side = str(intent["side"]).strip().upper()

        candidates: List[Mapping[str, Any]] = []
        for entry in book:
            if str(entry.get("symbol", "")).strip().upper() != want_symbol:
                continue
            if str(entry.get("side", "")).strip().upper() != want_side:
                continue
            # Written as `not (… <= tol)` so a non-numeric field, which yields
            # NaN, fails the test. `NaN > tol` is False and would let it pass.
            if not abs(self._as_float(entry.get("quantity")) - float(intent["quantity"])) <= 1e-6:
                continue
            if not abs(self._as_float(entry.get("price")) - float(intent["price"])) <= 1e-6:
                continue
            if any(oid in claimed for oid in _extract_order_ids(entry)):
                continue
            if self.fuzzy_window_s is not None:
                entry_ts = self._entry_timestamp(entry)
                if entry_ts is None:
                    logger.debug("Skipping untimestamped book entry during attribute match")
                    continue
                if abs(entry_ts - created_at) > self.fuzzy_window_s:
                    continue
            candidates.append(entry)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Two equally plausible orders: picking one would be a coin flip on
            # live capital. Escalate instead of guessing.
            logger.error(
                "Attribute reconciliation found %d equally plausible broker orders; "
                "refusing to guess", len(candidates),
            )
        return None

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return float("nan")

    @staticmethod
    def _entry_timestamp(entry: Mapping[str, Any]) -> Optional[float]:
        """Epoch seconds for a book entry, or None if it carries no usable stamp.

        Numeric values are read as **seconds**. A broker that reports
        milliseconds will land far outside any sane window and simply never
        match, which is the safe direction — normalise the units in your order
        book adapter rather than letting this guess.
        """
        for field in _BOOK_TIMESTAMP_FIELDS:
            value = entry.get(field)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, datetime):
                dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                return dt.timestamp()
        return None

    # Retained for callers that used the older private helper.
    def _reconcile_unknown(
        self, key: str, broker_order_book_fn: OrderBookFn
    ) -> Optional[str]:
        """Deprecated shim: the broker order id when reconciliation finds one placed."""
        result = self._reconcile(key, broker_order_book_fn)
        return result.broker_order_id if result.outcome is ReconcileOutcome.FOUND_PLACED else None
