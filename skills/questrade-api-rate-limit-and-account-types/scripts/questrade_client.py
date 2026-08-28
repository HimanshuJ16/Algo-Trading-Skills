"""
questrade-api-rate-limit-and-account-types
=========================================

Reference client for the Questrade IQ REST API covering the three areas the
API actually punishes you for getting wrong:

1. **OAuth2 refresh-token rotation.** Every redemption returns a *new* refresh
   token and invalidates the one submitted. Losing the new one costs you API
   access until a human regenerates a token in the Questrade API Centre.
2. **Rate limiting.** Questrade meters two separate call categories, each
   across a per-second *and* a per-hour window. A single 30/sec bucket
   satisfies neither.
3. **Account-type eligibility.** Questrade returns sixteen account types. Only
   ``Margin`` can borrow, and borrowing is what a short sale requires.

Every documented figure in this module is sourced in
``references/standards.md``. Values that are *inferred* rather than documented
are labelled as such at the point of use.

The module has no third-party dependencies: HTTP is supplied by the caller as
``http_fn`` so the client is testable without network access and does not force
a transport library on the host application.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import quote

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Hosts
# --------------------------------------------------------------------------
#: Live OAuth2 host. Source: Questrade API "Getting started" / "Security".
LIVE_LOGIN_HOST = "https://login.questrade.com"
#: Practice (paper) OAuth2 host. Source: Questrade API "Getting started".
PRACTICE_LOGIN_HOST = "https://practicelogin.questrade.com"

#: Questrade only accepts API requests over TLS ("Security" page).
_REQUIRED_SCHEME = "https://"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class QuestradeAPIError(Exception):
    """
    Base error for every failure raised by this module.

    ``status_code`` is the HTTP status when one is available and ``error_code``
    is Questrade's numeric ``code`` field from a general/order error body. Both
    are exposed **structurally** so callers classify on attributes rather than
    by searching the message text for ``"429"`` -- a substring that also matches
    order ids and limit prices.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class QuestradeAuthError(QuestradeAPIError):
    """OAuth2 exchange failed, or the client is not authenticated."""


class QuestradeRateLimitError(QuestradeAPIError):
    """
    A request was refused for rate-limit reasons.

    ``source`` is ``"local"`` when this client's own budget refused to dispatch
    (no request was sent, so nothing can have executed broker-side) or
    ``"server"`` when Questrade returned HTTP 429. ``reset_at`` carries the Unix
    timestamp from ``X-RateLimit-Reset`` when Questrade supplied one.
    """

    def __init__(
        self,
        message: str,
        *,
        source: str,
        status_code: Optional[int] = None,
        reset_at: Optional[float] = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.source = source
        self.reset_at = reset_at


class AccountRestrictionError(QuestradeAPIError):
    """The requested order side is not permitted for the target account."""


# --------------------------------------------------------------------------
# Enumerations (source: Questrade API "Enumerations" reference)
# --------------------------------------------------------------------------
class AccountType(Enum):
    """
    Every account type Questrade documents, plus an ``UNKNOWN`` sentinel.

    ``UNKNOWN`` exists because the published enumeration has not changed since
    2015 while Questrade keeps launching account types (FHSA is the recent
    example). An account whose type this client cannot recognise is treated as
    **maximally restricted** rather than silently coerced to ``MARGIN``.
    """

    CASH = "Cash"
    MARGIN = "Margin"
    TFSA = "TFSA"
    RRSP = "RRSP"
    FHSA = "FHSA"
    SRRSP = "SRRSP"
    LRRSP = "LRRSP"
    LIRA = "LIRA"
    LIF = "LIF"
    RIF = "RIF"
    SRIF = "SRIF"
    LRIF = "LRIF"
    RRIF = "RRIF"
    PRIF = "PRIF"
    RESP = "RESP"
    FRESP = "FRESP"
    UNKNOWN = "__unknown__"

    @classmethod
    def from_api(cls, raw: Any, *, strict: bool = False) -> "AccountType":
        """
        Map the API's ``type`` string onto the enum.

        Raises :class:`QuestradeAPIError` when ``strict`` and the value is not
        recognised; otherwise returns :attr:`UNKNOWN` and logs a warning. It
        never falls back to ``MARGIN`` -- that fallback would hand an
        unrecognised registered plan the full permissions of a margin account.
        """
        try:
            return cls(str(raw))
        except ValueError:
            if strict:
                raise QuestradeAPIError(
                    f"Unrecognised Questrade account type {raw!r}; refusing to guess."
                ) from None
            logger.warning(
                "Unrecognised Questrade account type %r; treating as restricted.", raw
            )
            return cls.UNKNOWN


class AccountStatus(Enum):
    """
    Documented account statuses, plus an ``UNKNOWN`` sentinel.

    Questrade's enumeration table lists these values without descriptions, so
    the trading semantics attached to them in ``_ORDER_ACCEPTING_STATUSES`` and
    ``_LIQUIDATE_ONLY_STATUSES`` are this module's conservative interpretation
    of the status names, not documented broker behaviour.
    """

    ACTIVE = "Active"
    SUSPENDED_CLOSED = "Suspended (Closed)"
    SUSPENDED_VIEW_ONLY = "Suspended (View Only)"
    LIQUIDATE_ONLY = "Liquidate Only"
    CLOSED = "Closed"
    UNKNOWN = "__unknown__"

    @classmethod
    def from_api(cls, raw: Any) -> "AccountStatus":
        try:
            return cls(str(raw))
        except ValueError:
            logger.warning(
                "Unrecognised Questrade account status %r; treating as non-trading.",
                raw,
            )
            return cls.UNKNOWN


#: Registered (tax-sheltered) plans. Under the Income Tax Act a registered plan
#: trust may not borrow -- ITA 146(4)(a) for RRSPs, ITA 146.2(2)(f) for TFSAs --
#: which is why none of these can carry a margin debit or a short position.
REGISTERED_ACCOUNT_TYPES = frozenset(
    {
        AccountType.TFSA,
        AccountType.RRSP,
        AccountType.FHSA,
        AccountType.SRRSP,
        AccountType.LRRSP,
        AccountType.LIRA,
        AccountType.LIF,
        AccountType.RIF,
        AccountType.SRIF,
        AccountType.LRIF,
        AccountType.RRIF,
        AccountType.PRIF,
        AccountType.RESP,
        AccountType.FRESP,
    }
)

#: The only account type that can borrow, and therefore the only one that can
#: hold a short position. ``Cash`` is unregistered but equally cannot short.
MARGIN_CAPABLE_ACCOUNT_TYPES = frozenset({AccountType.MARGIN})

#: Documented Questrade "Order Side" values.
ORDER_SIDES = frozenset({"Buy", "Sell", "Short", "Cov", "BTO", "STC", "STO", "BTC"})

#: Sides that can only exist against borrowed stock, i.e. require margin.
BORROW_REQUIRING_SIDES = frozenset({"Short", "Cov"})

#: Sell-To-Open writes an option. Whether a *covered* write is permitted in a
#: given registered plan is a Questrade account-approval question the API does
#: not expose, so this client escalates rather than deciding.
OPTION_WRITING_SIDES = frozenset({"STO"})

#: Sides that can only reduce an existing position.
POSITION_REDUCING_SIDES = frozenset({"Sell", "Cov", "STC", "BTC"})

_ORDER_ACCEPTING_STATUSES = frozenset({AccountStatus.ACTIVE})
_LIQUIDATE_ONLY_STATUSES = frozenset({AccountStatus.LIQUIDATE_ONLY})


class Eligibility(Enum):
    """Outcome of a pre-trade account-eligibility check."""

    ALLOWED = "allowed"
    DENIED = "denied"
    REVIEW_REQUIRED = "review_required"


# --------------------------------------------------------------------------
# Data records
# --------------------------------------------------------------------------
@dataclass
class QuestradeAuthToken:
    """
    One OAuth2 session.

    ``refresh_token`` is the *rotated* token: the one submitted to obtain this
    session is already dead. ``expires_at`` is wall-clock (useful for display
    and persistence); expiry decisions use ``monotonic_deadline`` instead so a
    clock step cannot make a live token look dead or vice versa.
    """

    access_token: str
    refresh_token: str
    api_server: str
    token_type: str
    expires_at: float
    expires_in: float
    monotonic_deadline: float = field(default=0.0, repr=False)

    def __repr__(self) -> str:
        # Never let a traceback or a debug log print bearer/refresh material.
        return (
            "QuestradeAuthToken(access_token='<redacted>', "
            "refresh_token='<redacted>', "
            f"api_server={self.api_server!r}, token_type={self.token_type!r}, "
            f"expires_at={self.expires_at!r}, expires_in={self.expires_in!r})"
        )


@dataclass(frozen=True)
class QuestradeAccount:
    """One row from ``GET v1/accounts``."""

    account_number: str
    account_type: AccountType
    is_primary: bool
    status: AccountStatus
    is_billing: bool = False
    client_account_type: str = ""

    @property
    def is_registered(self) -> bool:
        return self.account_type in REGISTERED_ACCOUNT_TYPES

    @property
    def can_borrow(self) -> bool:
        return self.account_type in MARGIN_CAPABLE_ACCOUNT_TYPES


@dataclass(frozen=True)
class OrderEligibility:
    """
    Result of :meth:`QuestradeClient.check_order_eligibility`.

    ``REVIEW_REQUIRED`` is deliberately distinct from ``ALLOWED``: it means the
    restriction depends on broker-side account approvals this API does not
    expose. :attr:`allowed` folds it into ``False`` so a caller that only reads
    the boolean fails closed.
    """

    eligibility: Eligibility
    reason: str
    account_number: str
    account_type: AccountType
    order_side: str

    @property
    def allowed(self) -> bool:
        return self.eligibility is Eligibility.ALLOWED


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RateWindow:
    """``capacity`` requests per ``period_sec`` seconds."""

    capacity: int
    period_sec: float

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError(f"RateWindow capacity must be >= 1, got {self.capacity}")
        if self.period_sec <= 0:
            raise ValueError(f"RateWindow period_sec must be > 0, got {self.period_sec}")


class RateLimitCategory(Enum):
    """Questrade's two documented rate-limit categories."""

    ACCOUNT = "account"
    MARKET_DATA = "market_data"


#: Source: Questrade API "Rate limiting". Account calls 30/sec and 30,000/hour;
#: Market Data calls 20/sec and 15,000/hour. Both windows bind simultaneously:
#: 30 req/sec sustained is 108,000 req/hour and exhausts the hourly account
#: budget in under 17 minutes.
DOCUMENTED_WINDOWS: Dict[RateLimitCategory, Tuple[RateWindow, ...]] = {
    RateLimitCategory.ACCOUNT: (RateWindow(30, 1.0), RateWindow(30_000, 3600.0)),
    RateLimitCategory.MARKET_DATA: (RateWindow(20, 1.0), RateWindow(15_000, 3600.0)),
}

#: Endpoints Questrade lists in the rate-limit table, keyed by normalised path.
_DOCUMENTED_ENDPOINTS: Dict[str, RateLimitCategory] = {
    "time": RateLimitCategory.ACCOUNT,
    "accounts": RateLimitCategory.ACCOUNT,
    "accounts/{id}/positions": RateLimitCategory.ACCOUNT,
    "accounts/{id}/balances": RateLimitCategory.ACCOUNT,
    "accounts/{id}/executions": RateLimitCategory.ACCOUNT,
    "accounts/{id}/orders": RateLimitCategory.ACCOUNT,
    "markets": RateLimitCategory.MARKET_DATA,
    "markets/quotes/{id}": RateLimitCategory.MARKET_DATA,
    "markets/candles/{id}": RateLimitCategory.MARKET_DATA,
    "symbols/{id}": RateLimitCategory.MARKET_DATA,
    "symbols/{id}/options": RateLimitCategory.MARKET_DATA,
}

_PATH_LITERALS = frozenset(
    {
        "time",
        "accounts",
        "markets",
        "symbols",
        "positions",
        "balances",
        "executions",
        "orders",
        "quotes",
        "candles",
        "options",
        "strategies",
        "search",
        "activities",
    }
)


def normalize_endpoint(path: str) -> str:
    """
    Reduce ``/v1/accounts/26598145/orders`` to ``accounts/{id}/orders``.

    Any segment that is not a known literal is treated as an id, which keeps
    account numbers, symbol ids and order ids out of the lookup key.
    """
    segments = [s for s in str(path).strip("/").split("/") if s]
    if segments and segments[0].lower() == "v1":
        segments = segments[1:]
    return "/".join(
        s if s.lower() in _PATH_LITERALS else "{id}" for s in segments
    )


def categorize_endpoint(path: str) -> RateLimitCategory:
    """
    Map a request path onto a rate-limit category.

    Questrade's table does not categorise every published endpoint --
    ``accounts/:id/activities``, ``symbols/search`` and the options/strategies
    quote calls are absent from it. Those fall through to the **tighter** Market
    Data budget. That is this module's conservative inference, not a documented
    Questrade fact: over-throttling costs latency, under-throttling costs a 429
    and eventually a suspension.
    """
    key = normalize_endpoint(path)
    category = _DOCUMENTED_ENDPOINTS.get(key)
    if category is not None:
        return category
    logger.debug(
        "Endpoint %r is not in Questrade's published rate-limit table; "
        "applying the tighter Market Data budget.",
        key,
    )
    return RateLimitCategory.MARKET_DATA


class TokenBucketRateLimiter:
    """
    Thread-safe token bucket over a single window.

    Refill is driven by a monotonic clock: ``time.time()`` can step backwards on
    an NTP correction, which would either freeze the bucket or grant a burst of
    thousands of tokens at once.

    **This smooths a request rate; it does not enforce a window count.** A
    bucket starts full and refills continuously, so one sized to Questrade's
    30,000/hour cap grants 30,000 immediately plus 30,000 more as it refills --
    60,000 in the first hour, twice the documented limit. Use it to *pace*
    traffic comfortably below a cap (a historical backfill, say);
    :class:`SlidingWindowCounter` is what enforces the caps themselves, and is
    what :class:`MultiWindowBudget` and the client are built on.
    """

    def __init__(
        self,
        capacity: int = 30,
        fill_rate: float = 30.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        if fill_rate <= 0:
            raise ValueError(
                f"fill_rate must be > 0, got {fill_rate}; a non-positive rate "
                "makes the bucket permanently empty once drained"
            )
        self.capacity = capacity
        self.fill_rate = float(fill_rate)
        self._tokens = float(capacity)
        self._monotonic = monotonic
        self._last_update = monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Caller must hold ``self._lock``."""
        now = self._monotonic()
        elapsed = max(0.0, now - self._last_update)
        self._last_update = now
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.fill_rate)

    @property
    def tokens(self) -> float:
        """Tokens currently available (refilled on read)."""
        with self._lock:
            self._refill()
            return self._tokens

    def acquire(self, tokens: float = 1.0) -> bool:
        """Consume ``tokens`` if available. Non-blocking; never partially debits."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def time_until_available(self, tokens: float = 1.0) -> float:
        """Seconds until ``tokens`` would be available (0.0 if available now)."""
        if tokens > self.capacity:
            return float("inf")
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                return 0.0
            return (tokens - self._tokens) / self.fill_rate

    def resync(self, remaining: float) -> None:
        """
        Clamp the bucket down to the broker's own count.

        Fed from ``X-RateLimit-Remaining``. Only ever lowers the local estimate:
        the server may be counting requests this process did not make (another
        bot on the same API key), and trusting a *higher* server number would
        let two processes each believe they hold the full quota.
        """
        with self._lock:
            self._refill()
            self._tokens = min(self._tokens, max(0.0, float(remaining)))


class SlidingWindowCounter:
    """
    Exact "no more than ``capacity`` grants in any trailing ``period_sec``".

    Questrade states its limits as request *counts per window* ("maximum allowed
    requests per second", "per hour"), not as refill rates, so this — not a
    token bucket — is the primitive that matches the documented rule. It keeps
    the grant timestamps, which bounds memory at ``capacity`` entries and makes
    the window exact rather than approximate.
    """

    def __init__(
        self,
        capacity: int,
        period_sec: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        if period_sec <= 0:
            raise ValueError(f"period_sec must be > 0, got {period_sec}")
        self.capacity = capacity
        self.period_sec = float(period_sec)
        self._monotonic = monotonic
        self._log: Deque[float] = deque()
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        """Caller must hold ``self._lock``."""
        horizon = now - self.period_sec
        while self._log and self._log[0] <= horizon:
            self._log.popleft()

    @property
    def used(self) -> int:
        """Grants inside the current window."""
        with self._lock:
            now = self._monotonic()
            self._expire(now)
            return len(self._log)

    def acquire(self) -> bool:
        with self._lock:
            now = self._monotonic()
            self._expire(now)
            if len(self._log) >= self.capacity:
                return False
            self._log.append(now)
            return True

    def time_until_available(self) -> float:
        """Seconds until the window would admit one more request."""
        with self._lock:
            now = self._monotonic()
            self._expire(now)
            if len(self._log) < self.capacity:
                return 0.0
            return max(0.0, self._log[0] + self.period_sec - now)

    def resync(self, remaining: float) -> None:
        """
        Reconcile with ``X-RateLimit-Remaining`` by charging the difference.

        Only ever *reduces* headroom: the server may be counting requests this
        process never made (a second bot on the same API key), and trusting a
        higher server figure would let both processes believe they hold the full
        quota. Synthetic charges are stamped at ``now``, so they age out over a
        full period — conservative by design.
        """
        try:
            allowed = max(0, min(self.capacity, int(float(remaining))))
        except (TypeError, ValueError):
            return
        with self._lock:
            now = self._monotonic()
            self._expire(now)
            target_used = self.capacity - allowed
            while len(self._log) < target_used:
                self._log.append(now)


class MultiWindowBudget:
    """
    Several :class:`SlidingWindowCounter` windows consumed all-or-nothing.

    All-or-nothing matters: if the per-hour window refuses after the per-second
    window has already been debited, every rejected attempt leaks capacity from
    the faster window and the effective per-second rate silently collapses.
    """

    def __init__(
        self,
        windows: Sequence[RateWindow],
        *,
        name: str = "",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not windows:
            raise ValueError("MultiWindowBudget requires at least one window")
        self.name = name
        self.windows: Tuple[RateWindow, ...] = tuple(windows)
        self._buckets = tuple(
            SlidingWindowCounter(
                capacity=w.capacity, period_sec=w.period_sec, monotonic=monotonic
            )
            for w in self.windows
        )
        self._lock = threading.RLock()

    def acquire(self) -> bool:
        with self._lock:
            if any(b.time_until_available() > 0.0 for b in self._buckets):
                return False
            for bucket in self._buckets:
                bucket.acquire()
            return True

    def time_until_available(self) -> float:
        with self._lock:
            return max(b.time_until_available() for b in self._buckets)

    def binding_window(self) -> Optional[RateWindow]:
        """The window furthest from granting, or ``None`` if all are ready."""
        with self._lock:
            waits = [b.time_until_available() for b in self._buckets]
        worst = max(waits)
        if worst <= 0.0:
            return None
        return self.windows[waits.index(worst)]

    def usage(self) -> Tuple[int, ...]:
        """Grants currently charged against each window, in declaration order."""
        with self._lock:
            return tuple(b.used for b in self._buckets)

    def resync(self, remaining: float) -> None:
        """
        Apply ``X-RateLimit-Remaining`` to the shortest-period window only.

        Questrade sends a single ``X-RateLimit-Remaining`` value and does not
        state which window it describes. Applying it to every window is
        actively harmful: ``remaining: 29`` against a 30,000/hour window would
        charge 29,971 requests and strand the client for an hour on one
        ambiguous header. The shortest window is the one a burst actually hits
        and the only one whose capacity is commensurate with a small reading.
        Values exceeding that window's capacity are ignored as not describing
        it. **Inferred** -- Questrade documents the header, not its scope.
        """
        with self._lock:
            if not self._buckets:
                return
            index = min(
                range(len(self.windows)), key=lambda i: self.windows[i].period_sec
            )
            try:
                value = float(remaining)
            except (TypeError, ValueError):
                return
            if value > self.windows[index].capacity:
                logger.debug(
                    "Ignoring X-RateLimit-Remaining=%s: exceeds the %s/%gs window "
                    "capacity, so it does not describe that window.",
                    remaining,
                    self.windows[index].capacity,
                    self.windows[index].period_sec,
                )
                return
            self._buckets[index].resync(value)


class QuestradeRateLimiter:
    """Per-category budgets seeded with Questrade's documented figures."""

    def __init__(
        self,
        windows: Optional[Mapping[RateLimitCategory, Sequence[RateWindow]]] = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        source = DOCUMENTED_WINDOWS if windows is None else windows
        missing = set(RateLimitCategory) - set(source)
        if missing:
            raise ValueError(
                "No rate-limit windows configured for "
                f"{sorted(c.value for c in missing)}"
            )
        self._budgets: Dict[RateLimitCategory, MultiWindowBudget] = {
            category: MultiWindowBudget(
                tuple(ws), name=category.value, monotonic=monotonic
            )
            for category, ws in source.items()
        }

    def budget(self, category: RateLimitCategory) -> MultiWindowBudget:
        return self._budgets[category]

    def acquire(self, category: RateLimitCategory) -> bool:
        return self._budgets[category].acquire()

    def time_until_available(self, category: RateLimitCategory) -> float:
        return self._budgets[category].time_until_available()

    def apply_headers(
        self, category: RateLimitCategory, headers: Optional[Mapping[str, Any]]
    ) -> None:
        """
        Resync a category from ``X-RateLimit-Remaining``.

        Header names are matched case-insensitively; a malformed value is
        ignored rather than allowed to corrupt the local budget.
        """
        if not headers:
            return
        lowered = {str(k).lower(): v for k, v in headers.items()}
        raw = lowered.get("x-ratelimit-remaining")
        if raw is None:
            return
        try:
            remaining = float(raw)
        except (TypeError, ValueError):
            logger.warning("Unparseable X-RateLimit-Remaining header: %r", raw)
            return
        self._budgets[category].resync(remaining)


def parse_rate_limit_reset(headers: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Return ``X-RateLimit-Reset`` as a Unix timestamp, or ``None``."""
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    raw = lowered.get("x-ratelimit-reset")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Unparseable X-RateLimit-Reset header: %r", raw)
        return None


# --------------------------------------------------------------------------
# api_server normalisation
# --------------------------------------------------------------------------
def normalize_api_server(raw: Any) -> str:
    """
    Normalise ``api_server`` into a base URL ending in exactly one ``/``.

    Questrade's own documentation returns this field in three different shapes
    -- ``https://api01.iq.questrade.com``, ``https://api01.iq.questrade.com/``
    and ``https://api01.iq.questrade.com/v1`` -- so ``f"{api_server}v1/accounts"``
    produces ``...questrade.comv1/accounts`` or ``.../v1v1/accounts`` for two of
    the three. A trailing version segment is stripped so callers can keep
    passing version-qualified paths.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise QuestradeAuthError("OAuth2 response contained no usable 'api_server'.")
    server = raw.strip()
    if not server.lower().startswith(_REQUIRED_SCHEME):
        raise QuestradeAuthError(
            f"api_server {server!r} is not an https:// URL; Questrade refuses "
            "plaintext connections and this may indicate a tampered response."
        )
    server = server.rstrip("/")
    if server.lower().endswith("/v1"):
        server = server[: -len("/v1")]
    return server + "/"


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------
#: ``http_fn(method, url, headers, body)`` returning ``(status, data)`` or
#: ``(status, data, response_headers)``. The three-tuple form enables
#: ``X-RateLimit-*`` ingestion; the two-tuple form is still accepted.
HttpFn = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Any]


class QuestradeClient:
    """
    Questrade IQ REST client with rotation-safe auth, category-aware rate
    limiting and fail-closed account-eligibility checks.

    This client implements the ``read_acc`` / ``read_md`` surface available to
    personal API applications. It deliberately does **not** submit orders:
    Questrade scopes ``POST accounts/:id/orders`` under ``trade``, documented as
    "partner developers only", and publishes no order-placement endpoint
    reference. :meth:`check_order_eligibility` is a *pre-submission gate* for
    whatever component does hold trade access.
    """

    def __init__(
        self,
        http_fn: HttpFn,
        *,
        practice: bool = False,
        token_persist_fn: Optional[Callable[[QuestradeAuthToken], None]] = None,
        rate_limiter: Optional[QuestradeRateLimiter] = None,
        strict_account_types: bool = False,
        token_request_method: str = "GET",
        expiry_skew_sec: float = 60.0,
        max_wait_sec: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if http_fn is None:
            raise ValueError("http_fn is required; the client performs no I/O itself.")
        if expiry_skew_sec < 0:
            raise ValueError("expiry_skew_sec must be >= 0")
        if max_wait_sec < 0:
            raise ValueError("max_wait_sec must be >= 0")
        if str(token_request_method).upper() not in {"GET", "POST"}:
            raise ValueError("token_request_method must be 'GET' or 'POST'")

        self._http_fn = http_fn
        self.login_host = PRACTICE_LOGIN_HOST if practice else LIVE_LOGIN_HOST
        self.practice = practice
        self._token_persist_fn = token_persist_fn
        self.rate_limiter = rate_limiter or QuestradeRateLimiter(monotonic=monotonic)
        self.strict_account_types = strict_account_types
        self.token_request_method = str(token_request_method).upper()
        self.expiry_skew_sec = float(expiry_skew_sec)
        self.max_wait_sec = float(max_wait_sec)
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._sleep = sleep_fn

        self.auth_token: Optional[QuestradeAuthToken] = None
        self.accounts: Dict[str, QuestradeAccount] = {}

    # -- transport ---------------------------------------------------------
    @staticmethod
    def _unpack(result: Any) -> Tuple[int, Any, Mapping[str, Any]]:
        """Accept either ``(status, data)`` or ``(status, data, headers)``."""
        if not isinstance(result, (tuple, list)) or len(result) not in (2, 3):
            raise QuestradeAPIError(
                "http_fn must return (status_code, data) or "
                f"(status_code, data, headers); got {type(result).__name__}"
            )
        if len(result) == 2:
            status, data = result
            headers: Mapping[str, Any] = {}
        else:
            status, data, headers = result
        if not isinstance(status, int) or isinstance(status, bool):
            raise QuestradeAPIError(
                f"http_fn returned a non-integer status {status!r}; classify "
                "rate limits and auth failures on the numeric status, never on "
                "message text."
            )
        return status, data, headers or {}

    @staticmethod
    def _raise_for_embedded_error(data: Any, status: int) -> None:
        """
        Raise when Questrade signals an error inside the body.

        Questrade documents order-processing errors that arrive under
        ``HTTP/1.1 200 OK`` carrying a non-zero ``code``, a ``message`` and an
        ``orderId`` for an order that **was** created. Treating a 200 as
        unconditional success is how a rejected-but-created order goes
        unnoticed.
        """
        if isinstance(data, Mapping) and "code" in data and "message" in data:
            try:
                code: Optional[int] = int(data["code"])
            except (TypeError, ValueError):
                code = None
            raise QuestradeAPIError(
                f"Questrade error {data.get('code')}: {data.get('message')}",
                status_code=status,
                error_code=code,
            )

    def _await_budget(self, category: RateLimitCategory) -> None:
        """Wait up to ``max_wait_sec`` for the category budget, else raise."""
        deadline = self._monotonic() + self.max_wait_sec
        while True:
            if self.rate_limiter.acquire(category):
                return
            wait = self.rate_limiter.time_until_available(category)
            remaining = deadline - self._monotonic()
            if wait > remaining or remaining <= 0:
                window = self.rate_limiter.budget(category).binding_window()
                detail = (
                    f"binding window {window.capacity}/{window.period_sec:g}s"
                    if window is not None
                    else "no window is currently binding"
                )
                raise QuestradeRateLimitError(
                    f"Local {category.value} budget exhausted; {detail} needs "
                    f"{wait:.3f}s but only {max(0.0, remaining):.3f}s of wait "
                    "budget remains. No request was dispatched.",
                    source="local",
                )
            self._sleep(max(0.0, min(wait, remaining)))

    def _request(
        self,
        method: str,
        path: str,
        *,
        category: Optional[RateLimitCategory] = None,
    ) -> Any:
        """Dispatch an authenticated call against the session's ``api_server``."""
        token = self.require_token()
        category = category or categorize_endpoint(path)
        self._await_budget(category)

        url = token.api_server + str(path).lstrip("/")
        headers = {"Authorization": f"{token.token_type} {token.access_token}"}
        status, data, response_headers = self._unpack(
            self._http_fn(method, url, headers, None)
        )
        self.rate_limiter.apply_headers(category, response_headers)

        if status == 429:
            raise QuestradeRateLimitError(
                f"Questrade returned HTTP 429 for {path}. Reduce polling "
                "frequency; repeated 429s escalate to a suspension.",
                source="server",
                status_code=429,
                reset_at=parse_rate_limit_reset(response_headers),
            )
        if status in (401, 403):
            raise QuestradeAuthError(
                f"Questrade rejected the access token for {path} (HTTP {status}). "
                "The token may have expired or the app may lack the required "
                "scope.",
                status_code=status,
            )
        if status != 200:
            raise QuestradeAPIError(
                f"Questrade call {method} {path} failed with HTTP {status}.",
                status_code=status,
            )
        self._raise_for_embedded_error(data, status)
        return data

    # -- OAuth2 ------------------------------------------------------------
    def refresh_access_token(self, refresh_token: str) -> QuestradeAuthToken:
        """
        Redeem a refresh token for a session, then persist the rotated token.

        The submitted token is consumed by this call and is dead the moment
        Questrade answers. If ``token_persist_fn`` is configured it is invoked
        with the new session **before** this method returns, and a persistence
        failure is raised rather than swallowed -- a process that crashes
        holding only the dead token has lost API access until a human
        regenerates one in the Questrade API Centre.
        """
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise QuestradeAuthError("refresh_token must be a non-empty string.")

        token = refresh_token.strip()
        endpoint = f"{self.login_host}/oauth2/token"

        if self.token_request_method == "POST":
            # Keeps the secret out of the request URL, which proxies and access
            # logs routinely record. Questrade documents this form on its
            # Security page. The body is handed over as un-encoded form fields
            # for the transport to encode -- do not pre-encode it here or the
            # transport will percent-encode the escapes a second time.
            url = endpoint
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            body: Optional[Dict[str, Any]] = {
                "grant_type": "refresh_token",
                "refresh_token": token,
            }
        else:
            # Questrade's sample refresh tokens contain '+' and '/'. Interpolated
            # raw into a query string, '+' decodes server-side as a space and the
            # exchange fails with a token that looks correct in the logs.
            encoded = quote(token, safe="")
            url = f"{endpoint}?grant_type=refresh_token&refresh_token={encoded}"
            headers = {}
            body = None

        status, data, _headers = self._unpack(
            self._http_fn(self.token_request_method, url, headers, body)
        )
        if status != 200 or not isinstance(data, Mapping):
            # The response body can echo the submitted token; never log it.
            raise QuestradeAuthError(
                f"OAuth2 refresh failed with HTTP {status}. If the token was "
                "already redeemed or is older than its 7-day validity window, "
                "a new one must be generated in the Questrade API Centre.",
                status_code=status,
            )

        missing = [
            key
            for key in ("access_token", "refresh_token", "api_server", "expires_in")
            if not data.get(key)
        ]
        if missing:
            # expires_in must never be defaulted: Questrade's own samples show
            # both 300s and 1800s, so a guess of 1800 can leave the client
            # believing a dead token is live for 25 minutes.
            raise QuestradeAuthError(
                f"OAuth2 response missing required field(s): {', '.join(missing)}."
            )

        try:
            expires_in = float(data["expires_in"])
        except (TypeError, ValueError):
            raise QuestradeAuthError(
                "OAuth2 response had a non-numeric expires_in: "
                f"{data['expires_in']!r}"
            ) from None
        if expires_in <= 0:
            raise QuestradeAuthError(
                f"OAuth2 response reported expires_in={expires_in}; refusing to "
                "use an already-expired session."
            )

        token = QuestradeAuthToken(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            api_server=normalize_api_server(data["api_server"]),
            token_type=str(data.get("token_type") or "Bearer"),
            expires_at=self._wall_clock() + expires_in,
            expires_in=expires_in,
            monotonic_deadline=self._monotonic() + expires_in,
        )

        if self._token_persist_fn is not None:
            try:
                self._token_persist_fn(token)
            except Exception as exc:  # noqa: BLE001 - re-raised as a fatal auth error
                raise QuestradeAuthError(
                    "Rotated refresh token could not be persisted; the previous "
                    "token is already spent, so continuing would strand this "
                    f"application without API access. Persistence error: {exc}"
                ) from exc

        self.auth_token = token
        logger.info(
            "Questrade session established (env=%s, api_server=%s, expires_in=%.0fs)",
            "practice" if self.practice else "live",
            token.api_server,
            expires_in,
        )
        return token

    def require_token(self) -> QuestradeAuthToken:
        """Return the live session, raising if absent or expired."""
        if self.auth_token is None:
            raise QuestradeAuthError(
                "Not authenticated. Call refresh_access_token() first."
            )
        if self.is_token_expired():
            raise QuestradeAuthError(
                "Access token has expired (or is within the skew window). "
                "Redeem the stored refresh token before making further calls."
            )
        return self.auth_token

    def seconds_to_expiry(self) -> float:
        """Seconds until the access token expires, using the monotonic deadline."""
        if self.auth_token is None:
            return 0.0
        return self.auth_token.monotonic_deadline - self._monotonic()

    def is_token_expired(self, skew_sec: Optional[float] = None) -> bool:
        """True when the token is expired or within ``skew_sec`` of expiring."""
        skew = self.expiry_skew_sec if skew_sec is None else float(skew_sec)
        return self.seconds_to_expiry() <= skew

    def ensure_authenticated(self) -> QuestradeAuthToken:
        """
        Refresh proactively when the session is at or near expiry.

        Uses the rotated refresh token held from the previous exchange. A
        session that has never been established cannot be recovered here -- the
        caller must supply the persisted token.
        """
        if self.auth_token is None:
            raise QuestradeAuthError(
                "No session to renew; call refresh_access_token() with the "
                "persisted refresh token first."
            )
        if self.is_token_expired():
            return self.refresh_access_token(self.auth_token.refresh_token)
        return self.auth_token

    # -- accounts ----------------------------------------------------------
    def fetch_accounts(self) -> List[QuestradeAccount]:
        """
        Fetch and register every account visible to this application.

        The registry is rebuilt rather than merged so an account that has been
        closed or removed since the last call does not linger as ``Active``.
        """
        data = self._request("GET", "v1/accounts", category=RateLimitCategory.ACCOUNT)
        if not isinstance(data, Mapping):
            raise QuestradeAPIError(
                f"GET v1/accounts returned {type(data).__name__}, expected an object."
            )

        registry: Dict[str, QuestradeAccount] = {}
        account_list: List[QuestradeAccount] = []
        for raw in data.get("accounts", []):
            if not isinstance(raw, Mapping) or not raw.get("number"):
                raise QuestradeAPIError(
                    f"Account record missing required 'number' field: {raw!r}"
                )
            account = QuestradeAccount(
                account_number=str(raw["number"]),
                account_type=AccountType.from_api(
                    raw.get("type"), strict=self.strict_account_types
                ),
                is_primary=bool(raw.get("isPrimary", False)),
                status=AccountStatus.from_api(raw.get("status")),
                is_billing=bool(raw.get("isBilling", False)),
                client_account_type=str(raw.get("clientAccountType", "")),
            )
            registry[account.account_number] = account
            account_list.append(account)

        self.accounts = registry
        return account_list

    def get_account(self, account_number: str) -> QuestradeAccount:
        """Look up a registered account, raising if ``fetch_accounts`` never saw it."""
        try:
            return self.accounts[str(account_number)]
        except KeyError:
            raise QuestradeAPIError(
                f"Account {account_number!r} is not registered. Call "
                "fetch_accounts() first; never assume an account number is valid."
            ) from None

    # -- pre-trade eligibility --------------------------------------------
    def check_order_eligibility(
        self, account_number: str, order_side: str
    ) -> OrderEligibility:
        """
        Decide whether ``order_side`` may be routed to ``account_number``.

        Rules, in order:

        * The account must be ``Active``. ``Liquidate Only`` permits
          position-reducing sides only; every other status denies outright.
        * ``Short`` and ``Cov`` require borrowed stock, so they are permitted
          only in a ``Margin`` account. Registered plans cannot borrow --
          ITA 146(4)(a) taxes an RRSP trust that borrows and ITA 146.2(2)(f)
          forbids a TFSA trust from borrowing -- and a ``Cash`` account has no
          margin facility. An ``UNKNOWN`` type is denied for the same reason.
        * ``STO`` (writing an option) returns ``REVIEW_REQUIRED`` outside a
          ``Margin`` account: whether a *covered* write is permitted depends on
          Questrade account approvals the API does not expose, and this client
          will not guess in either direction.

        ``order_side`` must be one of Questrade's documented Order Side values;
        anything else is a programming error and raises ``ValueError``. Note
        that ``"SellShort"`` is **not** a Questrade value -- the documented
        short side is ``"Short"``.
        """
        side = str(order_side)
        if side not in ORDER_SIDES:
            raise ValueError(
                f"{side!r} is not a documented Questrade order side. "
                f"Expected one of {sorted(ORDER_SIDES)}."
            )
        account = self.get_account(account_number)

        def result(eligibility: Eligibility, reason: str) -> OrderEligibility:
            if eligibility is not Eligibility.ALLOWED:
                logger.warning(
                    "Order side %s %s for account %s (%s): %s",
                    side,
                    eligibility.value,
                    account.account_number,
                    account.account_type.value,
                    reason,
                )
            return OrderEligibility(
                eligibility=eligibility,
                reason=reason,
                account_number=account.account_number,
                account_type=account.account_type,
                order_side=side,
            )

        if account.status in _LIQUIDATE_ONLY_STATUSES:
            if side not in POSITION_REDUCING_SIDES:
                return result(
                    Eligibility.DENIED,
                    f"account status is {account.status.value}; only "
                    "position-reducing sides may be submitted",
                )
        elif account.status not in _ORDER_ACCEPTING_STATUSES:
            return result(
                Eligibility.DENIED,
                f"account status is {account.status.value}, not Active",
            )

        if side in BORROW_REQUIRING_SIDES and not account.can_borrow:
            if account.account_type is AccountType.UNKNOWN:
                reason = (
                    "account type is not recognised by this client, so it cannot "
                    "be confirmed as margin-capable"
                )
            elif account.is_registered:
                reason = (
                    f"{account.account_type.value} is a registered plan and may "
                    "not borrow (ITA 146(4)(a) / 146.2(2)(f)); short positions "
                    "require a Margin account"
                )
            else:
                reason = (
                    f"{account.account_type.value} accounts have no margin "
                    "facility; short positions require a Margin account"
                )
            return result(Eligibility.DENIED, reason)

        if side in OPTION_WRITING_SIDES and account.account_type is not AccountType.MARGIN:
            return result(
                Eligibility.REVIEW_REQUIRED,
                "option-writing eligibility in this account type depends on "
                "Questrade account approvals that the API does not expose; "
                "confirm with the broker before routing",
            )

        return result(Eligibility.ALLOWED, "permitted for this account type and status")

    def validate_order_for_account(self, account_number: str, order_action: str) -> bool:
        """
        Boolean form of :meth:`check_order_eligibility`.

        ``REVIEW_REQUIRED`` folds to ``False`` so a caller that ignores the
        richer result still fails closed. Prefer
        :meth:`check_order_eligibility` when the reason matters, or
        :meth:`assert_order_allowed` when the check must be impossible to skip.
        """
        return self.check_order_eligibility(account_number, order_action).allowed

    def assert_order_allowed(
        self, account_number: str, order_side: str
    ) -> OrderEligibility:
        """
        Raise :class:`AccountRestrictionError` unless the order is permitted.

        Use this on the submission path: a boolean return can be dropped by a
        caller, an exception cannot.
        """
        outcome = self.check_order_eligibility(account_number, order_side)
        if not outcome.allowed:
            raise AccountRestrictionError(
                f"Order side {outcome.order_side} is {outcome.eligibility.value} "
                f"for account {outcome.account_number} "
                f"({outcome.account_type.value}): {outcome.reason}"
            )
        return outcome
