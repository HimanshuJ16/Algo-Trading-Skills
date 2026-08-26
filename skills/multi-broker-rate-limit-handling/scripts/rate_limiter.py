"""
multi-broker-rate-limit-handling: multi-window token buckets (per endpoint class and
per account), strict-priority tier admission so a Tier 0 kill/cancel is never queued
behind quote polling, RFC 9110 `Retry-After`-aware full-jitter backoff, and telemetry.

Design notes that matter for correctness:

* **A budget is a set of windows, not a number.** Fyers API v3 meters 10 req/sec
  *and* 200 req/min *and* 100,000 req/day against the same counter; ICICI Breeze
  meters 100 req/min *and* 5,000 req/day globally. One bucket cannot express that,
  so a budget is a `_BucketGroup` of windows consumed all-or-nothing.
* **Some brokers have no per-endpoint split at all.** Alpaca's trading API
  (200 req/min per account) and Breeze bill every endpoint against one budget.
  Registering only per-endpoint buckets for those brokers over-issues; declare the
  shared cap with `register_account_bucket`.
* **Rate-limit errors are classified structurally, never by substring.** Matching
  ``"429" in str(exc)`` treats ``"Order 429123 rejected"`` as a throttle and retries
  a live order placement. See `default_rate_limit_classifier`.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import IntEnum
import logging
import random
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "CallTier",
    "RateLimitError",
    "RateLimitWaitTimeout",
    "UnregisteredBudgetError",
    "RateLimiterMetrics",
    "TokenBucket",
    "MultiBrokerRateLimiter",
    "TieredCallQueue",
    "default_rate_limit_classifier",
    "parse_retry_after",
    "full_jitter_backoff",
    "TIER_KILL",
    "TIER_ORDER",
    "TIER_STATUS",
    "TIER_DATA",
]

# Defaults. Every one of these is a policy choice, not a broker fact -- override per broker.
DEFAULT_BASE_BACKOFF_SEC = 0.2
DEFAULT_MAX_BACKOFF_SEC = 8.0
DEFAULT_MAX_WAIT_SEC = 30.0
DEFAULT_MAX_RETRY_AFTER_SEC = 60.0
_WAIT_POLL_SEC = 0.02


class CallTier(IntEnum):
    TIER_0_KILL = 0      # Emergency kill switch / risk cancellations
    TIER_1_ORDER = 1     # New order placement / modifications
    TIER_2_STATUS = 2    # Order status polling / margin checks
    TIER_3_DATA = 3      # Quotes / market data / historical backfill


# Backward compatibility constants
TIER_KILL = CallTier.TIER_0_KILL.value
TIER_ORDER = CallTier.TIER_1_ORDER.value
TIER_STATUS = CallTier.TIER_2_STATUS.value
TIER_DATA = CallTier.TIER_3_DATA.value


class RateLimitError(Exception):
    """
    Raised by (or wrapped around) a broker call that the broker throttled.

    Carrying `retry_after` lets the limiter honour server-directed pacing instead of
    guessing a delay shorter than the broker's reset window -- which is precisely what
    escalates a 429 into a ban.
    """

    def __init__(
        self,
        message: str = "rate limited",
        *,
        status_code: Optional[int] = 429,
        retry_after: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitWaitTimeout(Exception):
    """Raised when a call could not obtain capacity inside its wait deadline."""


class UnregisteredBudgetError(KeyError):
    """Raised in strict mode when no budget is registered for a broker/endpoint."""


def parse_retry_after(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """
    Parse an RFC 9110 s10.2.3 ``Retry-After`` value into seconds.

    The field permits either ``delay-seconds`` or an ``HTTP-date``; a float-only
    parser silently discards the date form and falls back to a much shorter delay.
    Past dates clamp to 0.0. Unparseable input returns None so the caller falls back
    to jittered backoff rather than to an immediate retry.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0.0, (target - reference).total_seconds())


def default_rate_limit_classifier(exc: BaseException) -> Tuple[bool, Optional[float]]:
    """
    Decide structurally whether `exc` is a broker throttle, and extract `Retry-After`.

    Returns ``(is_rate_limited, retry_after_seconds_or_None)``.

    Recognises, in order: `RateLimitError`; any exception exposing ``status_code``;
    any exception exposing a ``response`` with ``status_code``/``status`` (the shape
    `requests.HTTPError` and most broker SDK errors use). HTTP 429 (RFC 6585 s4) and
    503 are treated as throttles.

    It deliberately does **not** inspect the message text. A substring test for
    "429" matches order ids, prices and quantities, and a false positive here retries
    a call the broker may already have executed. Anything this cannot classify
    structurally propagates to the caller unretried -- pass a broker-specific
    `classify_fn` to `MultiBrokerRateLimiter` rather than loosening this.
    """
    status = getattr(exc, "status_code", None)
    retry_after_raw = getattr(exc, "retry_after", None)

    response = getattr(exc, "response", None)
    if response is not None:
        if status is None:
            status = getattr(response, "status_code", None)
            if status is None:
                status = getattr(response, "status", None)
        if retry_after_raw is None:
            headers = getattr(response, "headers", None)
            if headers is not None and hasattr(headers, "get"):
                retry_after_raw = headers.get("Retry-After") or headers.get("retry-after")

    if isinstance(status, bool) or not isinstance(status, int):
        if isinstance(exc, RateLimitError):
            return (True, parse_retry_after(retry_after_raw))
        return (False, None)
    if status in (429, 503):
        return (True, parse_retry_after(retry_after_raw))
    return (False, None)


def full_jitter_backoff(
    attempt: int,
    *,
    base_sec: float = DEFAULT_BASE_BACKOFF_SEC,
    cap_sec: float = DEFAULT_MAX_BACKOFF_SEC,
    rand_fn: Callable[[float, float], float] = random.uniform,
) -> float:
    """
    Full-jitter exponential backoff: ``uniform(0, min(cap, base * 2**attempt))``.

    Additive jitter under a cap (``min(cap, base * 2**n + jitter)``) degenerates to
    exactly `cap` for every client once the exponential term passes the cap, so a
    fleet throttled together retries in lockstep at the moment the herd is largest.
    Spreading over the whole interval is what decorrelates the retries.
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base_sec <= 0 or cap_sec <= 0:
        raise ValueError("base_sec and cap_sec must be > 0")
    ceiling = min(cap_sec, base_sec * (2.0 ** attempt))
    return rand_fn(0.0, ceiling)


@dataclass
class RateLimiterMetrics:
    """Counters for tuning polling frequency down *before* production 429s."""

    total_calls: int = 0
    tier_0_bypasses: int = 0
    rate_limit_hits_429: int = 0
    total_backoff_sec: float = 0.0
    total_wait_sec: float = 0.0
    retry_after_honored: int = 0
    wait_timeouts: int = 0
    calls_by_tier: Dict[int, int] = field(default_factory=dict)
    rate_limit_hits_by_tier: Dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "tier_0_bypasses": self.tier_0_bypasses,
            "rate_limit_hits_429": self.rate_limit_hits_429,
            "total_backoff_sec": round(self.total_backoff_sec, 3),
            "total_wait_sec": round(self.total_wait_sec, 3),
            "retry_after_honored": self.retry_after_honored,
            "wait_timeouts": self.wait_timeouts,
            "calls_by_tier": dict(sorted(self.calls_by_tier.items())),
            "rate_limit_hits_by_tier": dict(sorted(self.rate_limit_hits_by_tier.items())),
        }


class TokenBucket:
    """
    Thread-safe token bucket over a single window.

    `rate_per_sec` is the sustained refill rate, `capacity` the burst allowance.
    Uses a monotonic clock by default: a wall clock can step backwards under NTP
    correction, which makes the elapsed interval negative and silently *removes*
    tokens.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        name: str = "",
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError(f"rate_per_sec must be > 0 (got {rate_per_sec!r})")
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0 (got {capacity!r})")
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.name = name
        self._time_fn = time_fn
        self.last = time_fn()
        self.lock = threading.Lock()

    @classmethod
    def per_interval(
        cls,
        requests: float,
        interval_sec: float,
        *,
        capacity: Optional[float] = None,
        time_fn: Callable[[], float] = time.monotonic,
        name: str = "",
    ) -> "TokenBucket":
        """Build a window from a documented "N requests per interval" limit."""
        if interval_sec <= 0:
            raise ValueError("interval_sec must be > 0")
        if requests <= 0:
            raise ValueError("requests must be > 0")
        return cls(
            rate_per_sec=requests / interval_sec,
            capacity=requests if capacity is None else capacity,
            time_fn=time_fn,
            name=name,
        )

    # -- internals, called with self.lock already held -------------------------
    def _refill_locked(self) -> None:
        now = self._time_fn()
        elapsed = now - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last = now

    def _wait_time_locked(self, tokens: float) -> float:
        if self.tokens >= tokens:
            return 0.0
        return (tokens - self.tokens) / self.rate

    # -- public ---------------------------------------------------------------
    def try_consume(self, tokens: float = 1.0) -> bool:
        """Consume `tokens` if available; return False *without consuming* otherwise."""
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        with self.lock:
            self._refill_locked()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def wait_time_for(self, tokens: float = 1.0) -> float:
        """
        Seconds until `tokens` would be available.

        This is a *probe*, not permission to send: a concurrent worker may take the
        token the returned wait was sized for, so always re-check after sleeping.
        """
        if tokens > self.capacity:
            raise ValueError(
                f"request of {tokens} tokens exceeds bucket capacity {self.capacity}"
            )
        with self.lock:
            self._refill_locked()
            return self._wait_time_locked(tokens)


class _BucketGroup:
    """
    Several windows metering one budget, consumed all-or-nothing.

    Partial consumption is the bug this exists to prevent: deducting from the
    per-second window and then failing the per-day window leaks a token from the
    per-second window on every rejected attempt, which under-throttles the caller.

    Bucket locks are always taken in a globally consistent order (by object id), so
    groups that share buckets -- an endpoint budget and the account-wide budget --
    cannot deadlock against each other.
    """

    def __init__(self, buckets: Sequence[TokenBucket], label: str = "") -> None:
        if not buckets:
            raise ValueError("a budget needs at least one window")
        self.buckets: Tuple[TokenBucket, ...] = tuple(buckets)
        self.label = label
        self._ordered = sorted(self.buckets, key=id)
        # The narrowest window's capacity: a request larger than this can never be
        # satisfied, however long the caller waits.
        self.max_tokens: float = min(b.capacity for b in self.buckets)

    def _lock_all(self) -> List[threading.Lock]:
        locks = [b.lock for b in self._ordered]
        for lk in locks:
            lk.acquire()
        return locks

    @staticmethod
    def _unlock_all(locks: List[threading.Lock]) -> None:
        for lk in reversed(locks):
            lk.release()

    def try_consume(self, tokens: float = 1.0) -> bool:
        locks = self._lock_all()
        try:
            for b in self.buckets:
                b._refill_locked()
            if any(b.tokens < tokens for b in self.buckets):
                return False
            for b in self.buckets:
                b.tokens -= tokens
            return True
        finally:
            self._unlock_all(locks)

    def wait_time_for(self, tokens: float = 1.0) -> float:
        locks = self._lock_all()
        try:
            for b in self.buckets:
                b._refill_locked()
            return max(b._wait_time_locked(tokens) for b in self.buckets)
        finally:
            self._unlock_all(locks)

    def binding_window(self, tokens: float = 1.0) -> Optional[str]:
        """Name of the window currently gating this budget -- the tuning signal."""
        locks = self._lock_all()
        try:
            for b in self.buckets:
                b._refill_locked()
            worst = max(self.buckets, key=lambda b: b._wait_time_locked(tokens))
            if worst._wait_time_locked(tokens) <= 0:
                return None
            return worst.name or self.label or None
        finally:
            self._unlock_all(locks)


class _PriorityGate:
    """
    Strict-priority admission across tiers contending for one broker's budget.

    Sorting a single FIFO queue does not stop an in-flight low-priority call from
    taking the token a Tier 1 order is waiting for. This gate makes a waiter
    ineligible to attempt consumption while any strictly-higher-priority (lower
    numbered) waiter is pending, so a 40-instrument quote burst cannot win the token
    race against an order placement.

    Consequence to be aware of: while a Tier 1 waiter is blocked on an empty budget,
    Tier 2/3 waiters are held too. That is intentional, and it is bounded by each
    waiter's own `max_wait_sec` deadline -- which is why the deadline is mandatory.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: Counter = Counter()

    def enter(self, tier: int) -> None:
        with self._lock:
            self._waiting[tier] += 1

    def leave(self, tier: int) -> None:
        with self._lock:
            self._waiting[tier] -= 1
            if self._waiting[tier] <= 0:
                del self._waiting[tier]

    def may_proceed(self, tier: int) -> bool:
        with self._lock:
            return not any(t < tier for t in self._waiting)


class MultiBrokerRateLimiter:
    """
    Per-broker budget manager with Tier 0 bypass, strict tier priority, and
    `Retry-After`-aware full-jitter backoff.

    Budgets are keyed ``"<broker>:<endpoint_category>"``. An optional per-broker
    account budget is consumed *in addition* to the endpoint budget, for brokers that
    meter every endpoint against one account-wide cap.
    """

    def __init__(
        self,
        alert_fn: Optional[Callable[[str], None]] = None,
        *,
        strict: bool = False,
        default_rate_per_sec: float = 10.0,
        default_capacity: float = 10.0,
        base_backoff_sec: float = DEFAULT_BASE_BACKOFF_SEC,
        max_backoff_sec: float = DEFAULT_MAX_BACKOFF_SEC,
        max_wait_sec: float = DEFAULT_MAX_WAIT_SEC,
        max_retry_after_sec: float = DEFAULT_MAX_RETRY_AFTER_SEC,
        classify_fn: Callable[
            [BaseException], Tuple[bool, Optional[float]]
        ] = default_rate_limit_classifier,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        rand_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        """
        `strict=True` makes an unregistered broker/endpoint raise instead of silently
        inventing a budget. Prefer it in production: a typo (``"quotes"`` where the
        registered category is ``"quote"``) otherwise gets the permissive default
        rather than the 1 req/sec Kite Connect actually allows for quotes.

        `time_fn`, `sleep_fn` and `rand_fn` are injectable so pacing and backoff are
        deterministically testable without real sleeping.
        """
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.strict = strict
        self.default_rate_per_sec = default_rate_per_sec
        self.default_capacity = default_capacity
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.max_wait_sec = max_wait_sec
        self.max_retry_after_sec = max_retry_after_sec
        self.classify_fn = classify_fn
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._rand_fn = rand_fn

        self.metrics = RateLimiterMetrics()
        self._metrics_lock = threading.Lock()
        self._registry_lock = threading.RLock()
        self._budgets: Dict[str, _BucketGroup] = {}
        self._account_budgets: Dict[str, _BucketGroup] = {}
        self._combined: Dict[str, _BucketGroup] = {}
        self._gates: Dict[str, _PriorityGate] = {}

    # -- registration ---------------------------------------------------------
    @staticmethod
    def _key(broker: str, endpoint_category: str) -> str:
        return f"{broker.lower()}:{endpoint_category.lower()}"

    def register_endpoint_bucket(
        self,
        broker: str,
        endpoint_category: str,
        rate_per_sec: float,
        capacity: float,
    ) -> None:
        """Register a single-window budget for one endpoint class."""
        key = self._key(broker, endpoint_category)
        bucket = TokenBucket(
            rate_per_sec, capacity, time_fn=self._time_fn, name=f"{key}@{rate_per_sec:g}/s"
        )
        with self._registry_lock:
            self._budgets[key] = _BucketGroup([bucket], label=key)
            self._combined.clear()

    def register_endpoint_windows(
        self,
        broker: str,
        endpoint_category: str,
        windows: Iterable[Tuple[float, float]],
    ) -> None:
        """
        Register a multi-window budget from ``(requests, interval_seconds)`` pairs.

        Use this wherever a broker stacks windows on one counter -- e.g. Fyers v3
        (10/sec, 200/min, 100,000/day). A single per-second bucket lets a burst pass
        that the per-minute or per-day counter will reject.
        """
        key = self._key(broker, endpoint_category)
        buckets = [
            TokenBucket.per_interval(
                req, interval, time_fn=self._time_fn, name=f"{key}@{req:g}/{interval:g}s"
            )
            for req, interval in windows
        ]
        with self._registry_lock:
            self._budgets[key] = _BucketGroup(buckets, label=key)
            self._combined.clear()

    def register_account_bucket(
        self,
        broker: str,
        windows: Iterable[Tuple[float, float]],
    ) -> None:
        """
        Register a broker-wide budget consumed by *every* endpoint of that broker.

        Required where the documented cap is global rather than per endpoint --
        Alpaca's trading API (200 req/min per account) and ICICI Breeze
        (100 req/min and 5,000 req/day across all endpoints) both work this way, and
        per-endpoint buckets alone will over-issue against them.
        """
        broker_key = broker.lower()
        buckets = [
            TokenBucket.per_interval(
                req,
                interval,
                time_fn=self._time_fn,
                name=f"{broker_key}:account@{req:g}/{interval:g}s",
            )
            for req, interval in windows
        ]
        with self._registry_lock:
            self._account_budgets[broker_key] = _BucketGroup(
                buckets, label=f"{broker_key}:account"
            )
            self._combined.clear()

    # -- lookup ---------------------------------------------------------------
    def _get_endpoint_budget(self, broker: str, endpoint_category: str) -> _BucketGroup:
        key = self._key(broker, endpoint_category)
        with self._registry_lock:
            group = self._budgets.get(key)
            if group is not None:
                return group
            if self.strict:
                raise UnregisteredBudgetError(
                    f"no rate-limit budget registered for '{key}'; register one, or "
                    f"construct MultiBrokerRateLimiter(strict=False) to accept a default"
                )
            logger.warning(
                "No rate-limit budget registered for '%s'; falling back to %.3g req/sec. "
                "This default is a guess, not a documented broker limit.",
                key,
                self.default_rate_per_sec,
            )
            bucket = TokenBucket(
                self.default_rate_per_sec,
                self.default_capacity,
                time_fn=self._time_fn,
                name=f"{key}@default",
            )
            group = _BucketGroup([bucket], label=key)
            self._budgets[key] = group
            self._combined.clear()
            return group

    def _get_budget(self, broker: str, endpoint_category: str) -> _BucketGroup:
        """
        Endpoint windows and any account-wide windows as one atomic budget.

        Combining them into a single group (rather than consuming two groups in
        sequence) is what makes admission all-or-nothing: consuming the endpoint
        token and then failing the account cap would leak endpoint capacity on every
        rejected attempt.
        """
        key = self._key(broker, endpoint_category)
        with self._registry_lock:
            combined = self._combined.get(key)
            if combined is not None:
                return combined
            endpoint = self._get_endpoint_budget(broker, endpoint_category)
            account = self._account_budgets.get(broker.lower())
            buckets = list(endpoint.buckets)
            if account is not None:
                buckets.extend(account.buckets)
            combined = _BucketGroup(buckets, label=key)
            self._combined[key] = combined
            return combined

    def _get_gate(self, broker: str) -> _PriorityGate:
        broker_key = broker.lower()
        with self._registry_lock:
            gate = self._gates.get(broker_key)
            if gate is None:
                gate = _PriorityGate()
                self._gates[broker_key] = gate
            return gate

    # -- metrics --------------------------------------------------------------
    def _bump(self, field_name: str, amount: float = 1) -> None:
        with self._metrics_lock:
            setattr(self.metrics, field_name, getattr(self.metrics, field_name) + amount)

    def _bump_tier(self, mapping_name: str, tier: int) -> None:
        with self._metrics_lock:
            mapping = getattr(self.metrics, mapping_name)
            mapping[tier] = mapping.get(tier, 0) + 1

    # -- execution ------------------------------------------------------------
    def execute_call(
        self,
        broker: str,
        endpoint_category: str,
        tier: int,
        call_fn: Callable[[], Any],
        max_retries: int = 3,
        *,
        tokens: float = 1.0,
        max_wait_sec: Optional[float] = None,
        on_rate_limited: Optional[Callable[[int, Optional[float]], None]] = None,
    ) -> Any:
        """
        Run `call_fn` under this broker's budget for the given criticality tier.

        Tier 0 never waits: it consumes a token if one is free and dispatches
        regardless, alerting when the budget was exhausted. Eating a 429 on a
        kill-switch cancel is strictly better than not sending it -- the alert exists
        so an operator can take the manual path if the broker does reject it.

        Tiers 1-3 wait for capacity under strict tier priority, then retry on a
        *structurally classified* rate-limit response, honouring `Retry-After` and
        otherwise sleeping full-jitter backoff.

        Retrying is safe here only because a classified throttle means the broker
        rejected the request. A timeout, a connection reset, or any error this cannot
        classify is re-raised unretried: the broker may already have accepted the
        order, and resolving that ambiguity is `order-placement-idempotency`'s job,
        not this limiter's.

        Raises `RateLimitWaitTimeout` if capacity does not arrive inside the deadline,
        and re-raises the broker's error once the retry budget is exhausted.
        """
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if tokens <= 0:
            raise ValueError("tokens must be > 0")

        tier_value = int(tier)
        self._bump("total_calls")
        self._bump_tier("calls_by_tier", tier_value)

        budget = self._get_budget(broker, endpoint_category)
        label = self._key(broker, endpoint_category)

        # An unsatisfiable request must fail immediately. Left to the wait loop it
        # would burn the whole deadline and then report a timeout, disguising a
        # configuration error as transient congestion.
        if tokens > budget.max_tokens:
            raise ValueError(
                f"request of {tokens:g} tokens exceeds the capacity of the narrowest "
                f"window in budget '{label}' ({budget.max_tokens:g}); it can never be "
                f"granted"
            )

        if tier_value == CallTier.TIER_0_KILL.value:
            if not budget.try_consume(tokens):
                self._bump("tier_0_bypasses")
                self.alert_fn(
                    f"CRITICAL: Tier 0 call to '{label}' dispatched with an exhausted "
                    f"rate-limit budget; the broker may reject it. Prepare the manual "
                    f"intervention path."
                )
            return call_fn()

        deadline_budget = self.max_wait_sec if max_wait_sec is None else max_wait_sec
        gate = self._get_gate(broker)

        attempt = 0
        while True:
            self._acquire(budget, gate, tier_value, tokens, deadline_budget, label)
            try:
                return call_fn()
            except BaseException as exc:  # noqa: BLE001 -- re-raised unless classified
                is_limited, retry_after = self.classify_fn(exc)
                if not is_limited:
                    raise

                self._bump("rate_limit_hits_429")
                self._bump_tier("rate_limit_hits_by_tier", tier_value)
                if on_rate_limited is not None:
                    on_rate_limited(tier_value, retry_after)

                attempt += 1
                if attempt > max_retries:
                    raise

                if retry_after is not None:
                    if retry_after > self.max_retry_after_sec:
                        self.alert_fn(
                            f"'{label}' (Tier {tier_value}) returned Retry-After="
                            f"{retry_after:.1f}s, beyond max_retry_after_sec="
                            f"{self.max_retry_after_sec:.1f}s. Not sleeping through it."
                        )
                        raise
                    delay = retry_after
                    self._bump("retry_after_honored")
                else:
                    delay = full_jitter_backoff(
                        attempt,
                        base_sec=self.base_backoff_sec,
                        cap_sec=self.max_backoff_sec,
                        rand_fn=self._rand_fn,
                    )

                self._bump("total_backoff_sec", delay)
                logger.warning(
                    "Rate limited on %s (Tier %d), attempt %d/%d. Backing off %.2fs%s.",
                    label,
                    tier_value,
                    attempt,
                    max_retries,
                    delay,
                    " (server Retry-After)" if retry_after is not None else " (full jitter)",
                )
                if delay > 0:
                    self._sleep_fn(delay)

    def _acquire(
        self,
        budget: _BucketGroup,
        gate: _PriorityGate,
        tier: int,
        tokens: float,
        max_wait_sec: float,
        label: str,
    ) -> None:
        """Block until the budget admits `tokens`, respecting strict tier priority."""
        start = self._time_fn()
        gate.enter(tier)
        try:
            while True:
                if gate.may_proceed(tier) and budget.try_consume(tokens):
                    waited = self._time_fn() - start
                    if waited > 0:
                        self._bump("total_wait_sec", waited)
                    return

                remaining = max_wait_sec - (self._time_fn() - start)
                if remaining <= 0:
                    self._bump("wait_timeouts")
                    binding = budget.binding_window(tokens)
                    raise RateLimitWaitTimeout(
                        f"Tier {tier} call to '{label}' did not obtain rate-limit "
                        f"capacity within {max_wait_sec:.2f}s"
                        + (f" (binding window: {binding})" if binding else "")
                    )

                # A computed wait is a probe, not permission to send: sleep at most
                # the shortfall, then re-check, because another worker (or a
                # higher-priority tier) may take the token this wait was sized for.
                # Sleeping through the injected `sleep_fn` (rather than parking on a
                # condition variable) keeps pacing deterministic under a test clock;
                # the `_WAIT_POLL_SEC` floor bounds how late a gate-blocked waiter
                # notices that the tier ahead of it has finished.
                needed = budget.wait_time_for(tokens)
                self._sleep_fn(max(0.0, min(remaining, max(needed, _WAIT_POLL_SEC))))
        finally:
            gate.leave(tier)

    def snapshot(self) -> Dict[str, Any]:
        """Metrics plus registered budget labels, for the observability pipeline."""
        with self._registry_lock:
            endpoint_keys = sorted(self._budgets)
            account_keys = sorted(self._account_budgets)
        with self._metrics_lock:
            metrics = self.metrics.to_dict()
        return {
            "metrics": metrics,
            "endpoint_budgets": endpoint_keys,
            "account_budgets": account_keys,
        }


class TieredCallQueue:
    """
    Simple per-tier FIFO queues drained highest-priority-first.

    Retained for backward compatibility. It is a batching helper, not an admission
    controller: it offers no wait deadline, no backoff, no rate-limit classification
    and no account-wide budget. Use `MultiBrokerRateLimiter` for live traffic.
    """

    def __init__(self, buckets: Dict[int, TokenBucket]) -> None:
        self.buckets: Dict[int, TokenBucket] = buckets
        self.queues: Dict[int, Deque[Callable[[], Any]]] = {
            tier: deque() for tier in buckets
        }

    def enqueue(self, tier: int, call_fn: Callable[[], Any]) -> None:
        if tier not in self.queues:
            raise KeyError(f"no bucket registered for tier {tier!r}")
        self.queues[tier].append(call_fn)

    def drain_tier(self, tier: int) -> List[Any]:
        bucket = self.buckets[tier]
        results: List[Any] = []
        q = self.queues[tier]
        while q and bucket.try_consume():
            results.append(q.popleft()())
        return results

    def drain_all_by_priority(self) -> List[Any]:
        results: List[Any] = []
        for tier in sorted(self.queues):
            results.extend(self.drain_tier(tier))
        return results
