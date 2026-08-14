"""
broker-api-idempotent-cancel-requests: idempotent order-cancel dispatcher that
de-duplicates cancel retries, classifies Cancel-vs-Fill race responses, and — above
all — refuses to report an indeterminate outcome as a determinate one.

The governing rule of this module is that **a cancel request is a request, not an
outcome.** Every broker consulted while writing this says so in its own words:

  - FIX 4.4 defines ``OrdStatus`` 6 = "Pending Cancel (e.g. result of Order Cancel
    Request <F>)" as a state distinct from 4 = "Canceled". The cancel request is
    acknowledged; only a subsequent ``ExecutionReport`` (35=8) moves the order to 4.
  - Alpaca's ``pending_cancel`` is documented as "The order is waiting to be
    canceled", and its docs state orders "will remain in pending_cancel until
    canceled by the execution venue that Alpaca routed the order to for execution".
    The ``DELETE`` returns 204 well before that happens.
  - Zerodha Kite Connect: "Successful placement of an order via the API does not
    imply its successful execution", and it directs clients to postbacks for the
    actual state transition.

So an HTTP 2xx on a cancel means ``PENDING_CANCEL``, not ``CANCELLED``. Callers that
free capital, decrement exposure, or place a replacement order on a 2xx are acting on
an order that can still fill. Brokers whose cancel endpoint *is* synchronous (Binance
``DELETE /api/v3/order`` returns the order already in state ``CANCELED``) can opt in
with ``treat_ack_as_cancelled=True``.

The second rule is that **an indeterminate outcome must never be cached as terminal.**
Binance's REST documentation is explicit about the 5xx case: "It is important to NOT
treat this as a failure operation; the execution status is UNKNOWN and could have been
a success." A manager that classifies an exhausted retry sequence as a terminal failure
*and stores it in the idempotency cache* converts one lost response into a permanently
un-cancellable order: every later retry under the same ``client_cancel_id`` returns the
cached failure without ever reaching the broker again. Only outcomes the broker
actually asserted are cached (see ``_TERMINAL_STATUSES`` / ``_CACHEABLE_STATUSES``);
``UNKNOWN`` and ``ORDER_UNKNOWN`` are deliberately re-dispatchable.

The third rule is that **classification errors are asymmetric.** Reporting a live order
as dead (``FILLED_BEFORE_CANCEL`` / ``ALREADY_CANCELLED``) stops the caller from ever
trying again and leaves working exposure in the market; reporting a dead order as
indeterminate only costs a reconciliation query. Every ambiguous case therefore
resolves toward "reconcile", never toward "terminal". Concretely, an HTTP 404 or a
Binance ``-2011``/``-2013`` "unknown order" is *not* treated as proof the order is
cancelled — the same error is returned when the request carries the wrong API key,
symbol, or order id, in which case the order is still live.

Scope limits:

  - **This module does not reconcile.** It classifies one cancel dispatch and tells the
    caller, via ``CancelResult.requires_reconciliation``, when the broker's order-state
    stream (``ExecutionReport``, postback, or an order-status query) is the only thing
    that can settle the outcome. Owning that ledger belongs to
    ``order-placement-idempotency`` and ``webhook-based-order-fill-notifications``.
  - **The cache is in-memory and per-process.** It does not survive a restart and is not
    shared across replicas or hosts. Cross-restart cancel de-duplication requires a
    durable intent ledger.
  - **Text classification is a heuristic over free-text broker error strings.** The
    default patterns are conservative and constructor-overridable per broker; a broker
    that returns a machine-readable reason code should be classified on that code by
    supplying custom patterns or a wrapper transport.
  - **It never raises into the caller.** It runs on the same thread as live risk
    reduction; a manager that throws there turns "the cancel is uncertain" into "the
    trading loop is dead".

References:
  - FIX 4.4, ``OrdStatus`` (tag 39) values 4 (Canceled) and 6 (Pending Cancel);
    ``CxlRejReason`` (tag 102) value 0 ("Too late to cancel"), 1 ("Unknown order"),
    3 ("Order already in Pending Cancel or Pending Replace status").
  - Alpaca Trading API, ``DELETE /v2/orders/{order_id}`` (204 accepted / 422 "The
    order status is not cancelable.") and the ``pending_cancel`` order status.
  - Binance Spot REST general API information (4XX/429/418/5XX semantics,
    ``Retry-After`` on 418 and 429) and error codes -2011 / -2013.
  - Zerodha Kite Connect v3, order cancellation and postbacks.
  - RFC 9110 Section 10.2.3, ``Retry-After`` (delay-seconds **or** HTTP-date).
"""
import email.utils
import logging
import random
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timezone
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Pattern, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: HTTP statuses worth re-dispatching the same ``client_cancel_id`` for. 5xx is
#: indeterminate rather than failed (Binance: "the execution status is UNKNOWN and
#: could have been a success"). 408/429 are transient by definition. 418 is Binance's
#: IP auto-ban for continuing to send requests after 429s; it is listed here so its
#: ``Retry-After`` is read, but its wait almost always exceeds the retry budget and
#: exits the loop rather than sleeping through a ban.
_RETRYABLE_STATUSES: Set[int] = {408, 418, 429}

#: Broker error text asserting the order filled before the cancel landed. The negative
#: lookbehind keeps "order was not filled" and "unfilled" out, and "partially filled"
#: is deliberately absent: a partial fill does not mean the order is gone, and
#: reporting it as ``FILLED_BEFORE_CANCEL`` would overstate the executed quantity.
DEFAULT_FILLED_PATTERNS: Tuple[str, ...] = (
    r"too late to cancel",
    r"already\s+(?:been\s+)?(?:filled|executed|traded)",
    r"(?<!not\s)(?<!partially\s)\bfilled\b",
    r"\bexecuted\b",
    r"order\s+is\s+complete",
)

#: Broker error text positively asserting the order is already in a cancelled state.
#: "Not found" is NOT here — absence of an order is not an assertion that it was
#: cancelled (see ``DEFAULT_UNKNOWN_ORDER_PATTERNS``).
DEFAULT_ALREADY_CANCELLED_PATTERNS: Tuple[str, ...] = (
    r"already\s+(?:been\s+)?(?:cancell?ed|canceled)",
    r"order\s+(?:is|was)\s+cancell?ed",
    r"cancell?ed\s+order",
    r"pending\s+cancel",
)

#: Broker error text meaning "I cannot see this order". Ambiguous by construction:
#: Binance returns -2011/-2013 for an order that is gone *and* for a request sent with
#: the wrong API key, symbol, or id — where the order is still live and working.
DEFAULT_UNKNOWN_ORDER_PATTERNS: Tuple[str, ...] = (
    r"unknown\s+order",
    r"order\s+does\s+not\s+exist",
    r"no\s+such\s+order",
    r"not\s+found",
    r"invalid\s+order\s*(?:id)?",
)

#: Response keys scanned, in order, for the broker's human-readable error text.
DEFAULT_DETAIL_KEYS: Tuple[str, ...] = (
    "detail", "error", "message", "msg", "error_description", "reason", "status_message",
)


class CancelStatus(Enum):
    """Outcome of one cancel dispatch.

    Only ``CANCELLED``, ``FILLED_BEFORE_CANCEL`` and ``ALREADY_CANCELLED`` assert that
    the order is no longer working. Everything else means the caller must consult the
    broker's order-state stream before acting on the order's exposure.
    """

    #: Broker synchronously confirmed the order is in a terminal cancelled state.
    #: Only produced when ``treat_ack_as_cancelled=True`` (see module docstring).
    CANCELLED = "CANCELLED"
    #: Cancel request accepted; the order is pending cancel and CAN STILL FILL.
    #: FIX 4.4 ``OrdStatus`` 6. This is the default outcome of an HTTP 2xx.
    PENDING_CANCEL = "PENDING_CANCEL"
    #: Broker rejected the cancel because the order had already filled
    #: (FIX ``CxlRejReason`` 0, "Too late to cancel"). Terminal.
    FILLED_BEFORE_CANCEL = "FILLED_BEFORE_CANCEL"
    #: Broker positively asserted the order is already cancelled. Terminal.
    ALREADY_CANCELLED = "ALREADY_CANCELLED"
    #: Broker cannot see the order. NOT proof it is cancelled — reconcile.
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    #: Broker refused the cancel for a reason that is not one of the above. The order
    #: is presumed still working — reconcile, then retry under a NEW cancel id.
    REJECTED = "REJECTED"
    #: Outcome indeterminate: transport error, timeout, or exhausted 5xx/429 retries.
    #: The cancel may have been applied. Reconcile; safe to re-dispatch this cancel id.
    UNKNOWN = "UNKNOWN"


#: Statuses asserting the order is no longer working.
_TERMINAL_STATUSES: Set[CancelStatus] = {
    CancelStatus.CANCELLED,
    CancelStatus.FILLED_BEFORE_CANCEL,
    CancelStatus.ALREADY_CANCELLED,
}

#: Statuses safe to replay from the idempotency cache. A cached entry means "the broker
#: already answered this exact cancel id, do not send it again". ``UNKNOWN`` and
#: ``ORDER_UNKNOWN`` are absent on purpose: caching them would make a lost response
#: permanent and leave a live order un-cancellable.
_CACHEABLE_STATUSES: Set[CancelStatus] = _TERMINAL_STATUSES | {
    CancelStatus.PENDING_CANCEL,
    CancelStatus.REJECTED,
}

#: Outcomes a desk should see in the log at WARNING or above: either the order state is
#: unsettled, or a fill happened where a cancel was intended.
_ATTENTION_STATUSES: Set[CancelStatus] = {
    CancelStatus.UNKNOWN,
    CancelStatus.ORDER_UNKNOWN,
    CancelStatus.FILLED_BEFORE_CANCEL,
}


@dataclass(frozen=True)
class CancelResult:
    """Normalised, non-raising result of one idempotent cancel call."""

    client_cancel_id: str
    order_id: str
    status: CancelStatus
    is_idempotent_retry: bool
    message: str
    #: Last HTTP status observed; ``None`` if no response was ever received.
    http_status: Optional[int] = None
    #: Number of transport calls made (1 when no retry was needed).
    attempts: int = 0
    #: Broker-supplied ``Retry-After`` in seconds, when the broker asked us to back off
    #: for longer than this manager's retry budget allows.
    retry_after_s: Optional[float] = None
    #: Raw broker error text used for classification, lower-cased.
    detail: str = ""

    @property
    def is_terminal(self) -> bool:
        """True only when the broker asserted the order is no longer working."""
        return self.status in _TERMINAL_STATUSES

    @property
    def requires_reconciliation(self) -> bool:
        """True when the order's true state must be read from the broker.

        ``PENDING_CANCEL`` is included: the cancel was accepted but the order can still
        fill until an execution report says otherwise.
        """
        return not self.is_terminal


class IdempotentCancelManager:
    """Thread-safe, non-raising dispatcher for idempotent order-cancel requests.

    Guarantees, for a given ``client_cancel_id``:

      1. At most one *in-flight* dispatch at a time. Concurrent callers block on the
         first dispatch and receive its result rather than adding to a cancel storm
         (Binance auto-bans an IP with HTTP 418 for continuing to send after 429s).
      2. Exactly one dispatch overall, once the broker has returned a cacheable answer.
      3. An indeterminate outcome is never cached, so a later retry under the same id
         genuinely reaches the broker again.

    It does **not** guarantee the order was cancelled — see the module docstring and
    ``CancelResult.requires_reconciliation``.
    """

    def __init__(
        self,
        http_cancel_fn: Callable[[str, str], Tuple[int, Mapping[str, Any]]],
        max_cache_size: int = 10000,
        max_retries: int = 3,
        base_backoff_ms: int = 100,
        max_backoff_ms: int = 5000,
        jitter_ratio: float = 0.2,
        treat_ack_as_cancelled: bool = False,
        in_flight_wait_s: float = 30.0,
        filled_patterns: Sequence[str] = DEFAULT_FILLED_PATTERNS,
        already_cancelled_patterns: Sequence[str] = DEFAULT_ALREADY_CANCELLED_PATTERNS,
        unknown_order_patterns: Sequence[str] = DEFAULT_UNKNOWN_ORDER_PATTERNS,
        detail_keys: Sequence[str] = DEFAULT_DETAIL_KEYS,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        """
        Args:
            http_cancel_fn: Transport taking ``(order_id, client_cancel_id)`` and
                returning ``(http_status, response_mapping)``. To have ``Retry-After``
                honoured, surface the response header into the mapping under
                ``retry_after`` (delay-seconds or HTTP-date, per RFC 9110).
            max_cache_size: Bounded idempotency cache capacity (insertion-ordered;
                oldest evicted first).
            max_retries: Additional attempts after the first. ``0`` disables retrying.
            base_backoff_ms: First backoff interval; doubles per attempt.
            max_backoff_ms: Ceiling on any single backoff, and the budget above which a
                broker-supplied ``Retry-After`` is handed back to the caller instead of
                being slept through. A cancel is risk-reducing; blocking it for minutes
                is worse than returning ``UNKNOWN`` and letting the caller decide.
            jitter_ratio: Fraction of each backoff removed at random, in ``[0, 1)``, to
                stop many concurrent cancels from re-dispatching in lockstep after a
                broker outage. Set to ``0.0`` for deterministic timing in tests.
            treat_ack_as_cancelled: Set only for brokers whose cancel endpoint is
                synchronous and returns the order already in a terminal cancelled
                state. Leaving this ``False`` is the safe default.
            in_flight_wait_s: How long a duplicate concurrent caller waits for the
                in-flight dispatch before returning ``UNKNOWN``.
            filled_patterns, already_cancelled_patterns, unknown_order_patterns:
                Case-insensitive regexes matched against the broker's error text.
            detail_keys: Response keys scanned, in order, for that error text.
            sleep_fn: Injected for testing; must block for the given seconds.
            rng: Injected for testing; source of jitter.

        Raises:
            TypeError: ``http_cancel_fn`` is not callable.
            ValueError: A numeric parameter is out of range.
        """
        if not callable(http_cancel_fn):
            raise TypeError("http_cancel_fn must be callable")
        if max_cache_size < 1:
            raise ValueError("max_cache_size must be >= 1")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if base_backoff_ms <= 0:
            raise ValueError("base_backoff_ms must be > 0")
        if max_backoff_ms < base_backoff_ms:
            raise ValueError("max_backoff_ms must be >= base_backoff_ms")
        if not 0.0 <= jitter_ratio < 1.0:
            raise ValueError("jitter_ratio must be in [0.0, 1.0)")
        if in_flight_wait_s <= 0:
            raise ValueError("in_flight_wait_s must be > 0")

        self._http_cancel_fn = http_cancel_fn
        self._max_cache_size = max_cache_size
        self._max_retries = max_retries
        self._base_backoff_ms = base_backoff_ms
        self._max_backoff_ms = max_backoff_ms
        self._jitter_ratio = jitter_ratio
        self._treat_ack_as_cancelled = treat_ack_as_cancelled
        self._in_flight_wait_s = in_flight_wait_s
        self._detail_keys = tuple(detail_keys)
        self._sleep = sleep_fn
        self._rng = rng if rng is not None else random.Random()

        self._filled_res = self._compile(filled_patterns)
        self._already_cancelled_res = self._compile(already_cancelled_patterns)
        self._unknown_order_res = self._compile(unknown_order_patterns)

        self._lock = threading.Lock()
        self._cancel_history: "OrderedDict[str, CancelResult]" = OrderedDict()
        self._in_flight: Dict[str, threading.Event] = {}
        self._seq_counter = 0
        #: Distinguishes cancel ids minted by different processes/restarts, which a
        #: per-process counter alone cannot do.
        self._instance_token = uuid.uuid4().hex[:8]

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _compile(patterns: Sequence[str]) -> Tuple[Pattern[str], ...]:
        return tuple(re.compile(p, re.IGNORECASE) for p in patterns)

    def generate_client_cancel_id(self, order_id: str) -> str:
        """Mint a cancel id unique within this process and across restarts.

        Not durable: it is derived from process-local state, so a caller that must
        de-duplicate cancels across a restart has to persist the id alongside the order
        intent (see ``order-placement-idempotency``).
        """
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("order_id must be a non-empty string")
        with self._lock:
            self._seq_counter += 1
            seq = self._seq_counter
        return f"CANCEL_{order_id}_{self._instance_token}_{seq}_{int(time.time() * 1000)}"

    def get_cached_result(self, client_cancel_id: str) -> Optional[CancelResult]:
        """Return the cached outcome for a cancel id, if the broker has answered it."""
        with self._lock:
            return self._cancel_history.get(client_cancel_id)

    def _cache_result(self, cid: str, result: CancelResult) -> None:
        """Store a cacheable result, evicting the oldest entry past capacity."""
        if result.status not in _CACHEABLE_STATUSES:
            logger.debug(
                "Not caching %s outcome for %s: it is indeterminate and must stay "
                "re-dispatchable", result.status.value, cid,
            )
            return
        with self._lock:
            self._cancel_history[cid] = result
            while len(self._cancel_history) > self._max_cache_size:
                evicted_cid, _ = self._cancel_history.popitem(last=False)
                logger.debug("Evicted cancel id %s from bounded cache", evicted_cid)

    def _extract_detail(self, payload: Mapping[str, Any]) -> str:
        for key in self._detail_keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text.lower()
        return ""

    @staticmethod
    def _parse_retry_after(payload: Mapping[str, Any], now: float) -> Optional[float]:
        """Parse an RFC 9110 ``Retry-After`` value (delay-seconds or HTTP-date)."""
        raw = payload.get("retry_after", payload.get("Retry-After"))
        if raw is None:
            return None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return max(0.0, float(raw))
        if not isinstance(raw, str) or not raw.strip():
            return None
        text = raw.strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            pass
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - now)

    @staticmethod
    def _normalise_response(raw: Any) -> Tuple[int, Mapping[str, Any]]:
        """Coerce a transport return value, raising if it is unusable."""
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise TypeError(
                f"http_cancel_fn must return (status_code, mapping); got {type(raw).__name__}"
            )
        status, payload = raw
        if isinstance(status, bool) or not isinstance(status, int):
            raise TypeError(f"http_cancel_fn returned non-integer status {status!r}")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise TypeError(
                f"http_cancel_fn returned non-mapping payload {type(payload).__name__}"
            )
        return status, payload

    @staticmethod
    def _is_retryable(status: Optional[int]) -> bool:
        if status is None:
            return True
        return status >= 500 or status in _RETRYABLE_STATUSES

    def _backoff_seconds(self, attempt: int, retry_after_s: Optional[float]) -> Optional[float]:
        """Delay before the next attempt, or ``None`` to abandon the retry loop.

        A broker-supplied ``Retry-After`` wins over the exponential schedule, but only
        while it fits inside ``max_backoff_ms``; a longer one (an 418 ban, say) is
        surfaced to the caller instead of blocking a risk-reducing cancel thread.
        """
        if retry_after_s is not None:
            if retry_after_s * 1000.0 > self._max_backoff_ms:
                return None
            return retry_after_s
        # The exponent is clamped before it reaches the float multiply: an unclamped
        # 2**attempt overflows float conversion at large max_retries, and any exponent
        # past the cap is arithmetically irrelevant anyway.
        delay_ms = min(
            float(self._base_backoff_ms) * float(2 ** min(attempt, 32)),
            float(self._max_backoff_ms),
        )
        if self._jitter_ratio:
            delay_ms *= 1.0 - self._rng.uniform(0.0, self._jitter_ratio)
        return delay_ms / 1000.0

    # ------------------------------------------------------------ classification

    def _classify(
        self,
        order_id: str,
        http_status: Optional[int],
        detail: str,
        transport_error: Optional[str],
    ) -> Tuple[CancelStatus, str]:
        """Map one broker response onto a ``CancelStatus``.

        Ambiguity always resolves toward "reconcile". Nothing here concludes that an
        order is dead unless the broker said so.
        """
        if transport_error is not None or http_status is None:
            return (
                CancelStatus.UNKNOWN,
                f"Cancel outcome for {order_id} is UNKNOWN: {transport_error or 'no response'}. "
                f"The broker may have applied it - reconcile before acting.",
            )

        if 200 <= http_status < 300:
            if self._treat_ack_as_cancelled:
                return (
                    CancelStatus.CANCELLED,
                    f"Order {order_id} confirmed cancelled (HTTP {http_status}, "
                    f"synchronous-cancel broker).",
                )
            return (
                CancelStatus.PENDING_CANCEL,
                f"Cancel request for {order_id} accepted (HTTP {http_status}). The order "
                f"is PENDING CANCEL and can still fill until an execution report "
                f"confirms it.",
            )

        if self._is_retryable(http_status):
            return (
                CancelStatus.UNKNOWN,
                f"Cancel outcome for {order_id} is UNKNOWN after exhausting retries "
                f"(HTTP {http_status}): {detail or 'no detail'}. Reconcile before acting.",
            )

        if any(p.search(detail) for p in self._filled_res):
            return (
                CancelStatus.FILLED_BEFORE_CANCEL,
                f"Cancel-vs-Fill race: order {order_id} filled before the cancel arrived "
                f"(HTTP {http_status}): {detail}.",
            )

        if any(p.search(detail) for p in self._already_cancelled_res):
            return (
                CancelStatus.ALREADY_CANCELLED,
                f"Order {order_id} was already cancelled at the broker "
                f"(HTTP {http_status}): {detail}.",
            )

        if http_status == 404 or any(p.search(detail) for p in self._unknown_order_res):
            return (
                CancelStatus.ORDER_UNKNOWN,
                f"Broker cannot see order {order_id} (HTTP {http_status}): "
                f"{detail or 'no detail'}. This is NOT proof it was cancelled - the same "
                f"error covers a wrong id, symbol, or API key. Reconcile.",
            )

        return (
            CancelStatus.REJECTED,
            f"Cancel for {order_id} rejected (HTTP {http_status}): {detail or 'no detail'}. "
            f"The order is presumed still working - reconcile before retrying.",
        )

    # ---------------------------------------------------------------- dispatch

    def cancel_order_idempotent(
        self, order_id: str, client_cancel_id: Optional[str] = None
    ) -> CancelResult:
        """Dispatch one cancel for ``order_id``, de-duplicated on ``client_cancel_id``.

        Returns the cached outcome if the broker has already answered this cancel id,
        and blocks on a concurrent dispatch of the same id rather than duplicating it.
        Never raises: transport and programming errors alike surface as
        ``CancelStatus.UNKNOWN``.

        Raises:
            ValueError: ``order_id`` or ``client_cancel_id`` is not a non-empty string.
        """
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("order_id must be a non-empty string")
        if client_cancel_id is not None and (
            not isinstance(client_cancel_id, str) or not client_cancel_id.strip()
        ):
            raise ValueError("client_cancel_id must be a non-empty string when supplied")

        cid = client_cancel_id or self.generate_client_cancel_id(order_id)

        waiter = self._claim_or_wait(cid)
        if isinstance(waiter, CancelResult):
            return waiter

        try:
            result = self._dispatch_with_retries(order_id, cid)
            self._cache_result(cid, result)
            return result
        finally:
            with self._lock:
                event = self._in_flight.pop(cid, None)
            if event is not None:
                event.set()

    def _claim_or_wait(self, cid: str) -> Optional[CancelResult]:
        """Claim exclusive dispatch of ``cid``, or return the result of whoever has it.

        Returns ``None`` when this caller owns the dispatch, otherwise a
        ``CancelResult`` replayed from cache or produced by waiting on the in-flight
        attempt. Doing the check-and-claim under one lock is what stops two threads
        from both missing the cache and both hitting the broker.
        """
        with self._lock:
            cached = self._cancel_history.get(cid)
            if cached is not None:
                logger.info(
                    "Idempotent cache hit for %s: replaying %s", cid, cached.status.value
                )
                return self._as_replay(cached)
            event = self._in_flight.get(cid)
            if event is None:
                self._in_flight[cid] = threading.Event()
                return None

        logger.info("Cancel %s already in flight; waiting instead of re-dispatching", cid)
        if not event.wait(timeout=self._in_flight_wait_s):
            logger.warning(
                "Timed out after %.1fs waiting on in-flight cancel %s", self._in_flight_wait_s, cid
            )
            return CancelResult(
                client_cancel_id=cid,
                order_id="",
                status=CancelStatus.UNKNOWN,
                is_idempotent_retry=True,
                message=(
                    f"Timed out waiting on a concurrent dispatch of cancel {cid}; its "
                    f"outcome is UNKNOWN. Reconcile before acting."
                ),
            )

        with self._lock:
            cached = self._cancel_history.get(cid)
        if cached is not None:
            return self._as_replay(cached)
        return CancelResult(
            client_cancel_id=cid,
            order_id="",
            status=CancelStatus.UNKNOWN,
            is_idempotent_retry=True,
            message=(
                f"Concurrent dispatch of cancel {cid} produced an indeterminate outcome. "
                f"Reconcile before acting."
            ),
        )

    @staticmethod
    def _as_replay(cached: CancelResult) -> CancelResult:
        return CancelResult(
            client_cancel_id=cached.client_cancel_id,
            order_id=cached.order_id,
            status=cached.status,
            is_idempotent_retry=True,
            message=f"Idempotent replay: {cached.message}",
            http_status=cached.http_status,
            attempts=cached.attempts,
            retry_after_s=cached.retry_after_s,
            detail=cached.detail,
        )

    def _dispatch_with_retries(self, order_id: str, cid: str) -> CancelResult:
        max_attempts = self._max_retries + 1
        http_status: Optional[int] = None
        payload: Mapping[str, Any] = {}
        transport_error: Optional[str] = None
        retry_after_s: Optional[float] = None
        attempts = 0

        for attempt in range(max_attempts):
            attempts = attempt + 1
            # Reset per attempt: carrying a previous attempt's payload forward would
            # classify the final outcome on a stale response body.
            http_status, payload, transport_error, retry_after_s = None, {}, None, None
            try:
                http_status, payload = self._normalise_response(
                    self._http_cancel_fn(order_id, cid)
                )
            except Exception as exc:  # noqa: BLE001 - must not raise into the trade loop
                transport_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Cancel transport error for %s (attempt %d/%d): %s",
                    cid, attempts, max_attempts, transport_error,
                )
            else:
                retry_after_s = self._parse_retry_after(payload, time.time())
                if not self._is_retryable(http_status):
                    break
                logger.warning(
                    "Retryable cancel response for %s (attempt %d/%d): HTTP %s",
                    cid, attempts, max_attempts, http_status,
                )

            if attempt >= max_attempts - 1:
                break
            delay = self._backoff_seconds(attempt, retry_after_s)
            if delay is None:
                logger.warning(
                    "Broker asked for a %.1fs Retry-After on %s, beyond the %dms retry "
                    "budget; returning control to the caller",
                    retry_after_s or 0.0, cid, self._max_backoff_ms,
                )
                break
            self._sleep(delay)

        detail = self._extract_detail(payload)
        status, message = self._classify(order_id, http_status, detail, transport_error)

        if status is CancelStatus.REJECTED:
            logger.error("%s", message)
        elif status in _ATTENTION_STATUSES:
            logger.warning("%s", message)
        else:
            logger.info("%s", message)

        return CancelResult(
            client_cancel_id=cid,
            order_id=order_id,
            status=status,
            is_idempotent_retry=False,
            message=message,
            http_status=http_status,
            attempts=attempts,
            retry_after_s=retry_after_s,
            detail=detail,
        )
