"""SEC Regulation NMS Rule 611 (Order Protection Rule) trade-through surveillance.

Post-trade compliance evaluation of executions in NMS stocks against the
protected quotations that were displayed *at the time of execution*, and
classification of the nine statutory exceptions in 17 CFR 242.611(b).

Four points of the rule text drive the design, and each of them is a place
where an intuitive implementation gets the wrong answer:

* **A trade-through is defined by price, not by side.** Rule 600(b)(105):
  "*Trade-through means the purchase or sale of an NMS stock during regular
  trading hours, either as principal or agent, at a price that is lower than a
  protected bid or higher than a protected offer.*" A *purchase* printed below
  the protected bid is a trade-through of that bid. That is not a corner case:
  the whole point of the stopped-order exception in Rule 611(b)(9) is to permit
  exactly that print for a customer buy order, which it would not need to do if
  buys were only ever tested against the offer. Testing buys against the offer
  alone and sells against the bid alone silently passes half the universe of
  trade-throughs.

* **Rule 611 applies only during regular trading hours.** The definition above
  is limited to RTH, which Rule 600(b)(88) fixes at 9:30 a.m. to 4:00 p.m.
  Eastern. The staff FAQ is explicit that policies and procedures "*are not
  required to address trades that occur outside of regular trading hours, and
  the exceptions in Rule 611(b), including the ISO exception, are not needed
  outside of regular trading hours*" (FAQ 7.01). A surveillance engine with no
  session gate manufactures violations out of pre- and post-market prints.

* **The flickering-quote exception is a per-venue historical test.**
  Rule 611(b)(8) excepts a trade-through where "*the trading center displaying
  the protected quotation that was traded through had displayed, within one
  second prior to execution ... a best bid or best offer, as applicable, for the
  NMS stock with a price that was equal or inferior to the price of the
  trade-through transaction.*" It is not "some quote somewhere changed
  recently". In a liquid NMS stock every venue updates many times per second,
  so the loose reading excepts essentially every trade-through and the engine
  reports nothing, ever.

* **The comparison must be against the quotes as of the execution.** Per
  FAQ 3.02, "*trade prices should be compared with protected quotations at the
  time of execution*", and per FAQ 6.01 a firm's compliance is assessed on the
  quotation data it held at that time. Feeding an unordered quote list into a
  max/min therefore leaks quotes that did not yet exist when the trade printed.

The engine takes per-venue top-of-book records, selects each venue's latest
quote at or before the execution timestamp, drops manual quotes (Rule 611
protects automated quotations only) and venues under an active Self-Help
declaration, and classifies the result.

**Scope and limits.** This is a post-trade surveillance and audit tool, not a
pre-trade router control and not a substitute for the written policies and
procedures Rule 611(a)(1) actually requires. It evaluates NMS *stocks*; listed
options are outside Rule 611. Three exceptions are marked but cannot be proven
from price data alone -- Rule 611(b)(7) (benchmark) turns on what was
determinable when the commitment was made, and Rule 611(b)(2) (not regular way)
and (b)(3) (single-priced auction) are properties of the transaction. For those
the engine records the claim and says so in the audit record rather than
pretending to have validated it.

**Rule status as of 2 September 2026.** Rule 611 is in effect. On 11 June 2026
the Commission proposed to rescind it in its entirety, together with
Rule 610(e) and the defined terms in Rule 600(b)(6), (7), (47), (54), (81),
(82) and (105) (Release No. 34-105655, File No. S7-2026-20, 91 FR 36656,
17 June 2026; comments closed 17 August 2026). No final rule had been adopted
as of the date above, so nothing here is contingent on the proposal.

Sources:
  17 CFR 242.611 -- Order protection rule (paragraphs (a)-(d), exceptions
  (b)(1)-(b)(9))
    https://www.law.cornell.edu/cfr/text/17/242.611
  17 CFR 242.600(b) -- definitions: automated quotation (b)(6), automated
  trading center (b)(7), intermarket sweep order (b)(47), manual quotation
  (b)(54), protected bid or protected offer (b)(81), protected quotation
  (b)(82), regular trading hours (b)(88), trade-through (b)(105), trading
  center (b)(106)
    https://www.law.cornell.edu/cfr/text/17/242.600
  SEC Division of Trading and Markets, "Responses to Frequently Asked Questions
  Concerning Rule 611 and Rule 610 of Regulation NMS" (FAQ 3.02 time of
  execution; 3.08/3.16 benchmark documentation; 3.10 stopped-order underwater
  test; 4.07 self-help notice and objective parameters; 4.09 combined ISO and
  self-help; 6.01-6.04 firm-specific data; 7.01 regular trading hours)
    https://www.sec.gov/divisions/marketreg/nmsfaq610-11.htm
  SEC Release No. 34-105655, "The Trade-Through Rule and Locked and Crossed
  Markets Provisions of Regulation NMS" (proposed rescission)
    https://www.sec.gov/files/rules/proposed/2026/34-105655.pdf
  CAT NMS Plan clock synchronisation and record retention (FAQ R1, B2, A23)
    https://www.catnmsplan.com/faq/r1
"""
import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Rule 600(b)(88): regular trading hours are 9:30 a.m. to 4:00 p.m. Eastern,
# unless changed under the Rule 605(a)(3) procedures -- which, per FAQ 7.02,
# have never been used to alter the window. Rule 600(b)(105) limits the
# definition of "trade-through" to this session, so Rule 611 does not reach a
# print outside it.
REGULAR_TRADING_HOURS_START = datetime.time(9, 30)
REGULAR_TRADING_HOURS_END = datetime.time(16, 0)
US_EQUITY_MARKET_TIMEZONE = "America/New_York"

# Rule 611(b)(8) fixes the flickering-quote look-back at one second prior to
# execution. It is not a tolerance to be tuned.
FLICKERING_QUOTE_WINDOW = datetime.timedelta(seconds=1)

# Price comparison tolerance. Prices are carried as floats to keep the public
# dataclasses unchanged; the finest increment Rule 612 permits is $0.0001, so a
# $1e-6 tolerance is two orders of magnitude below any legal price difference
# while still absorbing binary-float representation error (~1e-14 at $100).
PRICE_EPSILON = 1e-6

# Sentinel used by ProtectedQuote for "this venue is not quoting this side".
_NO_QUOTE = 0.0


class Rule611Status(Enum):
    """Outcome of a Rule 611 evaluation, one value per statutory disposition."""

    COMPLIANT_NO_TRADE_THROUGH = "COMPLIANT_NO_TRADE_THROUGH"
    TRADE_THROUGH_VIOLATION = "TRADE_THROUGH_VIOLATION"
    # Rule 600(b)(105) confines "trade-through" to regular trading hours, so an
    # execution outside 09:30-16:00 ET cannot be one. See FAQ 7.01.
    NOT_SUBJECT_RULE_611 = "NOT_SUBJECT_RULE_611"
    EXEMPT_SELF_HELP = "EXEMPT_SELF_HELP"                        # 611(b)(1)
    EXEMPT_NOT_REGULAR_WAY = "EXEMPT_NOT_REGULAR_WAY"            # 611(b)(2)
    EXEMPT_SINGLE_PRICED_AUCTION = "EXEMPT_SINGLE_PRICED_AUCTION"  # 611(b)(3)
    EXEMPT_CROSSED_MARKET = "EXEMPT_CROSSED_MARKET"              # 611(b)(4)
    EXEMPT_ISO = "EXEMPT_ISO"                                    # 611(b)(5)/(6)
    # ISO marking present, but the simultaneous routes supplied for review do
    # not cover every superior-priced protected quotation as Rule 600(b)(47)(ii)
    # requires. Rule 611(c) puts that burden on the router.
    ISO_SWEEP_NOT_SUBSTANTIATED = "ISO_SWEEP_NOT_SUBSTANTIATED"
    EXEMPT_BENCHMARK = "EXEMPT_BENCHMARK"                        # 611(b)(7)
    EXEMPT_FLICKERING_QUOTE = "EXEMPT_FLICKERING_QUOTE"          # 611(b)(8)
    EXEMPT_STOPPED_ORDER = "EXEMPT_STOPPED_ORDER"                # 611(b)(9)


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeThroughKind(Enum):
    """Which protected quotation a print traded through.

    Rule 600(b)(105) makes this a property of the price, not of the side of the
    order: a purchase below the protected bid trades through the bid just as a
    sale below it does.
    """

    THROUGH_PROTECTED_OFFER = "THROUGH_PROTECTED_OFFER"  # price > protected offer
    THROUGH_PROTECTED_BID = "THROUGH_PROTECTED_BID"      # price < protected bid


class RegNMSError(Exception):
    """Raised when Rule 611 evaluation cannot proceed on the data supplied."""


@dataclass
class ProtectedQuote:
    """One trading centre's top-of-book quotation in an NMS stock.

    Rule 600(b)(81) protects only a quotation that is displayed by an automated
    trading centre, disseminated under an effective NMS plan, and is the *best*
    bid or offer of a national securities exchange or national securities
    association. Depth behind the BBO is never protected, so one record per
    venue per instant is the right granularity.

    ``nbb_price``/``nbo_price`` of ``0.0`` mean the venue is not quoting that
    side. ``timestamp`` is when the quote became effective at the evaluating
    firm -- per FAQ 6.01 compliance is assessed on the firm's own quotation
    data, not on Network (SIP) timestamps. A naive datetime is read as UTC.
    """

    venue_id: str
    symbol: str
    nbb_price: float          # this venue's best bid
    nbb_size: int
    nbo_price: float          # this venue's best offer
    nbo_size: int
    is_automated: bool = True  # Rule 611 protects automated quotations only
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class ExecutionRecord:
    """A single execution in an NMS stock, submitted for Rule 611 review.

    The exception flags record what the trading centre *claims*. Some of those
    claims the engine can test against the quote record (ISO sweep coverage,
    the stopped-order underwater condition); the rest it can only record, and
    the audit result says which is which.
    """

    execution_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: int
    venue_id: str
    execution_timestamp: datetime.datetime

    # Rule 611(b)(5)/(6) -- intermarket sweep order.
    is_iso_tagged: bool = False
    # The ISO's limit price. Rule 600(b)(47) defines an ISO as a *limit* order,
    # and the sweep obligation is measured against its limit price, not against
    # the price it happened to execute at.
    iso_limit_price: Optional[float] = None
    # Venues to which simultaneous ISOs were routed. Supply it to have the
    # sweep checked against Rule 600(b)(47)(ii); omit it and the exception is
    # recorded as claimed-but-unverified.
    iso_routed_venue_ids: Optional[Sequence[str]] = None

    # Rule 611(b)(7) -- benchmark. ``benchmark_reference`` is the agreed
    # benchmark ("intraday VWAP 10:00-15:00", "ADR equivalent price"); FAQ 3.08
    # and 3.16 require the firm to document the externally observable
    # circumstances behind it.
    is_benchmark_vwap: bool = False
    benchmark_reference: Optional[str] = None

    # Rule 611(b)(2)/(b)(3) -- transaction-type exceptions the engine records
    # but cannot derive from price data.
    is_regular_way: bool = True
    is_single_priced_auction: bool = False

    # Rule 611(b)(9) -- stopped order. All three conditions must hold: customer
    # account, price agreed order-by-order, and an "underwater" print.
    is_stopped_order: bool = False
    stopped_order_customer_agreed: bool = False


@dataclass
class Rule611AuditResult:
    """Evaluation outcome, shaped for retention alongside CAT order records."""

    execution_id: str
    is_compliant: bool
    status: Rule611Status
    trade_through_bps: float
    executed_price: float
    protected_nbb: float
    protected_nbo: float
    violating_venue_id: Optional[str]
    exemption_reason: str
    audit_timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    symbol: str = ""
    side: Optional[OrderSide] = None
    # Which protected quotation was traded through, and at what price.
    trade_through_kind: Optional[TradeThroughKind] = None
    traded_through_quote_price: Optional[float] = None
    # The instant the protected NBBO was taken as of -- the execution time, per
    # FAQ 3.02. Retaining it is what makes the evaluation reproducible.
    quote_as_of: Optional[datetime.datetime] = None
    is_regular_trading_hours: bool = True
    # Venues excluded from the protected NBBO under Rule 611(b)(1) at that
    # instant, and the venues whose quotes did enter it.
    self_help_venues: Tuple[str, ...] = ()
    contributing_venues: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _SelfHelpWindow:
    """One Self-Help declaration interval; ``revoked_at`` None means open."""

    declared_at: datetime.datetime
    reason: str
    notice_sent: bool
    revoked_at: Optional[datetime.datetime] = None

    def covers(self, moment: datetime.datetime) -> bool:
        if moment < self.declared_at:
            return False
        return self.revoked_at is None or moment < self.revoked_at


def _ensure_aware(value: datetime.datetime, field_name: str) -> datetime.datetime:
    """Return ``value`` as a timezone-aware UTC-comparable datetime.

    Mixing naive and aware datetimes raises ``TypeError`` on subtraction, which
    in an audit pipeline surfaces as a crash on the one record whose feed
    stamped a timezone. Naive input is read as UTC, which is what SIP and
    exchange feeds carry.
    """
    if not isinstance(value, datetime.datetime):
        raise RegNMSError(
            f"{field_name} must be a datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value


def _validate_price(value: float, field_name: str, *, allow_zero: bool = False) -> float:
    """Reject NaN, infinity and non-positive prices.

    A NaN price makes every ``<`` and ``>`` comparison return False, so an
    upstream data-quality failure would be reported as a clean compliant
    execution. That is the worst possible failure mode for surveillance.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegNMSError(
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    value = float(value)
    if not math.isfinite(value):
        raise RegNMSError(f"{field_name} must be finite, got {value!r}")
    if value < 0.0 or (value == 0.0 and not allow_zero):
        raise RegNMSError(f"{field_name} must be positive, got {value!r}")
    return value


def _resolve_market_timezone():
    """Resolve the US equity market timezone, with an actionable error."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(US_EQUITY_MARKET_TIMEZONE)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - platform dependent
        raise RegNMSError(
            f"IANA timezone {US_EQUITY_MARKET_TIMEZONE!r} is unavailable. On "
            f"platforms without a system tz database, install the 'tzdata' "
            f"package -- the Rule 611 regular-trading-hours test cannot be "
            f"evaluated without it."
        ) from exc


def is_regular_trading_hours(moment: datetime.datetime) -> bool:
    """True if ``moment`` falls inside regular trading hours (Rule 600(b)(88)).

    9:30 a.m. to 4:00 p.m. Eastern, weekdays. Both bounds are treated as
    inclusive: the opening and closing auction prints are inside the session
    and are the transactions Rule 611(b)(3) exists to except.

    Exchange holidays are NOT modelled -- a holiday has no protected quotations
    to trade through, so the question does not arise in practice, but do not
    read a True from this function as "the market was open".
    """
    moment = _ensure_aware(moment, "moment")
    eastern = moment.astimezone(_resolve_market_timezone())
    if eastern.weekday() >= 5:  # Saturday, Sunday
        return False
    return REGULAR_TRADING_HOURS_START <= eastern.time() <= REGULAR_TRADING_HOURS_END


class RegNMSOrderProtectionEngine:
    """SEC Regulation NMS Rule 611 (Order Protection Rule) surveillance engine.

    Evaluates executions in NMS stocks against the protected quotations
    displayed at the time of execution, classifies the Rule 611(b) exceptions,
    and produces an audit record retainable alongside the firm's CAT order
    records.

    Self-Help declarations are held as time intervals, so a historical
    execution is evaluated against the declarations that were open *when it
    printed* rather than against whatever is open now. Replaying yesterday's
    tape must not depend on today's operational state.
    """

    def __init__(self) -> None:
        self.self_help_declarations: Dict[str, List[_SelfHelpWindow]] = {}
        logger.info(
            "Initialised SEC Regulation NMS Rule 611 order protection engine "
            "(RTH %s-%s ET, flickering-quote window %ss)",
            REGULAR_TRADING_HOURS_START,
            REGULAR_TRADING_HOURS_END,
            FLICKERING_QUOTE_WINDOW.total_seconds(),
        )

    # ------------------------------------------------------------------
    # Rule 611(b)(1) -- Self-Help
    # ------------------------------------------------------------------
    def declare_self_help(
        self,
        venue_id: str,
        reason: str = "Repeated failure to respond to IOC within one second",
        declared_at: Optional[datetime.datetime] = None,
        notice_sent: bool = False,
    ) -> None:
        """Open a Self-Help declaration against a trading centre.

        Rule 611(b)(1) excepts a trade-through effected while the trading
        centre displaying the protected quotation "*was experiencing a failure,
        material delay, or malfunction of its systems or equipment*". The staff
        FAQ 4.07 sets out three mandatory elements of the policies and
        procedures behind it: **notice**, **systems assessment and response**,
        and **objective parameters** -- naming the repeated failure of the
        destination to turn an IOC order around within one second (after
        adjusting for transmission time) as a parameter that would justify it.

        ``notice_sent`` records the first element. Notice to the bypassed
        trading centre is mandatory and must be sent immediately upon use of
        the exception; a declaration recorded without it is logged as a
        deficiency and carried into the audit record, because that is the fact
        an examiner will ask about.
        """
        venue_id = self._normalise_venue(venue_id)
        declared_at = _ensure_aware(
            declared_at or datetime.datetime.now(datetime.timezone.utc), "declared_at"
        )
        windows = self.self_help_declarations.setdefault(venue_id, [])
        overlapping = next(
            (w for w in windows if w.revoked_at is None or w.covers(declared_at)), None
        )
        if overlapping is not None:
            logger.warning(
                "Self-Help already declared against venue '%s' at %s and still "
                "covering %s; ignoring duplicate declaration",
                venue_id,
                overlapping.declared_at.isoformat(),
                declared_at.isoformat(),
            )
            return
        windows.append(
            _SelfHelpWindow(
                declared_at=declared_at, reason=reason, notice_sent=bool(notice_sent)
            )
        )
        logger.warning(
            "SEC Reg NMS Self-Help DECLARED against venue '%s' at %s: %s",
            venue_id,
            declared_at.isoformat(),
            reason,
        )
        if not notice_sent:
            logger.error(
                "Self-Help notice to bypassed venue '%s' is NOT recorded. "
                "Rule 611(b)(1) policies and procedures require notice "
                "immediately upon use of the exception (Reg NMS FAQ 4.07)",
                venue_id,
            )

    def revoke_self_help(
        self, venue_id: str, revoked_at: Optional[datetime.datetime] = None
    ) -> None:
        """Close the open Self-Help declaration for a venue, if any.

        The interval is retained rather than deleted: an execution that printed
        while the declaration was open must still evaluate against it when the
        tape is replayed.
        """
        venue_id = self._normalise_venue(venue_id)
        windows = self.self_help_declarations.get(venue_id)
        if not windows or windows[-1].revoked_at is not None:
            logger.info("No open Self-Help declaration to revoke for '%s'", venue_id)
            return
        revoked_at = _ensure_aware(
            revoked_at or datetime.datetime.now(datetime.timezone.utc), "revoked_at"
        )
        open_window = windows[-1]
        if revoked_at < open_window.declared_at:
            raise RegNMSError(
                f"Self-Help revocation for '{venue_id}' at {revoked_at.isoformat()} "
                f"precedes its declaration at {open_window.declared_at.isoformat()}"
            )
        windows[-1] = _SelfHelpWindow(
            declared_at=open_window.declared_at,
            reason=open_window.reason,
            notice_sent=open_window.notice_sent,
            revoked_at=revoked_at,
        )
        logger.info(
            "SEC Reg NMS Self-Help REVOKED for venue '%s' at %s",
            venue_id,
            revoked_at.isoformat(),
        )

    def is_self_help_active(
        self, venue_id: str, at: Optional[datetime.datetime] = None
    ) -> bool:
        """Whether Self-Help was open against ``venue_id`` at ``at`` (default now)."""
        venue_id = self._normalise_venue(venue_id)
        moment = _ensure_aware(
            at or datetime.datetime.now(datetime.timezone.utc), "at"
        )
        return any(w.covers(moment) for w in self.self_help_declarations.get(venue_id, ()))

    def self_help_venues_at(self, at: datetime.datetime) -> Tuple[str, ...]:
        """Venues under an open Self-Help declaration at ``at``, sorted."""
        moment = _ensure_aware(at, "at")
        return tuple(
            sorted(
                venue
                for venue, windows in self.self_help_declarations.items()
                if any(w.covers(moment) for w in windows)
            )
        )

    def self_help_notice_recorded(
        self, venue_id: str, at: datetime.datetime
    ) -> Optional[bool]:
        """Whether notice was recorded for the declaration open at ``at``."""
        venue_id = self._normalise_venue(venue_id)
        moment = _ensure_aware(at, "at")
        for window in self.self_help_declarations.get(venue_id, ()):
            if window.covers(moment):
                return window.notice_sent
        return None

    # ------------------------------------------------------------------
    # Protected NBBO
    # ------------------------------------------------------------------
    def compute_protected_nbbo(
        self,
        quotes: Sequence[ProtectedQuote],
        as_of: Optional[datetime.datetime] = None,
        symbol: Optional[str] = None,
        apply_self_help: bool = True,
    ) -> Tuple[float, float, Optional[str], Optional[str]]:
        """Protected NBB and NBO from automated, non-Self-Help venue quotes.

        Returns ``(nbb, nbo, nbb_venue, nbo_venue)``.

        ``as_of`` is the instant the book is taken at. Quotes stamped after it
        are discarded and, for each venue, only its most recent quote at or
        before it is used -- without that, an unordered quote list lets a
        quotation that did not yet exist decide whether a trade printed through
        the market. Leave it ``None`` only for a live snapshot whose records are
        all current.

        ``symbol``, when given, restricts the book to that instrument. Quotes in
        another NMS stock produce a numerically valid but meaningless NBBO, and
        that is a mistake a mixed feed makes silently.

        Raises ``RegNMSError`` when no protected bid or no protected offer
        survives the filters -- an empty side is not a zero price.
        """
        moment = _ensure_aware(as_of, "as_of") if as_of is not None else None
        wanted_symbol = self._normalise_symbol(symbol) if symbol is not None else None

        # Latest quote per venue at or before `as_of`.
        latest: Dict[str, ProtectedQuote] = {}
        for quote in quotes:
            if not isinstance(quote, ProtectedQuote):
                raise RegNMSError(
                    f"quotes must contain ProtectedQuote records, got "
                    f"{type(quote).__name__}"
                )
            if wanted_symbol is not None and (
                self._normalise_symbol(quote.symbol) != wanted_symbol
            ):
                continue
            # Rule 611 protects automated quotations only; a manual quotation
            # (Rule 600(b)(54)) may be traded through freely.
            if not quote.is_automated:
                continue
            venue = self._normalise_venue(quote.venue_id)
            quote_time = _ensure_aware(quote.timestamp, "ProtectedQuote.timestamp")
            if moment is not None and quote_time > moment:
                continue
            if apply_self_help and self.is_self_help_active(venue, moment):
                continue
            incumbent = latest.get(venue)
            if incumbent is None or quote_time >= _ensure_aware(
                incumbent.timestamp, "ProtectedQuote.timestamp"
            ):
                latest[venue] = quote

        valid_bids: List[Tuple[float, str]] = []
        valid_offers: List[Tuple[float, str]] = []
        for venue, quote in latest.items():
            bid = _validate_price(quote.nbb_price, "ProtectedQuote.nbb_price", allow_zero=True)
            offer = _validate_price(quote.nbo_price, "ProtectedQuote.nbo_price", allow_zero=True)
            if bid > _NO_QUOTE:
                valid_bids.append((bid, venue))
            if offer > _NO_QUOTE:
                valid_offers.append((offer, venue))

        if not valid_bids or not valid_offers:
            raise RegNMSError(
                "No two-sided protected automated quotation available"
                + (f" for {wanted_symbol}" if wanted_symbol else "")
                + (f" as of {moment.isoformat()}" if moment else "")
                + f" ({len(valid_bids)} protected bid(s), "
                f"{len(valid_offers)} protected offer(s) after filtering)."
            )

        # Ties broken on venue id so a replay of the same tape is deterministic.
        best_nbb, nbb_venue = max(valid_bids, key=lambda item: (item[0], item[1]))
        best_nbo, nbo_venue = min(valid_offers, key=lambda item: (item[0], item[1]))
        return best_nbb, best_nbo, nbb_venue, nbo_venue

    # ------------------------------------------------------------------
    # Rule 611 evaluation
    # ------------------------------------------------------------------
    def evaluate_execution(
        self,
        exec_rec: ExecutionRecord,
        protected_quotes: Sequence[ProtectedQuote],
    ) -> Rule611AuditResult:
        """Evaluate one execution for Rule 611 compliance.

        The order of reasoning follows the rule itself. Rule 611(b) excepts
        "*the transaction that constituted the trade-through*", so whether a
        trade-through occurred is settled first and the exceptions are applied
        only then. An ISO-marked execution that never traded through the market
        is reported as compliant, not as exempt -- the distinction matters when
        an examiner asks how often the firm relies on the ISO exception.
        """
        self._validate_execution(exec_rec)
        exec_time = _ensure_aware(
            exec_rec.execution_timestamp, "ExecutionRecord.execution_timestamp"
        )
        symbol = self._normalise_symbol(exec_rec.symbol)
        price = float(exec_rec.price)

        # Rule 600(b)(105) confines trade-throughs to regular trading hours.
        if not is_regular_trading_hours(exec_time):
            logger.info(
                "Rule 611 audit [%s]: outside regular trading hours (%s) -- "
                "Rule 611 does not apply",
                exec_rec.execution_id,
                exec_time.isoformat(),
            )
            return self._result(
                exec_rec,
                symbol=symbol,
                status=Rule611Status.NOT_SUBJECT_RULE_611,
                is_compliant=True,
                reason=(
                    "Executed outside regular trading hours (09:30-16:00 ET). "
                    "Rule 600(b)(105) limits trade-throughs to regular trading "
                    "hours; Reg NMS FAQ 7.01"
                ),
                quote_as_of=exec_time,
                is_rth=False,
            )

        self_help_venues = self.self_help_venues_at(exec_time)

        # Two books: the full protected market, and the market after removing
        # venues under Self-Help. The difference is what Rule 611(b)(1) buys.
        nbb_all, nbo_all, nbb_venue_all, nbo_venue_all = self.compute_protected_nbbo(
            protected_quotes, as_of=exec_time, symbol=symbol, apply_self_help=False
        )
        try:
            nbb, nbo, nbb_venue, nbo_venue = self.compute_protected_nbbo(
                protected_quotes, as_of=exec_time, symbol=symbol, apply_self_help=True
            )
        except RegNMSError:
            if not self_help_venues:
                raise
            # Every protected quotation on one side belongs to a Self-Help
            # venue: there is no protected market left to trade through.
            logger.info(
                "Rule 611 audit [%s]: no protected market remains after "
                "Self-Help exclusion of %s",
                exec_rec.execution_id,
                ", ".join(self_help_venues),
            )
            return self._result(
                exec_rec,
                symbol=symbol,
                status=Rule611Status.EXEMPT_SELF_HELP,
                is_compliant=True,
                reason=self._self_help_reason(self_help_venues, exec_time),
                nbb=nbb_all,
                nbo=nbo_all,
                quote_as_of=exec_time,
                self_help_venues=self_help_venues,
            )

        contributing = self._contributing_venues(
            protected_quotes, exec_time, symbol, self_help_venues
        )
        through_all = self._classify_trade_through(
            price, nbb_all, nbo_all, nbb_venue_all, nbo_venue_all
        )
        through = self._classify_trade_through(price, nbb, nbo, nbb_venue, nbo_venue)

        base = dict(
            symbol=symbol,
            nbb=nbb,
            nbo=nbo,
            quote_as_of=exec_time,
            self_help_venues=self_help_venues,
            contributing_venues=contributing,
        )

        # Not a trade-through against the full protected market: compliant.
        if through_all is None:
            logger.info(
                "Rule 611 audit [%s]: COMPLIANT (no trade-through)",
                exec_rec.execution_id,
            )
            return self._result(
                exec_rec,
                status=Rule611Status.COMPLIANT_NO_TRADE_THROUGH,
                is_compliant=True,
                reason="Executed at or within the protected NBBO",
                **base,
            )

        # A trade-through only against venues under Self-Help: Rule 611(b)(1).
        if through is None:
            logger.info(
                "Rule 611 audit [%s]: EXEMPT under Rule 611(b)(1) Self-Help",
                exec_rec.execution_id,
            )
            return self._result(
                exec_rec,
                status=Rule611Status.EXEMPT_SELF_HELP,
                is_compliant=True,
                reason=self._self_help_reason(self_help_venues, exec_time),
                **base,
            )

        kind, quote_price, quote_venue = through
        bps = abs(price - quote_price) / quote_price * 10_000.0
        detail = dict(
            base,
            kind=kind,
            traded_through_quote_price=quote_price,
            violating_venue_id=quote_venue,
            bps=bps,
        )

        # Rule 611(b)(4) -- crossed protected market. When a protected bid is
        # priced above a protected offer, every price is through one side or
        # the other; the rule excepts the whole condition rather than asking
        # the trading centre to resolve it.
        if nbb > nbo + PRICE_EPSILON:
            logger.warning(
                "Rule 611 audit [%s]: EXEMPT under Rule 611(b)(4) -- crossed "
                "protected market (NBB $%.4f > NBO $%.4f)",
                exec_rec.execution_id,
                nbb,
                nbo,
            )
            return self._result(
                exec_rec,
                status=Rule611Status.EXEMPT_CROSSED_MARKET,
                is_compliant=True,
                reason=(
                    f"Crossed protected market at execution "
                    f"(NBB ${nbb:.4f} > NBO ${nbo:.4f}) -- Rule 611(b)(4)"
                ),
                **detail,
            )

        # Rule 611(b)(2) -- not a "regular way" contract.
        if not exec_rec.is_regular_way:
            return self._exempt(
                exec_rec,
                Rule611Status.EXEMPT_NOT_REGULAR_WAY,
                "Transaction was not a 'regular way' contract -- Rule 611(b)(2). "
                "Settlement terms are asserted by the reporter and are not "
                "verifiable from quote data",
                **detail,
            )

        # Rule 611(b)(3) -- single-priced opening, reopening or closing
        # transaction by the trading centre.
        if exec_rec.is_single_priced_auction:
            return self._exempt(
                exec_rec,
                Rule611Status.EXEMPT_SINGLE_PRICED_AUCTION,
                "Single-priced opening, reopening or closing transaction -- "
                "Rule 611(b)(3). Auction type is asserted by the reporter",
                **detail,
            )

        # Rule 611(b)(5)/(6) -- intermarket sweep order.
        if exec_rec.is_iso_tagged:
            return self._evaluate_iso(exec_rec, protected_quotes, exec_time, detail)

        # Rule 611(b)(7) -- benchmark. Whether the price was "not based,
        # directly or indirectly, on the quoted price ... and for which the
        # material terms were not reasonably determinable at the time the
        # commitment to execute the order was made" is a facts-and-
        # circumstances question (FAQ 3.16). Price data cannot settle it.
        if exec_rec.is_benchmark_vwap:
            if exec_rec.benchmark_reference:
                reason = (
                    f"Benchmark execution against "
                    f"'{exec_rec.benchmark_reference}' -- Rule 611(b)(7). Firm "
                    f"must retain documentation of the externally observable "
                    f"circumstances (Reg NMS FAQ 3.08, 3.16)"
                )
            else:
                reason = (
                    "Benchmark execution -- Rule 611(b)(7) claimed with NO "
                    "benchmark reference recorded. Unsubstantiated: FAQ 3.08 "
                    "and 3.16 require documented externally observable "
                    "circumstances"
                )
                logger.warning(
                    "Rule 611 audit [%s]: Rule 611(b)(7) claimed without a "
                    "benchmark reference",
                    exec_rec.execution_id,
                )
            return self._exempt(
                exec_rec, Rule611Status.EXEMPT_BENCHMARK, reason, **detail
            )

        # Rule 611(b)(9) -- stopped order. Unlike (b)(2), (b)(3) and (b)(7),
        # the "underwater" condition in (b)(9)(iii) is checkable.
        if exec_rec.is_stopped_order:
            granted, reason = self._evaluate_stopped_order(exec_rec, price, nbb, nbo)
            if granted:
                return self._exempt(
                    exec_rec, Rule611Status.EXEMPT_STOPPED_ORDER, reason, **detail
                )
            logger.warning(
                "Rule 611 audit [%s]: Rule 611(b)(9) claimed but not met -- %s",
                exec_rec.execution_id,
                reason,
            )

        # Rule 611(b)(8) -- flickering quote. Per-venue and strictly backward
        # looking: the trading centre whose protected quotation was traded
        # through must itself have displayed, within the second before the
        # print, a same-side quote at a price equal or inferior to the print.
        flicker_price = self._flickering_quote_price(
            protected_quotes, exec_time, symbol, quote_venue, kind, price
        )
        if flicker_price is not None:
            logger.info(
                "Rule 611 audit [%s]: EXEMPT under Rule 611(b)(8) -- venue '%s' "
                "displayed $%.4f within 1s prior to execution",
                exec_rec.execution_id,
                quote_venue,
                flicker_price,
            )
            return self._exempt(
                exec_rec,
                Rule611Status.EXEMPT_FLICKERING_QUOTE,
                (
                    f"Venue '{quote_venue}' displayed ${flicker_price:.4f} "
                    f"within one second prior to execution, equal or inferior "
                    f"to the trade-through price ${price:.4f} -- Rule 611(b)(8)"
                ),
                **detail,
            )

        logger.error(
            "Rule 611 TRADE-THROUGH VIOLATION [%s]: %s side=%s qty=%d price=$%.4f "
            "traded through venue '%s' at $%.4f (%s), protected NBB=$%.4f "
            "NBO=$%.4f as of %s (%.2f bps)",
            exec_rec.execution_id,
            symbol,
            exec_rec.side.value,
            exec_rec.quantity,
            price,
            quote_venue,
            quote_price,
            kind.value,
            nbb,
            nbo,
            exec_time.isoformat(),
            bps,
        )
        return self._result(
            exec_rec,
            status=Rule611Status.TRADE_THROUGH_VIOLATION,
            is_compliant=False,
            reason=(
                f"No Rule 611(b) exception applies -- executed at ${price:.4f} "
                f"through the protected "
                f"{'offer' if kind is TradeThroughKind.THROUGH_PROTECTED_OFFER else 'bid'}"
                f" of ${quote_price:.4f} displayed by '{quote_venue}'"
            ),
            **detail,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_trade_through(
        price: float,
        nbb: float,
        nbo: float,
        nbb_venue: Optional[str],
        nbo_venue: Optional[str],
    ) -> Optional[Tuple[TradeThroughKind, float, Optional[str]]]:
        """Which protected quotation, if any, this price traded through.

        Rule 600(b)(105): a trade-through is a purchase *or* sale at a price
        lower than a protected bid or higher than a protected offer. The side
        of the order is not part of the test -- which is precisely why
        Rule 611(b)(9) needs to except a stopped *buy* order printed below the
        national best bid.

        Returns ``(kind, protected_quote_price, displaying_venue)`` or ``None``.
        """
        if price > nbo + PRICE_EPSILON:
            return (TradeThroughKind.THROUGH_PROTECTED_OFFER, nbo, nbo_venue)
        if price < nbb - PRICE_EPSILON:
            return (TradeThroughKind.THROUGH_PROTECTED_BID, nbb, nbb_venue)
        return None

    @staticmethod
    def _evaluate_stopped_order(
        exec_rec: ExecutionRecord, price: float, nbb: float, nbo: float
    ) -> Tuple[bool, str]:
        """Test the three conditions of Rule 611(b)(9).

        (i) customer account and (ii) order-by-order agreement are assertions;
        (iii) is the "underwater" price test, which FAQ 3.10 states as the
        broker-dealer selling to the customer below the national best bid or
        buying from the customer above the national best offer. In the rule's
        own words: for a stopped *buy* order the price must be lower than the
        national best bid, for a stopped *sell* order higher than the national
        best offer.
        """
        if not exec_rec.stopped_order_customer_agreed:
            return (
                False,
                "Rule 611(b)(9) requires a customer account and agreement to "
                "the specified price on an order-by-order basis; not recorded",
            )
        if exec_rec.side is OrderSide.BUY:
            if price < nbb - PRICE_EPSILON:
                return (
                    True,
                    f"Stopped buy order executed at ${price:.4f}, below the "
                    f"protected bid ${nbb:.4f} -- Rule 611(b)(9)(iii) "
                    f"underwater condition met",
                )
            return (
                False,
                f"Stopped buy order at ${price:.4f} is not lower than the "
                f"national best bid ${nbb:.4f}; Rule 611(b)(9)(iii) not met",
            )
        if price > nbo + PRICE_EPSILON:
            return (
                True,
                f"Stopped sell order executed at ${price:.4f}, above the "
                f"protected offer ${nbo:.4f} -- Rule 611(b)(9)(iii) underwater "
                f"condition met",
            )
        return (
            False,
            f"Stopped sell order at ${price:.4f} is not higher than the "
            f"national best offer ${nbo:.4f}; Rule 611(b)(9)(iii) not met",
        )

    def _evaluate_iso(
        self,
        exec_rec: ExecutionRecord,
        quotes: Sequence[ProtectedQuote],
        exec_time: datetime.datetime,
        detail: dict,
    ) -> Rule611AuditResult:
        """Rule 611(b)(5)/(6), tested against Rule 600(b)(47)(ii) where possible.

        The receiving trading centre may rely on the ISO marking. The *router*
        may not: Rule 611(c) requires it to take reasonable steps to establish
        that the order met Rule 600(b)(47), which obliges it to have routed,
        simultaneously, additional ISOs against the full displayed size of every
        protected quotation priced superior to the ISO's **limit price** -- the
        protected offers for a buy, the protected bids for a sell.

        Venues under an open Self-Help declaration may be left out of the sweep
        (Reg NMS FAQ 4.09), so they are excluded here too.

        Supplying neither the limit price nor the routed venues leaves the
        exception recorded but unverified; the audit record says so rather than
        implying the sweep was checked.
        """
        if exec_rec.iso_limit_price is None or exec_rec.iso_routed_venue_ids is None:
            return self._exempt(
                exec_rec,
                Rule611Status.EXEMPT_ISO,
                (
                    "Order identified as an intermarket sweep order -- "
                    "Rule 611(b)(5). Sweep coverage NOT verified: supply "
                    "iso_limit_price and iso_routed_venue_ids to test the "
                    "Rule 600(b)(47)(ii) routing obligation that Rule 611(c) "
                    "places on the router"
                ),
                **detail,
            )

        limit_price = _validate_price(
            exec_rec.iso_limit_price, "ExecutionRecord.iso_limit_price"
        )
        routed = {self._normalise_venue(v) for v in exec_rec.iso_routed_venue_ids}
        self_help = set(self.self_help_venues_at(exec_time))
        symbol = self._normalise_symbol(exec_rec.symbol)

        required: Dict[str, float] = {}
        for venue, quote in self._book_as_of(quotes, exec_time, symbol).items():
            if venue in self_help:
                continue
            if exec_rec.side is OrderSide.BUY:
                # Superior to a buy limit means a lower-priced offer.
                if quote.nbo_price > _NO_QUOTE and quote.nbo_price < limit_price - PRICE_EPSILON:
                    required[venue] = quote.nbo_price
            else:
                # Superior to a sell limit means a higher-priced bid.
                if quote.nbb_price > _NO_QUOTE and quote.nbb_price > limit_price + PRICE_EPSILON:
                    required[venue] = quote.nbb_price

        missing = sorted(set(required) - routed)
        if missing:
            logger.error(
                "Rule 611 ISO SWEEP NOT SUBSTANTIATED [%s]: no simultaneous ISO "
                "recorded to %s, which displayed protected quotations superior "
                "to the ISO limit price $%.4f",
                exec_rec.execution_id,
                ", ".join(f"{v} @ ${required[v]:.4f}" for v in missing),
                limit_price,
            )
            return self._result(
                exec_rec,
                status=Rule611Status.ISO_SWEEP_NOT_SUBSTANTIATED,
                is_compliant=False,
                reason=(
                    f"ISO marked, but no simultaneous ISO recorded to "
                    f"{', '.join(missing)} despite protected quotations "
                    f"superior to the ISO limit price ${limit_price:.4f} -- "
                    f"Rule 600(b)(47)(ii) via Rule 611(c)"
                ),
                **detail,
            )

        return self._exempt(
            exec_rec,
            Rule611Status.EXEMPT_ISO,
            (
                f"Intermarket sweep order -- Rule 611(b)(5)/(6). Simultaneous "
                f"routes cover all {len(required)} protected quotation(s) "
                f"superior to the ISO limit price ${limit_price:.4f} "
                f"(Rule 600(b)(47)(ii))"
            ),
            **detail,
        )

    def _flickering_quote_price(
        self,
        quotes: Sequence[ProtectedQuote],
        exec_time: datetime.datetime,
        symbol: str,
        traded_through_venue: str,
        kind: TradeThroughKind,
        price: float,
    ) -> Optional[float]:
        """Best supporting quote for the Rule 611(b)(8) exception, if any.

        The exception is available only where **the venue whose protected
        quotation was traded through** had itself displayed, strictly within the
        one second before the execution, a same-side quote at a price *equal or
        inferior to* the trade-through price. Inferior means worse from the
        taker's standpoint: a higher offer where the offer was traded through, a
        lower bid where the bid was.

        The quote effective *at* the execution is the one that was traded
        through, so the window is strictly prior to it.
        """
        window_start = exec_time - FLICKERING_QUOTE_WINDOW
        best: Optional[float] = None
        for quote in quotes:
            if self._normalise_symbol(quote.symbol) != symbol:
                continue
            if self._normalise_venue(quote.venue_id) != traded_through_venue:
                continue
            if not quote.is_automated:
                continue
            quote_time = _ensure_aware(quote.timestamp, "ProtectedQuote.timestamp")
            if not (window_start <= quote_time < exec_time):
                continue
            if kind is TradeThroughKind.THROUGH_PROTECTED_OFFER:
                candidate = quote.nbo_price
                if candidate > _NO_QUOTE and candidate >= price - PRICE_EPSILON:
                    best = candidate if best is None else min(best, candidate)
            else:
                candidate = quote.nbb_price
                if candidate > _NO_QUOTE and candidate <= price + PRICE_EPSILON:
                    best = candidate if best is None else max(best, candidate)
        return best

    def _book_as_of(
        self,
        quotes: Sequence[ProtectedQuote],
        as_of: datetime.datetime,
        symbol: str,
    ) -> Dict[str, ProtectedQuote]:
        """Latest automated quote per venue at or before ``as_of``."""
        book: Dict[str, ProtectedQuote] = {}
        for quote in quotes:
            if self._normalise_symbol(quote.symbol) != symbol or not quote.is_automated:
                continue
            quote_time = _ensure_aware(quote.timestamp, "ProtectedQuote.timestamp")
            if quote_time > as_of:
                continue
            venue = self._normalise_venue(quote.venue_id)
            incumbent = book.get(venue)
            if incumbent is None or quote_time >= _ensure_aware(
                incumbent.timestamp, "ProtectedQuote.timestamp"
            ):
                book[venue] = quote
        return book

    def _contributing_venues(
        self,
        quotes: Sequence[ProtectedQuote],
        exec_time: datetime.datetime,
        symbol: str,
        self_help_venues: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        excluded = set(self_help_venues)
        return tuple(
            sorted(v for v in self._book_as_of(quotes, exec_time, symbol) if v not in excluded)
        )

    def _self_help_reason(
        self, self_help_venues: Sequence[str], exec_time: datetime.datetime
    ) -> str:
        parts = []
        for venue in self_help_venues:
            notice = self.self_help_notice_recorded(venue, exec_time)
            parts.append(f"{venue} (notice {'recorded' if notice else 'NOT recorded'})")
        return (
            "Protected quotation(s) bypassed under an open Self-Help "
            f"declaration -- Rule 611(b)(1): {', '.join(parts)}. Notice to the "
            "bypassed trading centre is required immediately upon use of the "
            "exception (Reg NMS FAQ 4.07)"
        )

    def _exempt(
        self, exec_rec: ExecutionRecord, status: Rule611Status, reason: str, **kwargs
    ) -> Rule611AuditResult:
        logger.info(
            "Rule 611 audit [%s]: %s", exec_rec.execution_id, status.value
        )
        return self._result(
            exec_rec, status=status, is_compliant=True, reason=reason, **kwargs
        )

    @staticmethod
    def _result(
        exec_rec: ExecutionRecord,
        *,
        symbol: str,
        status: Rule611Status,
        is_compliant: bool,
        reason: str,
        nbb: float = 0.0,
        nbo: float = 0.0,
        bps: float = 0.0,
        kind: Optional[TradeThroughKind] = None,
        traded_through_quote_price: Optional[float] = None,
        violating_venue_id: Optional[str] = None,
        quote_as_of: Optional[datetime.datetime] = None,
        is_rth: bool = True,
        self_help_venues: Tuple[str, ...] = (),
        contributing_venues: Tuple[str, ...] = (),
    ) -> Rule611AuditResult:
        return Rule611AuditResult(
            execution_id=exec_rec.execution_id,
            is_compliant=is_compliant,
            status=status,
            trade_through_bps=bps,
            executed_price=float(exec_rec.price),
            protected_nbb=nbb,
            protected_nbo=nbo,
            violating_venue_id=violating_venue_id,
            exemption_reason=reason,
            symbol=symbol,
            side=exec_rec.side,
            trade_through_kind=kind,
            traded_through_quote_price=traded_through_quote_price,
            quote_as_of=quote_as_of,
            is_regular_trading_hours=is_rth,
            self_help_venues=tuple(self_help_venues),
            contributing_venues=tuple(contributing_venues),
        )

    def _validate_execution(self, exec_rec: ExecutionRecord) -> None:
        if not isinstance(exec_rec, ExecutionRecord):
            raise RegNMSError(
                f"exec_rec must be an ExecutionRecord, got {type(exec_rec).__name__}"
            )
        if not isinstance(exec_rec.side, OrderSide):
            raise RegNMSError(
                f"ExecutionRecord.side must be an OrderSide, got {exec_rec.side!r}"
            )
        if not str(exec_rec.execution_id).strip():
            raise RegNMSError("ExecutionRecord.execution_id must not be empty")
        _validate_price(exec_rec.price, "ExecutionRecord.price")
        if isinstance(exec_rec.quantity, bool) or not isinstance(exec_rec.quantity, int):
            raise RegNMSError(
                f"ExecutionRecord.quantity must be an int, got "
                f"{type(exec_rec.quantity).__name__}"
            )
        if exec_rec.quantity <= 0:
            raise RegNMSError(
                f"ExecutionRecord.quantity must be positive, got {exec_rec.quantity}"
            )
        if exec_rec.is_iso_tagged and exec_rec.is_benchmark_vwap:
            # FAQ 3.22: an order may be evaluated separately under each
            # exception, so this is not invalid -- but it is worth surfacing.
            logger.warning(
                "Execution [%s] claims both the ISO and benchmark exceptions",
                exec_rec.execution_id,
            )

    @staticmethod
    def _normalise_venue(venue_id: str) -> str:
        if not isinstance(venue_id, str) or not venue_id.strip():
            raise RegNMSError(f"venue_id must be a non-empty string, got {venue_id!r}")
        return venue_id.strip().upper()

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise RegNMSError(f"symbol must be a non-empty string, got {symbol!r}")
        return symbol.strip().upper()
