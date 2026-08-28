"""
real-time-vs-delayed-data-entitlement-handling: fail-closed gate deciding whether
a consumer gets a real-time stream, a delayed stream, or nothing -- and refusing
live order execution driven by a delayed feed.

What this module is and is not
------------------------------
It is a **fail-closed tiering gate**. Given a subscriber's entitlement tier and an
inbound market data request, it decides whether to serve real-time, to serve
delayed (and with what delay interval and what display label), or to deny -- and
records an auditable reason either way. It refuses to let an execution request run
off a delayed feed.

It is **not** a per-venue licence gate (depth of book, non-display activity
category, subscriber classification integrity -- see
``market-data-entitlement-and-licensing-per-venue``), **not** a fee calculator,
**not** a latency monitor, and **not** a substitute for the vendor's own
permissioning system. It enforces the tier a compliance owner has encoded; it
cannot read the paperwork or measure the wire.

Why the delay interval is per venue and never hard-coded
--------------------------------------------------------
"Delayed means 15 minutes" is true at some venues and false at others, and the
difference decides which licence -- and which fee schedule -- the feed falls under:

* **CME Group**: "Real time Information is made available within ten (10) minutes
  of initial transmission"; "Delayed Information is made available more than ten
  (10) minutes, but less than eight (8) hours after initial transmission." A feed
  throttled by 8 minutes is still *real-time* Information at CME.
* **ICE Futures Europe**: "The Exchange defines real-time data as any market data
  that is < 10 minutes old", and delayed pricing data is "market prices of traded
  contracts transmitted more than 10 mins from publication via the API."
* **Nasdaq / ESMA terminology**: real-time data is "market data delivered with a
  delay of less than 15 minutes after publication"; delayed data is "market data
  made available 15 minutes after publication."

So the boundary is a per-venue input (``VenueDelayPolicy``), the engine refuses to
serve a delayed stream for a venue it has no policy for, and it refuses to call a
feed "delayed" when the configured delay does not actually clear that venue's
boundary.

Why blocking execution on a delayed feed is an execution-safety control
-----------------------------------------------------------------------
It is **not** a claim that a regulator forbids trading on delayed prices. Some
venues explicitly license automated use of delayed Information -- CME requires
Non-Display Use "of Real Time and Delayed Information" to be reported per
Application, which presupposes the use exists. The gate is here because a
strategy priced off quotes that are ten or fifteen minutes stale executes against
a book it cannot see, and because a delayed entitlement is the usual sign that the
real-time licence for that venue was never bought. If your strategy legitimately
trades off delayed data under a licence that permits it, this gate is the wrong
tool -- see the SKILL's "When NOT to Use".

Delayed does not mean unlicensed or free
----------------------------------------
Nasdaq's Display Requirements Policy notes that where a product is eligible for
delayed pricing "there may not be a charge for the usage of the delayed data,
*depending upon the product selected*", and still requires a Prominent Delay
Message on every display of it. The free-after-15-minutes obligation in MiFIR
Article 13(1) binds EU trading venues; it is not a global rule. This engine
therefore emits the display label a delayed stream must carry, and never treats
"delayed" as "outside the licence".

Determinism and concurrency
---------------------------
The engine holds no mutable decision state: the venue policy table is built once
at construction and read-only thereafter, so concurrent callers cannot race. The
same inputs always produce the same report.

References: see ``references/standards.md``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable as _IterableABC
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Entitlement tiers -------------------------------------------------------
TIER_REAL_TIME = "REAL_TIME"
TIER_DELAYED = "DELAYED"

#: The only tiers this engine knows how to reason about. Anything else -- a typo
#: such as ``REALTIME``, an empty string, a vendor-specific label -- is denied.
#: It must never fall through to one of the serving paths: an unclassified tier
#: cannot be shown to be licensed, and (before v2.0.0) a tier that merely failed
#: to equal ``DELAYED`` skipped the execution block entirely.
RECOGNISED_TIERS: FrozenSet[str] = frozenset({TIER_REAL_TIME, TIER_DELAYED})

# --- Subscriber classification -----------------------------------------------
# Same vocabulary as market-data-entitlement-and-licensing-per-venue, which owns
# the integrity rules (only natural persons may qualify as Non-Professional,
# re-verification cadence, and so on). Here the classification is validated and
# carried onto the audit record; it is not re-adjudicated.
CLASSIFICATION_PROFESSIONAL = "PROFESSIONAL"
CLASSIFICATION_NON_PROFESSIONAL = "NON_PROFESSIONAL"
RECOGNISED_CLASSIFICATIONS: FrozenSet[str] = frozenset(
    {CLASSIFICATION_PROFESSIONAL, CLASSIFICATION_NON_PROFESSIONAL}
)

# --- Audit statuses ----------------------------------------------------------
STATUS_REALTIME_ENTITLED = "REALTIME_STREAM_ENTITLED"
STATUS_DELAYED_ENTITLED = "DELAYED_STREAM_ENTITLED"
STATUS_EXCHANGE_NOT_SUBSCRIBED = "EXCHANGE_NOT_SUBSCRIBED"
STATUS_LIVE_TRADING_BLOCKED = "LIVE_TRADING_BLOCKED_DELAYED_DATA"
STATUS_UNRECOGNISED_TIER = "ENTITLEMENT_DENIED_UNRECOGNISED_TIER"
STATUS_NO_DELAY_POLICY = "DELAYED_STREAM_BLOCKED_NO_DELAY_POLICY"
STATUS_INSUFFICIENT_DELAY = "DELAYED_STREAM_BLOCKED_INSUFFICIENT_DELAY"
STATUS_DELAY_EXCEEDS_POLICY = "DELAYED_STREAM_BLOCKED_DELAY_EXCEEDS_POLICY"

#: Every status this engine can return. Callers routing on ``status`` MUST treat
#: an unrecognised value as a denial rather than falling through to approval.
ALL_STATUSES: Tuple[str, ...] = (
    STATUS_REALTIME_ENTITLED,
    STATUS_DELAYED_ENTITLED,
    STATUS_EXCHANGE_NOT_SUBSCRIBED,
    STATUS_LIVE_TRADING_BLOCKED,
    STATUS_UNRECOGNISED_TIER,
    STATUS_NO_DELAY_POLICY,
    STATUS_INSUFFICIENT_DELAY,
    STATUS_DELAY_EXCEEDS_POLICY,
)

#: Nasdaq Display Requirements Policy: on a ticker "the delay message should be
#: interspersed with the market data at least every 90 seconds". Used as the
#: default refresh cadence; tighten it if a venue you use demands more.
DEFAULT_DELAY_MESSAGE_REFRESH_SECONDS = 90


class EntitlementConfigurationError(ValueError):
    """Raised when an entitlement record, request, or venue policy is invalid.

    Subclasses ``ValueError`` so callers already guarding the call with
    ``except ValueError`` keep working.

    Structurally invalid input is a defect, not a compliance decision. Evaluating
    it anyway produces an authoritative-looking ``REALTIME_STREAM_ENTITLED``
    backed by nothing.
    """


# --- validation helpers ------------------------------------------------------

def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntitlementConfigurationError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise EntitlementConfigurationError(f"{name} must be a bool, got {value!r}")
    return value


def _require_positive_int(value: object, name: str, minimum: int = 1) -> int:
    # bool is a subclass of int; True would silently become 1 minute.
    if isinstance(value, bool) or not isinstance(value, int):
        raise EntitlementConfigurationError(f"{name} must be an int, got {value!r}")
    if value < minimum:
        raise EntitlementConfigurationError(f"{name} must be >= {minimum}, got {value}")
    return value


def _normalise_token(value: object, name: str) -> str:
    return _require_non_empty(value, name).upper()


def _require_member(value: object, name: str, allowed: Iterable[str]) -> str:
    token = _normalise_token(value, name)
    allowed_set = set(allowed)
    if token not in allowed_set:
        raise EntitlementConfigurationError(
            f"{name} must be one of {sorted(allowed_set)}, got {token!r}")
    return token


# --- data model --------------------------------------------------------------

@dataclass(frozen=True)
class VenueDelayPolicy:
    """How one venue defines "delayed", and how a delayed stream must be labelled.

    Populate every field from the venue's own published data policy, not from a
    house convention. The numbers differ by venue and they decide which licence
    the feed falls under.
    """

    venue_id: str
    #: Delay, in whole minutes, that the firm actually applies to this venue's
    #: feed before serving it as delayed. This is what the engine reports and what
    #: the throttle must be configured to.
    delay_minutes: int
    #: Smallest delay, in whole minutes, at which this venue treats the feed as
    #: Delayed Information. Nasdaq / ESMA terminology: 15 (data delivered with a
    #: delay of less than 15 minutes is real-time). CME Group and ICE Futures
    #: Europe draw the line at *more than* ten minutes, so use 11 there -- a
    #: 10-minute throttle still yields real-time Information at those venues.
    min_delay_minutes: int = 15
    #: Largest delay still licensed as *delayed* rather than end-of-day or
    #: historical Information, in whole minutes. CME Group caps Delayed
    #: Information at less than eight hours (479 minutes); beyond that a separate
    #: licence applies. ``None`` means the upper bound is not tracked here and the
    #: engine will not gate on it.
    max_delay_minutes: Optional[int] = None
    #: The Prominent Delay Message this venue's delayed data must be displayed
    #: with, verbatim. Nasdaq's own examples: "Data Delayed 15 minutes",
    #: "Del-15", "Data Delayed 24 hours".
    display_label: str = ""
    #: How often the delay message must reappear on a scrolling display, in
    #: seconds. Nasdaq requires at least every 90 seconds on a ticker.
    delay_message_refresh_seconds: int = DEFAULT_DELAY_MESSAGE_REFRESH_SECONDS
    #: Free-text citation of the policy document and version these numbers came
    #: from. Carried onto the audit record so a decision can be traced back to the
    #: paperwork it was made under.
    policy_source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "venue_id", _normalise_token(self.venue_id, "venue_id"))
        object.__setattr__(
            self, "delay_minutes", _require_positive_int(self.delay_minutes, "delay_minutes"))
        object.__setattr__(
            self, "min_delay_minutes",
            _require_positive_int(self.min_delay_minutes, "min_delay_minutes"))
        if self.max_delay_minutes is not None:
            _require_positive_int(self.max_delay_minutes, "max_delay_minutes")
            if self.max_delay_minutes < self.min_delay_minutes:
                raise EntitlementConfigurationError(
                    f"max_delay_minutes ({self.max_delay_minutes}) must be >= min_delay_minutes "
                    f"({self.min_delay_minutes}) for venue {self.venue_id}")
        # A delayed stream that carries no delay message breaches the display
        # requirement on the day it is served, so the label is mandatory rather
        # than defaulted to something plausible.
        object.__setattr__(
            self, "display_label", _require_non_empty(self.display_label, "display_label"))
        object.__setattr__(
            self, "delay_message_refresh_seconds",
            _require_positive_int(
                self.delay_message_refresh_seconds, "delay_message_refresh_seconds"))
        if not isinstance(self.policy_source, str):
            raise EntitlementConfigurationError(
                f"policy_source must be a string, got {self.policy_source!r}")


@dataclass
class UserEntitlement:
    """The tier and venue set a subscriber is entitled to.

    Every field is an assertion the compliance owner must be able to support from
    an executed agreement or subscriber record.
    """

    user_id: str
    #: ``PROFESSIONAL`` or ``NON_PROFESSIONAL``. Validated and recorded here; the
    #: integrity rules behind the declaration belong to
    #: ``market-data-entitlement-and-licensing-per-venue``.
    subscriber_type: str
    #: Venues the subscriber is entitled to at ``entitlement_tier``. Any iterable
    #: of venue ids -- list, tuple, set -- compared case-insensitively. A bare
    #: string is rejected rather than iterated character by character.
    subscribed_exchanges: Iterable[str]
    #: ``REAL_TIME`` or ``DELAYED``. Anything else is denied.
    entitlement_tier: str


@dataclass
class MarketDataRequest:
    """One request for market data on one instrument."""

    symbol: str
    exchange: str
    #: True when the data will drive live order entry. A delayed tier plus this
    #: flag is the case the gate exists to refuse.
    is_trading_execution_request: bool = False


@dataclass
class EntitlementAuditReport:
    """The auditable record of one tiering decision.

    ``audit_notes`` carries the human-readable reason and is intended to be
    persisted verbatim. The engine keeps no durable record of its own -- persist
    every report, denials included, as it is returned.

    The stream-shape fields describe *the stream that was authorised*. On a denial
    no stream is authorised, so ``is_delayed`` is False and ``delay_minutes`` is
    ``None``: the report never asserts a delay for data it did not release.
    """

    user_id: str
    symbol: str
    exchange: str
    is_permitted: bool
    is_delayed: bool
    #: Delay of the authorised stream in whole minutes: 0 for real-time, the
    #: venue policy's ``delay_minutes`` for delayed, ``None`` on any denial.
    delay_minutes: Optional[int]
    trading_execution_allowed: bool
    status: str                              # see ALL_STATUSES
    audit_notes: str
    #: Normalised classification the decision was recorded against.
    subscriber_type: str = ""
    #: Normalised tier the decision was made against; "" when unrecognised.
    entitlement_tier: str = ""
    #: Prominent Delay Message the caller MUST render with this stream. Set only
    #: for an authorised delayed stream.
    required_display_label: Optional[str] = None
    #: Cadence, in seconds, at which that message must reappear on a scrolling
    #: display. Set only for an authorised delayed stream.
    delay_message_refresh_seconds: Optional[int] = None
    #: ``policy_source`` of the venue policy the decision used, if any.
    policy_source: str = ""


class RealTimeVsDelayedEntitlementEngine:
    """Fail-closed gate for real-time versus delayed market data delivery.

    Decision order::

        tier recognised -> exchange subscribed -> execution-on-delayed block
        -> real-time serve | delayed serve (venue delay policy checks)

    The order is part of the contract: it determines which ``status`` an auditor
    sees for a request that breaches more than one rule. The tier is checked
    first because an unrecognised tier makes every later question unanswerable --
    the engine cannot tell what the subscriber is entitled to at all.

    Args:
        venue_delay_policies: one :class:`VenueDelayPolicy` per venue whose
            delayed feed may be served. A venue with no policy cannot be served
            delayed: the engine has no defensible delay interval or display label
            for it and will not invent 15 minutes. Real-time serving needs no
            policy. Duplicate venue ids are a configuration error, not
            last-one-wins.

    Raises:
        EntitlementConfigurationError: if the policy table is malformed.
    """

    def __init__(
        self, venue_delay_policies: Optional[Iterable[VenueDelayPolicy]] = None
    ) -> None:
        table: Dict[str, VenueDelayPolicy] = {}
        for policy in venue_delay_policies or ():
            if not isinstance(policy, VenueDelayPolicy):
                raise EntitlementConfigurationError(
                    f"venue_delay_policies entries must be VenueDelayPolicy, got {policy!r}")
            if policy.venue_id in table:
                raise EntitlementConfigurationError(
                    f"duplicate VenueDelayPolicy for venue {policy.venue_id!r}; "
                    "a venue must have exactly one delay policy")
            table[policy.venue_id] = policy
        # Read-only after construction: the engine keeps no mutable decision
        # state, so concurrent callers cannot race each other.
        self._venue_delay_policies: Mapping[str, VenueDelayPolicy] = MappingProxyType(table)

    @property
    def venue_delay_policies(self) -> Mapping[str, VenueDelayPolicy]:
        """The configured per-venue delay policies, keyed by normalised venue id."""
        return self._venue_delay_policies

    # -- public API -----------------------------------------------------------

    def evaluate_request(
        self, user: UserEntitlement, request: MarketDataRequest
    ) -> EntitlementAuditReport:
        """Decide what stream this subscriber may receive, and say why.

        Args:
            user: the subscriber's tier, classification and subscribed venues.
            request: the instrument and venue being requested, and whether the
                data will drive live order entry.

        Returns:
            An :class:`EntitlementAuditReport`. ``is_permitted`` is True only for
            ``REALTIME_STREAM_ENTITLED`` and ``DELAYED_STREAM_ENTITLED``, and
            ``trading_execution_allowed`` is True only for the former.

        Raises:
            EntitlementConfigurationError: if the entitlement record or request is
                structurally invalid. Malformed input is a defect, not a denial,
                and must not be mistaken for a compliance decision.
        """
        user_id = _require_non_empty(user.user_id, "user.user_id")
        symbol = _require_non_empty(request.symbol, "request.symbol")
        exchange = _normalise_token(request.exchange, "request.exchange")
        subscriber_type = _require_member(
            user.subscriber_type, "user.subscriber_type", RECOGNISED_CLASSIFICATIONS)
        _require_bool(
            request.is_trading_execution_request, "request.is_trading_execution_request")
        subscribed = self._normalise_exchanges(user.subscribed_exchanges)

        # The tier is normalised but NOT validated against a whitelist here: an
        # unrecognised tier is a compliance denial, not a configuration defect,
        # because it commonly arrives from an upstream entitlement system rather
        # than from local code.
        tier = _normalise_token(user.entitlement_tier, "user.entitlement_tier")

        def deny(status: str, notes: str, *, level: int = logging.WARNING,
                 recorded_tier: str = tier, policy_source: str = "") -> EntitlementAuditReport:
            logger.log(level, notes)
            return EntitlementAuditReport(
                user_id=user_id, symbol=symbol, exchange=exchange,
                is_permitted=False, is_delayed=False, delay_minutes=None,
                trading_execution_allowed=False, status=status, audit_notes=notes,
                subscriber_type=subscriber_type, entitlement_tier=recorded_tier,
                policy_source=policy_source,
            )

        # 1. Tier recognised. Before this check existed, any tier that merely
        #    failed to equal 'DELAYED' -- 'REALTIME', 'Real Time', '' -- skipped
        #    the execution block below and was served as a permitted stream.
        if tier not in RECOGNISED_TIERS:
            return deny(
                STATUS_UNRECOGNISED_TIER,
                f"ENTITLEMENT DENIED [{user_id}]: entitlement tier {tier!r} is not one of "
                f"{sorted(RECOGNISED_TIERS)}. An unclassified tier cannot be shown to be "
                f"licensed and must never fall through to a serving path.",
                level=logging.ERROR,
                recorded_tier="",
            )

        # 2. Venue subscribed at all.
        if exchange not in subscribed:
            return deny(
                STATUS_EXCHANGE_NOT_SUBSCRIBED,
                f"ACCESS DENIED [{user_id}]: user is not subscribed to exchange "
                f"'{request.exchange}'. Subscribed venues: {sorted(subscribed) or 'none'}.",
            )

        # 3. Execution on a delayed feed. An execution request is refused before
        #    any stream is shaped: the caller must not receive a permitted-looking
        #    report it can act on.
        if request.is_trading_execution_request and tier == TIER_DELAYED:
            return deny(
                STATUS_LIVE_TRADING_BLOCKED,
                f"TRADING BLOCKED [{user_id} ({symbol})]: live order execution requested on a "
                f"DELAYED data entitlement for {exchange}. Quotes driving the decision are "
                f"stale by the venue's delay interval; a real-time entitlement is required to "
                f"place live orders from this feed.",
                level=logging.ERROR,
            )

        # 4. Real-time serve.
        if tier == TIER_REAL_TIME:
            notes = (
                f"ENTITLEMENT APPROVED [{user_id} - {STATUS_REALTIME_ENTITLED}]: "
                f"symbol = {symbol} ({exchange}), subscriber = {subscriber_type}, "
                f"delay = 0 mins, trading allowed = True."
            )
            logger.info(notes)
            return EntitlementAuditReport(
                user_id=user_id, symbol=symbol, exchange=exchange,
                is_permitted=True, is_delayed=False, delay_minutes=0,
                trading_execution_allowed=True, status=STATUS_REALTIME_ENTITLED,
                audit_notes=notes, subscriber_type=subscriber_type, entitlement_tier=tier,
            )

        # 5. Delayed serve -- only against an explicit venue policy.
        policy = self._venue_delay_policies.get(exchange)
        if policy is None:
            return deny(
                STATUS_NO_DELAY_POLICY,
                f"DELAYED STREAM BLOCKED [{user_id} ({symbol})]: no VenueDelayPolicy configured "
                f"for '{exchange}'. The delay interval that makes a feed 'delayed' is venue "
                f"specific (CME Group and ICE Futures Europe draw the line above ten minutes, "
                f"Nasdaq and the ESMA terminology at fifteen), so the engine will not assume "
                f"one. Configure the policy from that venue's published data policy.",
                level=logging.ERROR,
            )

        if policy.delay_minutes < policy.min_delay_minutes:
            return deny(
                STATUS_INSUFFICIENT_DELAY,
                f"DELAYED STREAM BLOCKED [{user_id} ({symbol})]: configured delay of "
                f"{policy.delay_minutes} min does not reach {exchange}'s delayed-data threshold "
                f"of {policy.min_delay_minutes} min. Data throttled by less than the venue's "
                f"boundary is still real-time Information and is fee-liable at real-time rates.",
                level=logging.ERROR,
                policy_source=policy.policy_source,
            )

        if policy.max_delay_minutes is not None and policy.delay_minutes > policy.max_delay_minutes:
            return deny(
                STATUS_DELAY_EXCEEDS_POLICY,
                f"DELAYED STREAM BLOCKED [{user_id} ({symbol})]: configured delay of "
                f"{policy.delay_minutes} min exceeds {exchange}'s delayed-data ceiling of "
                f"{policy.max_delay_minutes} min. Beyond that ceiling the feed is end-of-day or "
                f"historical Information, which is licensed separately.",
                level=logging.ERROR,
                policy_source=policy.policy_source,
            )

        notes = (
            f"ENTITLEMENT APPROVED [{user_id} - {STATUS_DELAYED_ENTITLED}]: "
            f"symbol = {symbol} ({exchange}), subscriber = {subscriber_type}, "
            f"delay = {policy.delay_minutes} mins, trading allowed = False. "
            f"Display must carry '{policy.display_label}' prominently, refreshed at least every "
            f"{policy.delay_message_refresh_seconds}s on a scrolling display."
        )
        logger.info(notes)
        return EntitlementAuditReport(
            user_id=user_id, symbol=symbol, exchange=exchange,
            is_permitted=True, is_delayed=True, delay_minutes=policy.delay_minutes,
            trading_execution_allowed=False, status=STATUS_DELAYED_ENTITLED,
            audit_notes=notes, subscriber_type=subscriber_type, entitlement_tier=tier,
            required_display_label=policy.display_label,
            delay_message_refresh_seconds=policy.delay_message_refresh_seconds,
            policy_source=policy.policy_source,
        )

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _normalise_exchanges(value: object) -> FrozenSet[str]:
        """Normalise the subscribed-venue list, rejecting a bare string.

        Any iterable of ids is accepted, but ``subscribed_exchanges="NASDAQ"``
        would otherwise iterate as the characters ``N``, ``A``, ``S``... and match
        nothing, denying every request for a reason no one could read off the
        report.
        """
        if isinstance(value, (str, bytes)) or not isinstance(value, _IterableABC):
            raise EntitlementConfigurationError(
                "user.subscribed_exchanges must be an iterable of venue ids "
                f"(not a bare string), got {value!r}")
        return frozenset(
            _normalise_token(item, "user.subscribed_exchanges[]") for item in value
        )
