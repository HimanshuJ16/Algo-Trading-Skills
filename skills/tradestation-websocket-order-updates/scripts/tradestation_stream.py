"""tradestation-websocket-order-updates: frame classifier, stall detector, fill
extractor, and gap-reconciliation planner for the TradeStation **v3** order stream.

Scope
-----
This module is an **offline frame classifier and reconciliation planner**. It does
no network I/O: you hand it the raw text of a stream frame or a decoded REST order
payload, and it hands back a typed result plus the audit facts needed to decide
what to do next. Keeping it I/O-free is what makes every rule here deterministic
and unit-testable. Transport (the HTTP request, TLS, chunk buffering) and the
reconnect loop itself belong to the caller; see
``websocket-reconnection-with-state-recovery`` for backoff/jitter.

The transport is HTTP chunked streaming, not WebSocket
------------------------------------------------------
Despite this skill's slug, TradeStation does **not** expose a WebSocket for order
updates. It uses RFC2616 HTTP/1.1 chunked streaming — a normal ``GET`` whose
response body never ends::

    GET /v3/brokerage/stream/accounts/{accounts}/orders
    Authorization: Bearer {access_token}

    Transfer-Encoding: chunked
    Content-Type: application/vnd.tradestation.streams.v3+json

TradeStation's own documentation warns that HTTP chunk boundaries are **not**
application message boundaries: proxies re-chunk freely, so one chunk may carry
several JSON objects and one JSON object may be split across chunks. The caller
must reassemble complete JSON values before calling :meth:`classify_frame`;
feeding it a half object yields ``FRAME_MALFORMED``, not an order.

Order streaming is v3-only. There is no ``/v2/stream/orders`` endpoint — the
published v2 specification streams only barcharts, quotes and tickbars, and
TradeStation's streaming documentation states that the ``v3+json`` stream
content type is what orders and positions are served with.

Updates are cumulative snapshots, not deltas
--------------------------------------------
This is the single most important semantic on this stream, and getting it wrong
is what corrupts positions. Every order frame is the **whole current state of the
order**, so ``Legs[].ExecQuantity`` is the cumulative quantity executed so far,
not the size of the latest execution. A partially filled order emits repeated
``FPR`` frames whose ExecQuantity climbs; the delta between two frames is the new
execution.

Two consequences the caller must respect:

* Apply order state by **assignment**, never by ``+=``. A ledger that adds
  ExecQuantity on each frame double-counts on the very first reconnect, because
  the stream replays a snapshot of current orders on connect (terminated by
  ``{"StreamStatus": "EndSnapshot"}``).
* Because assignment is idempotent, deduplication here is a noise filter and a
  cost saver — it is *not* the correctness mechanism. Never rely on this
  module's signature set as the only thing standing between you and a
  double-counted fill.

Deduplication is at-least-once, and the caller closes the loop
--------------------------------------------------------------
:meth:`is_duplicate` is a **pure query**. Nothing is remembered until the caller
calls :meth:`mark_processed`, and the caller must do that only *after* the event
is durably applied::

    update = mgr.parse_stream_message(line)
    if update is not None and not mgr.is_duplicate(update):
        ledger.apply(update)        # may raise
        mgr.mark_processed(update)  # only reached if apply() succeeded

Marking before applying converts the stream into at-most-once delivery: a crash
between the two loses the event permanently, because the REST catch-up on the
next reconnect would suppress it as a duplicate.

Gap reconciliation cannot use a Unix timestamp
----------------------------------------------
There is no "give me everything since epoch-seconds N" call in this API:

* ``GET /v3/brokerage/accounts/{accounts}/orders`` returns today's orders and
  open orders. It takes **no** ``since`` parameter at all.
* ``GET /v3/brokerage/accounts/{accounts}/historicalorders`` takes ``since`` as a
  **date** (e.g. ``2006-01-13``), is limited to 90 days, and **excludes open
  orders**.

So an intraday reconnect must query both and reconcile client-side.
:meth:`catch_up_since_date` produces the ``since`` value in the accepted format,
derived from broker-supplied event times rather than the local clock, and clamped
to the 90-day window. See ``references/standards.md`` for the sourced parameter
tables.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Endpoints -------------------------------------------------------------
# The environment is selected by the HOST, not by the account id. Pointing a
# paper account id at the live host trades real money.
LIVE_BASE_URL = "https://api.tradestation.com"
SIM_BASE_URL = "https://sim-api.tradestation.com"

STREAM_ORDERS_PATH = "/v3/brokerage/stream/accounts/{accounts}/orders"
ORDERS_PATH = "/v3/brokerage/accounts/{accounts}/orders"
HISTORICAL_ORDERS_PATH = "/v3/brokerage/accounts/{accounts}/historicalorders"
STREAM_CONTENT_TYPE = "application/vnd.tradestation.streams.v3+json"

# --- Documented API limits (see references/standards.md) --------------------
#: TradeStation sends a heartbeat after 5 seconds on an idle stream.
HEARTBEAT_IDLE_SECONDS = 5.0
#: Three missed heartbeats. Silence longer than this means the socket is hung.
DEFAULT_STALL_THRESHOLD_SECONDS = 15.0
#: ``historicalorders?since=`` is limited to 90 days prior to the current date.
MAX_HISTORICAL_LOOKBACK_DAYS = 90
#: Maximum (and default) ``pageSize`` on the order endpoints.
MAX_ORDERS_PER_PAGE = 600
#: Concurrent open order streams permitted per authenticated user.
ORDER_STREAM_CONCURRENCY_LIMIT = 40
#: Request quota, 5-minute rolling interval, for the order-details resource.
ORDER_DETAILS_QUOTA_PER_5_MIN = 320
#: ``since`` is accepted as a date; this is the unambiguous ISO form.
SINCE_DATE_FORMAT = "%Y-%m-%d"

# --- Frame kinds -----------------------------------------------------------
FRAME_ORDER = "ORDER"
FRAME_HEARTBEAT = "HEARTBEAT"
FRAME_STREAM_STATUS = "STREAM_STATUS"
FRAME_ERROR = "ERROR"
FRAME_EMPTY = "EMPTY"
FRAME_MALFORMED = "MALFORMED"
FRAME_UNKNOWN = "UNKNOWN"

STREAM_STATUS_END_SNAPSHOT = "EndSnapshot"
STREAM_STATUS_GO_AWAY = "GoAway"

# --- Order status codes ----------------------------------------------------
#: The documented v3 ``Status`` enum, code -> description.
ORDER_STATUS_DESCRIPTIONS: Mapping[str, str] = {
    "ACK": "Received",
    "BRO": "Broken",
    "CAN": "Canceled",
    "CND": "Condition Met",
    "DON": "Queued",
    "EXP": "Expired",
    "FLL": "Filled",
    "FLP": "Partial Fill (UROut)",
    "FPR": "Partial Fill (Alive)",
    "LAT": "Too Late to Cancel",
    "OPN": "Sent",
    "OSO": "OSO Order",
    "OUT": "UROut",
    "REJ": "Rejected",
    "RJC": "Cancel Request Rejected",
    "RSN": "Replace Sent",
    "SUS": "Suspended",
    "TSC": "Trade Server Canceled",
    "UCH": "Replaced",
    "UCN": "Cancel Sent",
}

#: Statuses after which the order can execute no further quantity. ``FLP`` is
#: terminal because the unfilled remainder was cancelled ("Partial Fill (UROut)")
#: whereas ``FPR`` ("Partial Fill (Alive)") is still working. ``LAT``, ``RJC``,
#: ``UCN``, ``RSN`` and ``UCH`` describe cancel/replace attempts, not the end of
#: the order, so they are deliberately excluded.
TERMINAL_ORDER_STATUSES = frozenset({"BRO", "CAN", "EXP", "FLL", "FLP", "OUT", "REJ", "TSC"})

#: Statuses that carry executed quantity.
FILL_BEARING_STATUSES = frozenset({"FLL", "FLP", "FPR"})

_DEFAULT_MAX_TRACKED_SIGNATURES = 10_000

#: Matches the fractional-seconds group of an RFC3339 timestamp.
_SUBSECOND_RE = re.compile(r"\.\d+")


class TradeStationStreamError(RuntimeError):
    """Raised when the stream must be torn down or catch-up could not run.

    Raised for ``GoAway`` and for server error frames — both of which oblige the
    client to end the HTTP request and open a fresh stream — and when the REST
    catch-up callable fails.
    """


@dataclass(frozen=True)
class OrderLegFill:
    """One leg of an order, with cumulative execution state for that leg."""

    symbol: str
    asset_type: str
    buy_or_sell: str
    quantity_ordered: Decimal
    exec_quantity: Decimal
    quantity_remaining: Decimal
    execution_price: Decimal


@dataclass(frozen=True)
class TradeStationOrderUpdate:
    """A single cumulative snapshot of one order.

    ``filled_quantity`` is the sum of ``ExecQuantity`` across legs and is only a
    meaningful scalar for single-leg orders; multi-leg strategies must account
    per leg via :attr:`legs`. ``average_price`` is the order's ``FilledPrice``,
    which TradeStation documents as the average fill price at the top level.
    Quantities and prices are :class:`~decimal.Decimal` — binary floats do not
    belong in a fill ledger.
    """

    order_id: str
    status: str
    filled_quantity: Decimal
    average_price: Decimal
    signature: str
    account_id: str = ""
    status_description: str = ""
    reject_reason: str = ""
    legs: Tuple[OrderLegFill, ...] = ()
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    #: Local wall-clock receipt time. Never use this as a broker event time.
    timestamp: float = field(default_factory=time.time)

    @property
    def is_terminal(self) -> bool:
        """True when the order can execute no further quantity."""
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def broker_event_utc(self) -> Optional[datetime]:
        """Best broker-supplied event time: close time if closed, else open time."""
        return self.closed_at or self.opened_at


@dataclass(frozen=True)
class StreamFrame:
    """Classification of one raw stream frame."""

    kind: str
    order: Optional[TradeStationOrderUpdate] = None
    stream_status: str = ""
    heartbeat_at: Optional[datetime] = None
    error: str = ""
    message: str = ""
    detail: str = ""

    @property
    def requires_reconnect(self) -> bool:
        """True when TradeStation has told the client to end this stream.

        ``GoAway`` announces a server shutdown and an error frame obliges the
        client to terminate the HTTP request; in both cases the only correct
        response is to open a new stream (after a backoff delay) and run REST
        catch-up over the gap.
        """
        return self.kind == FRAME_ERROR or (
            self.kind == FRAME_STREAM_STATUS and self.stream_status == STREAM_STATUS_GO_AWAY
        )


def _to_decimal(raw: Any, field_name: str, order_id: str = "") -> Decimal:
    """Coerce a TradeStation numeric field to Decimal.

    Every numeric on this API arrives as a JSON *string*. Missing, empty and
    unparseable values become ``Decimal("0")`` with a warning rather than
    raising, because one malformed field must not kill a live stream loop.

    Non-finite values are rejected the same way. This is not theoretical:
    ``json.loads`` accepts the bare literals ``NaN`` and ``Infinity`` by default,
    and ``Decimal`` parses the strings ``"NaN"`` and ``"Infinity"`` happily, so
    without this guard a single hostile or corrupt field turns a position
    quantity into NaN and poisons every sum downstream of it.
    """
    if raw is None or raw == "":
        return Decimal("0")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        logger.warning(
            "Unparseable numeric field %r=%r on order %r; treating as 0",
            field_name, raw, order_id,
        )
        return Decimal("0")
    if not value.is_finite():
        logger.warning(
            "Non-finite numeric field %r=%r on order %r; treating as 0",
            field_name, raw, order_id,
        )
        return Decimal("0")
    return value


def _normalize(value: Decimal) -> str:
    """Render a Decimal canonically so "100", "100.0" and "100.00" agree."""
    return format(value.normalize(), "f")


def _parse_rfc3339(raw: Any) -> Optional[datetime]:
    """Parse an RFC3339 timestamp into an aware UTC datetime, or None.

    Fractional seconds are truncated to microseconds first: RFC3339 permits any
    number of digits, ``datetime`` accepts at most six, and a failed parse here
    silently costs the catch-up window its anchor.
    """
    if not isinstance(raw, str) or not raw:
        return None
    text = _SUBSECOND_RE.sub(lambda m: m.group(0)[:7], raw.strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Unparseable RFC3339 timestamp %r", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_legs(raw_legs: Any, order_id: str) -> Tuple[OrderLegFill, ...]:
    if not isinstance(raw_legs, SequenceABC) or isinstance(raw_legs, (str, bytes)):
        return ()
    legs: List[OrderLegFill] = []
    for raw in raw_legs:
        if not isinstance(raw, MappingABC):
            continue
        legs.append(
            OrderLegFill(
                symbol=str(raw.get("Symbol", "")),
                asset_type=str(raw.get("AssetType", "")),
                buy_or_sell=str(raw.get("BuyOrSell", "")),
                quantity_ordered=_to_decimal(raw.get("QuantityOrdered"), "QuantityOrdered", order_id),
                exec_quantity=_to_decimal(raw.get("ExecQuantity"), "ExecQuantity", order_id),
                quantity_remaining=_to_decimal(raw.get("QuantityRemaining"), "QuantityRemaining", order_id),
                execution_price=_to_decimal(raw.get("ExecutionPrice"), "ExecutionPrice", order_id),
            )
        )
    return tuple(legs)


def build_order_update(payload: Mapping[str, Any]) -> Optional[TradeStationOrderUpdate]:
    """Build an order update from a decoded v3 Order object.

    Works for both stream frames and REST order rows — they share one schema.
    Returns ``None`` when the payload carries no ``OrderID``, which is the only
    field this module treats as mandatory.
    """
    order_id = str(payload.get("OrderID", "") or "")
    if not order_id:
        return None

    status = str(payload.get("Status", "") or "")
    if status and status not in ORDER_STATUS_DESCRIPTIONS:
        # Do not drop it: an unrecognised status may still carry executed
        # quantity, and silently discarding it would lose a fill.
        logger.warning("Unrecognised order status %r on order %r", status, order_id)

    legs = _parse_legs(payload.get("Legs"), order_id)
    filled_quantity = sum((leg.exec_quantity for leg in legs), Decimal("0"))
    average_price = _to_decimal(payload.get("FilledPrice"), "FilledPrice", order_id)

    return TradeStationOrderUpdate(
        order_id=order_id,
        status=status,
        filled_quantity=filled_quantity,
        average_price=average_price,
        signature=build_signature(order_id, status, legs, average_price),
        account_id=str(payload.get("AccountID", "") or ""),
        status_description=str(payload.get("StatusDescription", "") or ""),
        reject_reason=str(payload.get("RejectReason", "") or ""),
        legs=legs,
        opened_at=_parse_rfc3339(payload.get("OpenedDateTime")),
        closed_at=_parse_rfc3339(payload.get("ClosedDateTime")),
    )


def build_signature(
    order_id: str,
    status: str,
    legs: Sequence[OrderLegFill],
    average_price: Decimal,
) -> str:
    """Compose the deduplication key for one cumulative order snapshot.

    Per-leg executed quantity is included, not just a summed total: a two-leg
    order whose legs fill 1/9 and then 9/1 has an unchanged total and must not
    collapse to one event. Values are normalised decimal strings so that the
    same state rendered as ``"100"`` by the stream and ``"100.00"`` by REST
    produces one key rather than two, and legs are sorted so that the same state
    does not yield two keys merely because two endpoints ordered the legs
    differently.
    """
    leg_part = ";".join(
        sorted(f"{leg.symbol}@{_normalize(leg.exec_quantity)}" for leg in legs)
    )
    return f"{order_id}|{status}|{leg_part}|{_normalize(average_price)}"


class TradeStationStreamManager:
    """Classifies v3 order stream frames, detects stalls, and plans catch-up.

    Not thread-safe. Drive one instance from the single task that owns the
    stream; hand reconciled updates to other threads through a queue.
    """

    def __init__(
        self,
        account_id: str,
        *,
        max_tracked_signatures: int = _DEFAULT_MAX_TRACKED_SIGNATURES,
        stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not account_id:
            raise ValueError("account_id is required")
        if max_tracked_signatures < 1:
            raise ValueError("max_tracked_signatures must be >= 1")
        if stall_threshold_seconds <= HEARTBEAT_IDLE_SECONDS:
            raise ValueError(
                "stall_threshold_seconds must exceed the documented "
                f"{HEARTBEAT_IDLE_SECONDS}s heartbeat interval"
            )
        self.account_id = account_id
        self.max_tracked_signatures = max_tracked_signatures
        self.stall_threshold_seconds = stall_threshold_seconds
        self.is_connected: bool = False
        self._monotonic = monotonic
        # Ordered set; oldest signature is evicted first once the cap is hit.
        self._processed: "OrderedDict[str, None]" = OrderedDict()
        self._last_frame_at: float = monotonic()
        self._last_broker_event_utc: Optional[datetime] = None

    # -- connection state ---------------------------------------------------

    def mark_connected(self) -> None:
        """Record a successful (re)connect and reset the stall timer."""
        self.is_connected = True
        self._last_frame_at = self._monotonic()
        logger.info("Order stream connected for account %s", self.account_id)

    def mark_disconnected(self) -> None:
        """Record that the stream is down. Catch-up is owed on reconnect."""
        self.is_connected = False
        logger.warning("Order stream disconnected for account %s", self.account_id)

    # -- stall detection ----------------------------------------------------

    def seconds_since_last_frame(self) -> float:
        """Seconds since any frame — including a heartbeat — was last seen.

        Measured on a monotonic clock, so an NTP step or a DST change cannot
        fabricate or mask a stall.
        """
        return self._monotonic() - self._last_frame_at

    def is_stream_stalled(self) -> bool:
        """True when silence has exceeded the stall threshold.

        TradeStation emits a heartbeat after 5 seconds of an idle stream, so
        prolonged silence means the connection is hung rather than quiet — the
        failure mode a TCP socket will not report on its own. The caller should
        tear the stream down and reconnect, not wait longer.
        """
        return self.seconds_since_last_frame() > self.stall_threshold_seconds

    # -- deduplication ------------------------------------------------------

    @property
    def tracked_signature_count(self) -> int:
        return len(self._processed)

    def is_duplicate(self, update: TradeStationOrderUpdate) -> bool:
        """Pure query: has this exact order state already been committed?

        Records nothing. Call :meth:`mark_processed` *after* the update has been
        durably applied, so that a failure in between leaves the event
        recoverable via REST catch-up.
        """
        return update.signature in self._processed

    def mark_processed(self, update: TradeStationOrderUpdate) -> None:
        """Commit an update as applied, evicting the oldest entry past the cap.

        Eviction is safe because order frames are cumulative snapshots and a
        correct ledger applies them by assignment: re-applying an evicted state
        is a no-op, not a double count.
        """
        self._processed[update.signature] = None
        self._processed.move_to_end(update.signature)
        while len(self._processed) > self.max_tracked_signatures:
            evicted, _ = self._processed.popitem(last=False)
            logger.debug("Evicted oldest tracked signature %s", evicted)

    # -- broker event time --------------------------------------------------

    @property
    def last_broker_event_utc(self) -> Optional[datetime]:
        """Latest broker-supplied event time observed, or None.

        Sourced from ``ClosedDateTime``/``OpenedDateTime`` on orders and from the
        heartbeat ``Timestamp``. The local clock is never used here: catch-up
        queries are evaluated against the broker's clock, so local skew would
        silently narrow the recovery window.
        """
        return self._last_broker_event_utc

    def _observe_broker_time(self, moment: Optional[datetime]) -> None:
        if moment is None:
            return
        if self._last_broker_event_utc is None or moment > self._last_broker_event_utc:
            self._last_broker_event_utc = moment

    def catch_up_since_date(self, *, now: Optional[datetime] = None) -> str:
        """Return the ``since`` value for ``historicalorders``, as ``YYYY-MM-DD``.

        Date granularity is the API's, not a simplification: ``since`` accepts a
        date and nothing finer, so catch-up necessarily re-reads whole days and
        relies on deduplication plus idempotent application to absorb the
        overlap. The value is clamped into the documented 90-day window; a gap
        older than that cannot be recovered through this endpoint and is logged
        as such.

        With no broker-supplied anchor yet -- a cold start -- this reaches back
        one day rather than starting at today, because ``/orders`` covers only
        today and an order filled in the previous session's extended hours would
        otherwise fall through both endpoints.
        """
        today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
        floor: date = today - timedelta(days=MAX_HISTORICAL_LOOKBACK_DAYS)

        anchor = self._last_broker_event_utc
        if anchor is None:
            # Cold start, or every observed timestamp failed to parse: we have no
            # idea how long we were down. Reach back one day so a fill from the
            # previous session's extended hours stays recoverable -- one extra
            # page, absorbed by deduplication.
            since = today - timedelta(days=1)
        else:
            since = anchor.date()
        if since > today:
            # Broker time ahead of ours, or a bad parse. Never query the future.
            logger.warning("Last broker event %s is ahead of today %s; clamping", since, today)
            since = today
        if since < floor:
            logger.error(
                "Gap since %s exceeds the %d-day historicalorders window; "
                "orders closed before %s cannot be recovered via this endpoint",
                since, MAX_HISTORICAL_LOOKBACK_DAYS, floor,
            )
            since = floor
        return since.strftime(SINCE_DATE_FORMAT)

    # -- frame classification ----------------------------------------------

    def classify_frame(self, message_str: str) -> StreamFrame:
        """Classify one complete JSON frame from the order stream.

        The caller must have reassembled a whole JSON value first: TradeStation
        warns that HTTP chunk boundaries are not message boundaries, so a
        fragment reaching this method is a framing bug and is reported as
        ``FRAME_MALFORMED`` rather than guessed at.

        Every frame — heartbeats included — resets the stall timer, which is the
        entire point of heartbeats.
        """
        self._last_frame_at = self._monotonic()

        if not isinstance(message_str, str):
            return StreamFrame(kind=FRAME_MALFORMED, detail="frame was not a string")

        text = message_str.strip()
        if not text:
            return StreamFrame(kind=FRAME_EMPTY)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Undecodable stream frame (chunk reassembly bug?): %s", exc)
            return StreamFrame(kind=FRAME_MALFORMED, detail=str(exc))

        if not isinstance(data, MappingABC):
            return StreamFrame(kind=FRAME_MALFORMED, detail="frame was not a JSON object")

        # Control frames first: an error frame carries AccountID, not OrderID,
        # and must never be mistaken for order data.
        if "Heartbeat" in data:
            moment = _parse_rfc3339(data.get("Timestamp"))
            self._observe_broker_time(moment)
            logger.debug("Heartbeat %s", data.get("Heartbeat"))
            return StreamFrame(kind=FRAME_HEARTBEAT, heartbeat_at=moment)

        if "StreamStatus" in data:
            status = str(data.get("StreamStatus", "") or "")
            if status == STREAM_STATUS_GO_AWAY:
                logger.warning("Server sent GoAway; stream must be restarted")
            elif status == STREAM_STATUS_END_SNAPSHOT:
                logger.info("Initial order snapshot complete")
            return StreamFrame(kind=FRAME_STREAM_STATUS, stream_status=status)

        if "Error" in data:
            error = str(data.get("Error", "") or "")
            message = str(data.get("Message", "") or "")
            logger.error("Order stream error frame: %s - %s", error, message)
            return StreamFrame(kind=FRAME_ERROR, error=error, message=message)

        update = build_order_update(data)
        if update is None:
            logger.warning("Frame carried no OrderID and no control key; ignoring")
            return StreamFrame(kind=FRAME_UNKNOWN, detail="no OrderID")

        self._observe_broker_time(update.broker_event_utc)
        return StreamFrame(kind=FRAME_ORDER, order=update)

    def parse_stream_message(self, message_str: str) -> Optional[TradeStationOrderUpdate]:
        """Convenience wrapper returning only new order updates.

        Returns ``None`` for heartbeats, stream-status frames, empty or
        malformed frames, and for an order state already committed via
        :meth:`mark_processed`.

        Raises :class:`TradeStationStreamError` on ``GoAway`` and on server error
        frames. That is deliberate: both oblige the client to end the HTTP
        request, and returning ``None`` for them — indistinguishable from a
        heartbeat — is how a bot silently stops receiving fills while believing
        it is connected. Callers that would rather branch than catch should use
        :meth:`classify_frame` and check
        :attr:`StreamFrame.requires_reconnect`.

        The returned update is **not** committed. Apply it, then call
        :meth:`mark_processed`.
        """
        frame = self.classify_frame(message_str)

        if frame.kind == FRAME_ERROR:
            raise TradeStationStreamError(
                f"Order stream error frame: {frame.error} - {frame.message}"
            )
        if frame.kind == FRAME_STREAM_STATUS and frame.stream_status == STREAM_STATUS_GO_AWAY:
            raise TradeStationStreamError(
                "Server sent GoAway; terminate this stream, reconnect, and run REST catch-up"
            )
        if frame.kind != FRAME_ORDER or frame.order is None:
            return None

        update = frame.order
        if self.is_duplicate(update):
            logger.debug("Duplicate order state skipped: %s", update.signature)
            return None
        logger.info(
            "Order update: id=%s status=%s filled=%s avg_price=%s",
            update.order_id, update.status, update.filled_quantity, update.average_price,
        )
        return update

    # -- gap reconciliation -------------------------------------------------

    def reconcile_missed_orders(
        self, rest_fetch_fn: Callable[[str], Iterable[Mapping[str, Any]]]
    ) -> List[TradeStationOrderUpdate]:
        """Return order states missed while the stream was down.

        ``rest_fetch_fn`` receives the ``since`` **date string** from
        :meth:`catch_up_since_date` and must return the union of:

        * ``GET /v3/brokerage/accounts/{accounts}/orders`` — today's and open
          orders; this endpoint takes no ``since`` parameter, so pass none, and
        * ``GET /v3/brokerage/accounts/{accounts}/historicalorders?since=<date>``
          — closed orders, which the first endpoint omits.

        The callable owns pagination. Both endpoints cap a page at 600 orders
        and return a ``nextToken`` valid for one hour; a fetch that ignores it
        silently truncates recovery at 600 and drops the rest of the gap on the
        floor. See ``references/workflows.md``.

        Nothing is committed here. Apply each returned update, then call
        :meth:`mark_processed` for it — the whole point of leaving the commit to
        the caller is that a crash mid-apply must leave the event recoverable on
        the next reconnect.
        """
        since = self.catch_up_since_date()
        logger.info("Running REST catch-up for account %s since %s", self.account_id, since)
        try:
            rows = rest_fetch_fn(since)
            if rows is None:
                raise TradeStationStreamError(
                    "REST catch-up returned None; expected a sequence of orders"
                )
            # Materialise inside the guard: a caller-supplied generator raises
            # during iteration, not at the call, and a half-consumed recovery
            # batch must not escape as a bare transport exception.
            rows = list(rows)
        except TradeStationStreamError:
            raise
        except Exception as exc:  # noqa: BLE001 - callback is caller-supplied
            raise TradeStationStreamError(f"REST catch-up query failed: {exc}") from exc

        reconciled: List[TradeStationOrderUpdate] = []
        batch_seen: set = set()
        for raw in rows:
            if not isinstance(raw, MappingABC):
                logger.warning("Skipping non-object row in REST catch-up payload: %r", type(raw))
                continue
            update = build_order_update(raw)
            if update is None:
                logger.warning("Skipping REST catch-up row with no OrderID")
                continue
            # The two endpoints overlap, so the same order can appear twice in
            # one batch; de-duplicate within the batch as well as against
            # already-committed state.
            if update.signature in batch_seen or self.is_duplicate(update):
                continue
            batch_seen.add(update.signature)
            self._observe_broker_time(update.broker_event_utc)
            reconciled.append(update)
            logger.info(
                "Recovered missed order state: id=%s status=%s filled=%s",
                update.order_id, update.status, update.filled_quantity,
            )

        logger.info("Catch-up complete: %d missed order states to apply.", len(reconciled))
        return reconciled
