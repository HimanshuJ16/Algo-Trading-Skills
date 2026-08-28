"""
order-to-trade-ratio-fee-penalty-avoidance: order-to-trade ratio (OTR) monitor and
defensive throttling guard for venues that cap, surcharge, or penalise unexecuted
order-message traffic.

Two ratio conventions are supported because venues do not agree on one:

* ``OTRConvention.RTS9_UNEXECUTED`` -- Commission Delegated Regulation (EU) 2017/566
  ("RTS 9"), Article 3(1). The venue calculates, for each member and *each financial
  instrument*, at least at the end of every trading session, both:

      (a) in volume terms:  (total volume of orders / total volume of transactions) - 1
      (b) in number terms:  (total number of orders / total number of transactions) - 1

  Note the ``- 1``: RTS 9 measures *unexecuted* orders against transactions, so a
  member whose every order executes scores 0.0, not 1.0.

* ``OTRConvention.GROSS_MESSAGES_PER_TRADE`` -- the plain "N messages per trade"
  convention used by venue fee schedules that are not RTS 9 derived, including the
  NSE (India) daily algo order-to-trade ratio (NSE/SURV/38122, 22 June 2018).

Under RTS 9 Article 3(2) the limit is deemed exceeded if the member's activity *in one
specific instrument*, across all phases of the session including auctions, exceeds
**either or both** of the two ratios. Both are therefore evaluated here, and either one
alone can force a breach.

Message counting follows the RTS 9 Annex counting methodology, which is not a naive
"one message, one order" count -- see ``RTS9_ANNEX_WEIGHTS``. A limit-order
modification counts as **two** orders ("any modifications entails a cancellation and a
new insertion"); a two-sided quote counts as two, and a quote modification as four.
Counting a modify as one understates the ratio and under-throttles.

RTS 9 Article 1(a) defines "order" as all input messages on submission, modification
and cancellation relating to an order or a quote, **excluding** cancellation messages
sent subsequent to (i) uncrossing in an auction, (ii) a loss of venue connectivity, or
(iii) the use of a kill functionality. Those exempt cancels are a separate input field
here: counting a kill-switch mass-cancel toward the ratio can fabricate a breach out of
a risk control working exactly as intended.

Penalty structures differ per venue and none of the ones surveyed is a single flat
per-message rate applied to every excess message:

* Eurex Excessive System Usage fee: ``(number of transactions - transaction limit) *
  fee``, per participant per product per trading day, aggregated over a calendar month,
  with fewer than four exceedances in a month treated as accidental and not charged.
* NSE (India): tiered charge per algo order on the *daily* member-level OTR, applied on
  an incremental (bracket) basis -- see ``NSE_ALGO_OTR_PENALTY_TIERS_2018``.
* ICE Futures Europe / ICE Endex: a flat per-breach charge (GBP 2,000 / EUR 2,000) when
  the published Red Threshold is equalled or exceeded in any Designated Product on any
  session -- not a per-message charge at all.

``PenaltyTier`` models the progressive-bracket form, which reduces to the flat
``excess * fee`` form with a single unbounded tier. The ICE flat-per-breach form is out
of scope for this engine; derive it from the returned status instead.

Limitations (documented, deliberate):

- **Not a substitute for the venue's own numbers.** The venue is the system of record.
  This engine estimates the ratio from the client's own message counts so a strategy can
  throttle *before* the venue's end-of-session report arrives; it cannot reproduce venue
  bookkeeping exactly (implied orders, venue-side triggers of stop and peg orders,
  market-operations cancellations).
- **No default limit is supplied.** ``max_count_otr`` and ``max_volume_otr`` must be set
  from the venue's published schedule. Published limits differ by orders of magnitude:
  NSE charges from a daily ratio of 50, while ICE Futures Europe publishes Amber/Red
  thresholds of 2,000,000 / 2,500,000 in number terms for its Designated Products. Any
  built-in "typical" default would be wrong nearly everywhere.
- **Per-instrument only.** RTS 9 evaluates one instrument at a time. A venue-aggregated
  ratio can read compliant while breaching in a single instrument, which is precisely
  the case the rule targets. ``aggregate_worst_instrument`` folds a set of per-instrument
  audits; there is deliberately no venue-level session object.
- **Segment exclusions are the caller's job.** Venue regimes exclude classes of orders
  from the count -- SEBI/NSE excludes algo orders within 0.75% of the LTP (cash and
  derivatives), designated-market-maker orders, and auction/pre-open/block sessions.
  Feed this engine post-exclusion counts; it cannot apply price-band tests without the
  order book.
"""
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class OTRConvention(str, Enum):
    """Which published ratio definition the configured limits are expressed in."""

    #: RTS 9 Art. 3(1): (total orders / total transactions) - 1. Measures *unexecuted*
    #: orders, so a fully-executing member scores 0.0.
    RTS9_UNEXECUTED = "RTS9_UNEXECUTED"

    #: Plain messages-per-trade, as used by venue fee schedules not derived from RTS 9
    #: (e.g. the NSE daily algo order-to-trade ratio).
    GROSS_MESSAGES_PER_TRADE = "GROSS_MESSAGES_PER_TRADE"


#: RTS 9 Annex, "Counting methodology for orders set out for each type". Only the rows
#: this engine models are reproduced; the Annex covers further order types, and Art. 3(4)
#: requires an unlisted type to be counted like the most similar listed one.
RTS9_ANNEX_WEIGHTS = {
    "limit_submit": 1,    # Annex: "Limit" / "Limit - add"
    "limit_cancel": 1,    # Annex: "Limit - delete"
    "limit_modify": 2,    # Annex: "Limit - modify" (a modification entails a
                          #        cancellation and a new insertion)
    "quote_submit": 2,    # Annex: "Quote" (1 for the buy side and 1 for the sell side)
    "quote_cancel": 2,    # Annex: "Quote - delete"
    "quote_modify": 4,    # Annex: "Quote - modify"
}


@dataclass(frozen=True)
class PenaltyTier:
    """
    One bracket of a progressive per-message penalty schedule.

    ``fee_per_message`` is charged on the messages that fall between
    ``ratio_from * transactions`` and ``ratio_to * transactions``. A schedule of a single
    tier with ``ratio_from`` set to the venue limit and ``ratio_to=None`` reproduces the
    flat "excess messages x flat fee" form used by the Eurex ESU formula.

    Ratios are expressed in the engine's configured ``OTRConvention``.
    """

    ratio_from: float
    ratio_to: Optional[float]
    fee_per_message: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.ratio_from) or self.ratio_from < 0.0:
            raise ValueError(
                f"ratio_from must be finite and >= 0, got {self.ratio_from!r}."
            )
        if self.ratio_to is not None:
            if not math.isfinite(self.ratio_to):
                raise ValueError(f"ratio_to must be finite or None, got {self.ratio_to!r}.")
            if self.ratio_to <= self.ratio_from:
                raise ValueError(
                    f"ratio_to ({self.ratio_to}) must exceed ratio_from ({self.ratio_from})."
                )
        if not math.isfinite(self.fee_per_message) or self.fee_per_message < 0.0:
            raise ValueError(
                f"fee_per_message must be finite and >= 0, got {self.fee_per_message!r}."
            )


#: NSE (India) daily algo order-to-trade ratio charges, per algo order, on an incremental
#: basis, in rupees (2 paise = INR 0.02). Source: NSE circular NSE/SURV/38122
#: (Ref. 161/2018, 22 June 2018), implementing para 14 of SEBI circular
#: SEBI/HO/MRD/DP/CIR/P/2018/62. A daily ratio below 50 is not charged.
#: These are the charge slabs only. The same circular also imposes non-monetary
#: consequences this engine does not model: a 15-minute cooling-off at the next open at a
#: ratio of 500 or more, and suspension of proprietary trading for the first hour after
#: more than ten penalised days in the previous thirty rolling trading days.
NSE_ALGO_OTR_PENALTY_TIERS_2018: Tuple[PenaltyTier, ...] = (
    PenaltyTier(ratio_from=50.0, ratio_to=250.0, fee_per_message=0.02),
    PenaltyTier(ratio_from=250.0, ratio_to=500.0, fee_per_message=0.10),
    PenaltyTier(ratio_from=500.0, ratio_to=None, fee_per_message=0.10),
)


@dataclass(frozen=True)
class OTRInstrumentSession:
    """
    One member's message and execution activity in **one financial instrument** over one
    trading session, already filtered for whatever orders the venue's regime excludes.

    RTS 9 Art. 2 requires the ratio to be calculated per member per instrument, and
    Art. 3(2) determines breach from activity "in one specific instrument, taking into
    account all phases of the trading session, including the auctions". Passing
    venue-wide totals here produces a number that is not the regulated ratio.

    ``transactions`` counts totally *or partially* executed orders (RTS 9 Art. 1(b)); a
    partial fill is a transaction.

    ``exempt_cancels`` are cancellation messages excluded by RTS 9 Art. 1(a): those sent
    subsequent to auction uncrossing, a loss of venue connectivity, or use of a kill
    functionality. They must also be included in ``limit_cancels`` / ``quote_cancels``;
    they are subtracted, not added.

    ``ordered_volume`` and ``traded_volume`` use the RTS 9 Art. 1(c) meaning of volume for
    the instrument's asset class: number of instruments for shares/ETFs/depositary
    receipts, nominal value for bonds and structured finance products, lots or contracts
    for derivatives, metric tonnes of CO2 for emission allowances. Both must be in the
    same unit.
    """

    venue: str
    instrument_id: str
    session_date: str
    limit_submits: int
    limit_modifies: int
    limit_cancels: int
    transactions: int
    ordered_volume: float
    traded_volume: float
    quote_submits: int = 0
    quote_modifies: int = 0
    quote_cancels: int = 0
    exempt_cancels: int = 0

    def __post_init__(self) -> None:
        for name in ("venue", "instrument_id", "session_date"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string, got {value!r}.")
        counters = (
            "limit_submits", "limit_modifies", "limit_cancels", "transactions",
            "quote_submits", "quote_modifies", "quote_cancels", "exempt_cancels",
        )
        for name in counters:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}.")
        for name in ("ordered_volume", "traded_volume"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite, got {value!r}.")
            if float(value) < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}.")

        total_cancel_messages = self.limit_cancels + self.quote_cancels
        if self.exempt_cancels > total_cancel_messages:
            raise ValueError(
                f"exempt_cancels ({self.exempt_cancels}) exceeds total cancel messages "
                f"({total_cancel_messages}); exempt cancels are a subset of cancels sent."
            )
        # A transaction is an executed order, so executed volume cannot exceed the volume
        # that was ordered. The reverse indicates mismatched units or a mis-wired feed --
        # which would otherwise surface as a spuriously compliant negative RTS 9 ratio.
        if float(self.traded_volume) > float(self.ordered_volume):
            raise ValueError(
                f"traded_volume ({self.traded_volume}) exceeds ordered_volume "
                f"({self.ordered_volume}); a transaction is an executed order, so this "
                f"indicates mismatched volume units or a mis-wired feed."
            )
        if self.transactions > 0 and float(self.traded_volume) <= 0.0:
            raise ValueError(
                f"transactions={self.transactions} but traded_volume={self.traded_volume}; "
                f"an executed order has non-zero volume."
            )


@dataclass(frozen=True)
class OTRThresholdPolicy:
    """
    The venue's published OTR limits and penalty schedule.

    There is deliberately no default for ``max_count_otr`` / ``max_volume_otr``: published
    limits differ by orders of magnitude between venues, so a plausible-looking default
    would silently mis-throttle. Read them from the venue's own schedule, expressed in the
    same ``convention``.

    ``warning_threshold_pct`` is an operational safety margin chosen by the user, not a
    regulatory quantity. No consulted venue publishes an "80% warning" tier.
    """

    max_count_otr: float
    max_volume_otr: float
    convention: OTRConvention = OTRConvention.RTS9_UNEXECUTED
    warning_threshold_pct: float = 0.80
    penalty_tiers: Tuple[PenaltyTier, ...] = ()
    penalty_currency: str = "EUR"

    def __post_init__(self) -> None:
        for name in ("max_count_otr", "max_volume_otr"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and > 0, got {value!r}.")
        if not 0.0 < float(self.warning_threshold_pct) <= 1.0:
            raise ValueError(
                f"warning_threshold_pct must be in (0, 1], "
                f"got {self.warning_threshold_pct!r}."
            )
        if not isinstance(self.convention, OTRConvention):
            raise TypeError(
                f"convention must be an OTRConvention, "
                f"got {type(self.convention).__name__}."
            )
        tiers = tuple(self.penalty_tiers)
        for earlier, later in zip(tiers, tiers[1:]):
            if earlier.ratio_to is None or later.ratio_from < earlier.ratio_to:
                raise ValueError(
                    "penalty_tiers must be ordered and non-overlapping; tier from "
                    f"{later.ratio_from} follows a tier ending at {earlier.ratio_to}."
                )
        object.__setattr__(self, "penalty_tiers", tiers)


@dataclass(frozen=True)
class OTRReport:
    """
    Result of one per-instrument audit.

    ``count_otr`` / ``volume_otr`` are ``None`` when the ratio is not calculable because
    the member has no transactions in the instrument for the session -- the treatment ICE
    Futures Europe and ICE Endex apply ("No OTR ratios will be calculated in case the
    member has not traded for the applicable trading session"). ``penalty_fee_accrued`` is
    ``None`` in the same case: the venue's own charge is not defined there either, and
    reporting 0.00 would read as "nothing owed" rather than "not calculable".
    """

    venue: str
    instrument_id: str
    session_date: str
    convention: OTRConvention
    total_order_messages: int
    transactions: int
    count_otr: Optional[float]
    volume_otr: Optional[float]
    excess_messages: int
    penalty_fee_accrued: Optional[float]
    penalty_currency: str
    binding_ratio: str                   # 'COUNT', 'VOLUME', 'BOTH', 'NONE'
    recommended_action: str              # 'ALLOW_ORDER', 'THROTTLE_ORDER_MODIFICATIONS',
                                         # 'FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL'
    status: str                          # see the STATUS_* constants below
    audit_notes: str


STATUS_COMPLIANT = "OTR_COMPLIANT_SAFE"
STATUS_WARNING = "OTR_WARNING_THROTTLE_ACTIVE"
STATUS_BREACH = "OTR_BREACH_PENALTY_ACTIVE"
STATUS_NO_TRANSACTIONS = "OTR_NOT_CALCULABLE_NO_TRANSACTIONS"

ACTION_ALLOW = "ALLOW_ORDER"
ACTION_THROTTLE = "THROTTLE_ORDER_MODIFICATIONS"
ACTION_FREEZE = "FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL"


def weighted_order_message_count(session: OTRInstrumentSession) -> int:
    """
    Number of "orders" the venue counts for ``session``, per the RTS 9 Annex.

    Applies the Annex weights (a limit modify is two orders, a quote is two, a quote
    modify is four) and removes the Art. 1(a) exempt cancellations. Weighting every
    message as one -- the common shortcut -- understates the ratio for any strategy that
    reprices rather than cancel-replaces, and understates it fourfold for a two-sided
    quoting strategy.
    """
    countable_cancels = (
        session.limit_cancels + session.quote_cancels - session.exempt_cancels
    )
    # The exempt cancels are pooled, so their split between limit and quote cancels is not
    # recoverable. Removing them from the *limit* cancels first (weight 1) subtracts the
    # least from the count, which is the conservative direction: the ratio is never
    # understated, so the guard never under-throttles on this ambiguity.
    quote_cancel_messages = min(session.quote_cancels, max(0, countable_cancels))
    limit_cancel_messages = max(0, countable_cancels - quote_cancel_messages)

    return (
        session.limit_submits * RTS9_ANNEX_WEIGHTS["limit_submit"]
        + session.limit_modifies * RTS9_ANNEX_WEIGHTS["limit_modify"]
        + limit_cancel_messages * RTS9_ANNEX_WEIGHTS["limit_cancel"]
        + session.quote_submits * RTS9_ANNEX_WEIGHTS["quote_submit"]
        + session.quote_modifies * RTS9_ANNEX_WEIGHTS["quote_modify"]
        + quote_cancel_messages * RTS9_ANNEX_WEIGHTS["quote_cancel"]
    )


def _apply_convention(gross_ratio: float, convention: OTRConvention) -> float:
    """Converts a gross orders/transactions ratio into the configured convention."""
    if convention is OTRConvention.RTS9_UNEXECUTED:
        return gross_ratio - 1.0
    return gross_ratio


def tiered_penalty(
    total_messages: int,
    transactions: int,
    tiers: Sequence[PenaltyTier],
    convention: OTRConvention = OTRConvention.GROSS_MESSAGES_PER_TRADE,
) -> float:
    """
    Progressive per-message penalty over ``tiers``, charged on an incremental basis.

    Each tier's message bracket runs from ``ratio_from * transactions`` to
    ``ratio_to * transactions`` (unbounded above when ``ratio_to`` is ``None``), matching
    the NSE "on incremental basis" slab structure. Under
    ``OTRConvention.RTS9_UNEXECUTED`` the boundaries are offset by one transaction's worth
    of messages, since an RTS 9 ratio of *r* corresponds to *(r + 1)* gross messages per
    transaction.

    With a single tier starting at the venue limit and unbounded above this reduces to the
    Eurex ESU form, ``(messages - limit) * fee``.
    """
    if transactions <= 0:
        raise ValueError("tiered_penalty requires transactions > 0; the ratio is undefined.")
    offset = 1.0 if convention is OTRConvention.RTS9_UNEXECUTED else 0.0
    fee = 0.0
    for tier in tiers:
        lower = (tier.ratio_from + offset) * transactions
        upper = (
            math.inf if tier.ratio_to is None else (tier.ratio_to + offset) * transactions
        )
        messages_in_tier = min(float(total_messages), upper) - lower
        if messages_in_tier > 0.0:
            fee += messages_in_tier * tier.fee_per_message
    return fee


class OrderToTradeRatioFeePenaltyEngine:
    """
    Per-instrument order-to-trade ratio monitor, penalty estimator and throttling guard.

    Evaluates both the count and volume ratios and breaches on either, as RTS 9 Art. 3(2)
    requires ("exceeds either or both of the two ratios"). The engine is stateless: one
    call audits one instrument-session, so the caller controls the observation window.
    RTS 9 recital 7 caps that window at one trading session but permits shorter ones,
    which is what makes intra-session throttling possible.
    """

    def __init__(self, policy: OTRThresholdPolicy):
        if not isinstance(policy, OTRThresholdPolicy):
            raise TypeError(
                f"policy must be an OTRThresholdPolicy, got {type(policy).__name__}. "
                f"There is no default policy: OTR limits must come from the venue's "
                f"published schedule."
            )
        self.policy = policy

    def audit_session_otr(self, session: OTRInstrumentSession) -> OTRReport:
        """
        Audits one instrument-session, returning both ratios, the estimated penalty and
        the recommended order-flow action.

        Raises ``TypeError``/``ValueError`` on malformed input rather than substituting a
        placeholder denominator: an OTR computed against a fabricated trade count is worse
        than no OTR, because it reads as a measurement.
        """
        if not isinstance(session, OTRInstrumentSession):
            raise TypeError(
                f"session must be an OTRInstrumentSession, got {type(session).__name__}."
            )

        policy = self.policy
        total_messages = weighted_order_message_count(session)

        if session.transactions <= 0:
            return self._no_transaction_report(session, total_messages)

        gross_count_ratio = total_messages / float(session.transactions)
        gross_volume_ratio = float(session.ordered_volume) / float(session.traded_volume)
        count_otr = _apply_convention(gross_count_ratio, policy.convention)
        volume_otr = _apply_convention(gross_volume_ratio, policy.convention)

        count_breach = count_otr >= policy.max_count_otr
        volume_breach = volume_otr >= policy.max_volume_otr
        count_warning = count_otr >= policy.max_count_otr * policy.warning_threshold_pct
        volume_warning = volume_otr >= policy.max_volume_otr * policy.warning_threshold_pct

        if count_breach or volume_breach:
            status, action = STATUS_BREACH, ACTION_FREEZE
            binding = self._binding_label(count_breach, volume_breach)
        elif count_warning or volume_warning:
            status, action = STATUS_WARNING, ACTION_THROTTLE
            binding = self._binding_label(count_warning, volume_warning)
        else:
            status, action = STATUS_COMPLIANT, ACTION_ALLOW
            binding = "NONE"

        # Excess is a count-ratio quantity: the number of order messages beyond what the
        # count limit permits for the transactions actually achieved. A volume-only breach
        # produces zero excess messages and is still a breach -- do not read
        # excess_messages == 0 as "compliant".
        offset = 1.0 if policy.convention is OTRConvention.RTS9_UNEXECUTED else 0.0
        allowed_messages = (policy.max_count_otr + offset) * session.transactions
        excess_messages = max(0, total_messages - int(math.floor(allowed_messages)))

        penalty_fee = tiered_penalty(
            total_messages, session.transactions, policy.penalty_tiers, policy.convention
        )

        notes = (
            f"OTR AUDIT [{session.venue}/{session.instrument_id} {session.session_date} - "
            f"{status}]: convention={policy.convention.value}, weighted order messages="
            f"{total_messages}, transactions={session.transactions}. "
            f"Count OTR={count_otr:.2f} (limit {policy.max_count_otr:.2f}), "
            f"Volume OTR={volume_otr:.2f} (limit {policy.max_volume_otr:.2f}), "
            f"binding={binding}. Excess messages={excess_messages}, estimated penalty="
            f"{penalty_fee:,.2f} {policy.penalty_currency}. Action: '{action}'. "
            f"Venue reporting is authoritative; this is a client-side estimate."
        )
        self._log(status, notes)

        return OTRReport(
            venue=session.venue,
            instrument_id=session.instrument_id,
            session_date=session.session_date,
            convention=policy.convention,
            total_order_messages=total_messages,
            transactions=session.transactions,
            count_otr=round(count_otr, 4),
            volume_otr=round(volume_otr, 4),
            excess_messages=excess_messages,
            penalty_fee_accrued=round(penalty_fee, 2),
            penalty_currency=policy.penalty_currency,
            binding_ratio=binding,
            recommended_action=action,
            status=status,
            audit_notes=notes,
        )

    @staticmethod
    def _binding_label(count_flag: bool, volume_flag: bool) -> str:
        if count_flag and volume_flag:
            return "BOTH"
        return "COUNT" if count_flag else "VOLUME"

    def _no_transaction_report(
        self, session: OTRInstrumentSession, total_messages: int
    ) -> OTRReport:
        """
        Handles the zero-transaction case without inventing a denominator.

        ICE Futures Europe and ICE Endex state that no OTR is calculated when the member
        has not traded in the session. Substituting ``max(1, transactions)`` instead --
        the obvious shortcut -- silently grants a full limit's worth of free messages the
        venue never granted, and reports a ratio that is not the venue's.

        With no messages either, the member is simply idle and order flow is allowed. With
        messages but no executions, every message sent is unexecuted: that is the worst
        state the regime exists to catch, so order flow is frozen rather than allowed.
        """
        if total_messages == 0:
            status, action = STATUS_COMPLIANT, ACTION_ALLOW
            detail = "no order messages and no transactions; member idle in this instrument."
        else:
            status, action = STATUS_NO_TRANSACTIONS, ACTION_FREEZE
            detail = (
                f"{total_messages} weighted order messages with zero transactions; the "
                f"ratio is not calculable and every message sent is unexecuted."
            )
        notes = (
            f"OTR AUDIT [{session.venue}/{session.instrument_id} {session.session_date} - "
            f"{status}]: {detail} Action: '{action}'."
        )
        self._log(status, notes)
        return OTRReport(
            venue=session.venue,
            instrument_id=session.instrument_id,
            session_date=session.session_date,
            convention=self.policy.convention,
            total_order_messages=total_messages,
            transactions=0,
            count_otr=None,
            volume_otr=None,
            excess_messages=0,
            penalty_fee_accrued=None,
            penalty_currency=self.policy.penalty_currency,
            binding_ratio="NONE",
            recommended_action=action,
            status=status,
            audit_notes=notes,
        )

    @staticmethod
    def _log(status: str, notes: str) -> None:
        if status == STATUS_BREACH:
            logger.error("%s", notes)
        elif status in (STATUS_WARNING, STATUS_NO_TRANSACTIONS):
            logger.warning("%s", notes)
        else:
            logger.info("%s", notes)


#: Ranked worst-first so the caller acts on the instrument that binds. A venue-wide
#: "average OTR" is not a regulated quantity and can read compliant while one instrument
#: breaches -- exactly the case RTS 9 Art. 3(2) targets.
_STATUS_SEVERITY = {
    STATUS_COMPLIANT: 0,
    STATUS_WARNING: 1,
    STATUS_NO_TRANSACTIONS: 2,
    STATUS_BREACH: 3,
}


def aggregate_worst_instrument(reports: Iterable[OTRReport]) -> Optional[OTRReport]:
    """
    Returns the most severe of ``reports``, or ``None`` if ``reports`` is empty.

    Use this to drive a venue-level kill decision from per-instrument audits. Ties break
    on the larger count ratio, then the larger volume ratio; a not-calculable ratio sorts
    above any finite one, because unbounded unexecuted traffic is the worse state.
    """
    ranked: List[Tuple[Tuple[int, float, float], OTRReport]] = []
    for report in reports:
        count_key = math.inf if report.count_otr is None else float(report.count_otr)
        volume_key = math.inf if report.volume_otr is None else float(report.volume_otr)
        ranked.append(((_STATUS_SEVERITY[report.status], count_key, volume_key), report))
    if not ranked:
        return None
    return max(ranked, key=lambda item: item[0])[1]
