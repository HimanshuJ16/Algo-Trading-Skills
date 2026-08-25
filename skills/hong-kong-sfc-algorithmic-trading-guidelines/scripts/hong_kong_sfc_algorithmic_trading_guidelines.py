"""Hong Kong SFC electronic / algorithmic trading pre-trade compliance gate.

Evaluates a single order against the Hong Kong requirements that a licensed or
registered person's algorithmic trading system must satisfy *before* the order
reaches the Stock Exchange of Hong Kong (SEHK), and produces an auditable
record of the decision.

Regulatory anchors (full citations in ``references/standards.md``):

* **Code of Conduct paragraph 18 and Schedule 7** -- Schedule 7 is titled
  "Additional requirements for licensed or registered persons conducting
  electronic trading". Its structure matters, because the paragraph numbers are
  routinely misquoted:

  - **1.1 Management and supervision** -- written policies, at least one
    responsible officer or executive officer accountable for the electronic
    trading system (1.1.1(a)), managerial and supervisory controls designed to
    manage the risks of using it (1.1.1(d)), adequately qualified staff (1.1.4).
  - **1.2.1 Adequacy of system -- system controls** -- the system must have
    effective controls to enable the firm, where necessary, to "(a) immediately
    prevent the system from generating and sending orders to the market; and
    (b) cancel any unexecuted orders that are in the market." *This* is the
    kill-switch requirement; there is no "Schedule 7 paragraph 4".
  - **1.3 Record keeping** -- audit logs and incident reports retained for "a
    period of not less than 2 years" (1.3.2(b)); the Annex sets out what an
    audit log must contain, including order placement/cancellation/modification
    "with time stamping and the assignment of unique reference number",
    compliance validation exceptions, and erroneous order inputs.
  - **2.1 Risk management: internet trading and DMA** -- client orders must be
    subject to "appropriate automated pre-trade risk management controls" and
    regular post-trade monitoring; 2.1.1(a) enumerates preventing orders that
    exceed prescribed thresholds, limiting the firm's financial exposure,
    preventing erroneous orders, and preventing orders that are not in
    compliance with regulatory requirements.
  - **3 Specific requirements on algorithmic trading** -- 3.1 qualification of
    the people who design/develop and of those approved to use the system;
    3.2.1 adequate testing before deployment and 3.2.2 review at least
    annually; 3.3.1 controls reasonably designed to prevent erroneous orders or
    orders that interfere with a fair and orderly market; 3.4.2 records of "all
    the parameters which its algorithmic trading system and trading algorithms
    take into account for each order" retained for not less than 2 years.

* **SFC Circular to all Licensed Corporations on Algorithmic Trading**,
  13 December 2016 (SFO/IS/044/2016) -- the thematic-review findings this module
  is shaped around. The SFC criticised kill switches implemented only "at the
  exchange connectivity level or the algorithmic engine level ... instead of
  implementing them at more disaggregated levels (eg, relating to a particular
  client or algorithmic strategy)", and pre-trade price limits overridden on
  nothing more than verbal approval. Its Good Practices appendix enumerates the
  control suite modelled here: price controls, maximum order value, maximum
  order volume (for example against average daily volume), maximum message
  limits, and quantity/price checks preventing child orders from exceeding the
  parent order.

* **SFO section 170** -- criminal offence to sell securities at or through a
  recognized stock market without a "presently exercisable and unconditional
  right to vest the securities in the purchaser", or reasonable grounds to
  believe one has it. Maximum penalty HK$100,000 and 2 years' imprisonment.
* **SFO section 171** -- the seller must identify the order as a short selling
  order and provide documentary assurance that it is covered, at the time the
  order is placed; the intermediary must obtain it before transmitting the order
  and retain it for at least 12 months.
* **SFO section 172** -- the exchange participant inputting a short selling
  order must mark it "short"; SEHK Eleventh Schedule Regulation (5)(b) is the
  Exchange-level counterpart.
* **SEHK Rules of the Exchange, Rule 563D(1)** -- outside the enumerated exempt
  categories, short selling is limited to Designated Securities, in the
  Pre-opening Session (POS securities), the Continuous Trading Session, and the
  Closing Auction Session (CAS securities); in POS and CAS "only at-auction
  limit orders may be input into the System as short selling orders".
* **SEHK Eleventh Schedule Regulation (15)** -- the tick rule: a short sale of a
  Designated Security "shall not be made on the Exchange below the best current
  ask price (during the Continuous Trading Session) or the CAS reference price
  (during the Closing Auction Session)"; Rule 501(G)(3)(d) applies the POS
  reference price during the Pre-opening Session.

**No numeric pre-trade threshold in this module is set by the SFC or SEHK.**
The HK$10,000,000 notional cap and the 5% price band are placeholders. The SFC
requires controls that are *reasonably designed*, and its 2016 circular's first
risk-management finding was precisely the absence of documented analysis behind
threshold parameter values. Calibrate them, document the rationale, and review
them -- do not cite this file as authority for a number it merely defaults to.

Two deliberate error-handling rules run through the module:

* **The caller's own order fields raise.** An unknown session, side, order type
  or exemption token, an empty ``algo_id``, a non-positive quantity or a
  non-finite price is a defect in the strategy, not a compliance decision.
  ``ValueError`` is loud; a silent pass is how a malformed order reaches SEHK.
* **Missing external market data fails closed.** A reference price the gate
  needs but did not receive produces ``MISSING_MARKET_DATA`` and blocks the
  order. A control that cannot be evaluated has not been satisfied.

This module is a decision and evidence engine, not a trading system. It does not
cancel orders, does not query the Designated Securities list, and its in-memory
``audit_trail`` is a reference adapter -- Schedule 7 paragraph 1.3.2 retention
requires a durable, append-only store.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Deque, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- SEHK trading sessions (Rules of the Exchange, Chapter 5) ---------------
SESSION_POS = "POS"
"""Pre-opening Session."""
SESSION_CTS = "CTS"
"""Continuous Trading Session."""
SESSION_CAS = "CAS"
"""Closing Auction Session."""

KNOWN_SESSIONS: FrozenSet[str] = frozenset({SESSION_POS, SESSION_CTS, SESSION_CAS})

#: Sessions in which a short selling order may be input under Rule 563D(1).
#: Auction sessions additionally restrict the permitted order type.
SHORT_SELL_AUCTION_SESSIONS: FrozenSet[str] = frozenset({SESSION_POS, SESSION_CAS})

# --- Order types ------------------------------------------------------------
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_ENHANCED_LIMIT = "ENHANCED_LIMIT"
ORDER_TYPE_SPECIAL_LIMIT = "SPECIAL_LIMIT"
ORDER_TYPE_AT_AUCTION = "AT_AUCTION"
ORDER_TYPE_AT_AUCTION_LIMIT = "AT_AUCTION_LIMIT"

KNOWN_ORDER_TYPES: FrozenSet[str] = frozenset(
    {
        ORDER_TYPE_LIMIT,
        ORDER_TYPE_ENHANCED_LIMIT,
        ORDER_TYPE_SPECIAL_LIMIT,
        ORDER_TYPE_AT_AUCTION,
        ORDER_TYPE_AT_AUCTION_LIMIT,
    }
)

# --- Order sides ------------------------------------------------------------
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDE_SHORT_SELL = "SHORT_SELL"

KNOWN_SIDES: FrozenSet[str] = frozenset({SIDE_BUY, SIDE_SELL, SIDE_SHORT_SELL})

# --- Kill switch scopes -----------------------------------------------------
#: The SFC's 2016 circular treats a firm-wide-only kill switch as a deficiency:
#: it removes any ability to stop one client or one strategy without stopping
#: everything. Scoped switches are the remedy, not an embellishment.
KILL_SWITCH_SCOPE_FIRM = "FIRM"
KILL_SWITCH_SCOPE_ALGO = "ALGO"
KILL_SWITCH_SCOPE_CLIENT = "CLIENT"

KNOWN_KILL_SWITCH_SCOPES: FrozenSet[str] = frozenset(
    {KILL_SWITCH_SCOPE_FIRM, KILL_SWITCH_SCOPE_ALGO, KILL_SWITCH_SCOPE_CLIENT}
)

#: Scopes that are keyed by an identifier (algo id / client id). FIRM is not.
KEYED_KILL_SWITCH_SCOPES: FrozenSet[str] = frozenset(
    {KILL_SWITCH_SCOPE_ALGO, KILL_SWITCH_SCOPE_CLIENT}
)

# --- Rule 563D(1) exempt short selling categories ---------------------------
#: Participants for whom Rule 563D(1) disapplies the Designated Securities
#: restriction. Claiming one of these is a supervised, evidenced status -- the
#: gate records the claim, it cannot verify it.
EXEMPT_SECURITIES_MARKET_MAKER = "SECURITIES_MARKET_MAKER"
EXEMPT_DUAL_COUNTER_MARKET_MAKER = "DUAL_COUNTER_MARKET_MAKER"
EXEMPT_STRUCTURED_PRODUCT_LIQUIDITY_PROVIDER = "STRUCTURED_PRODUCT_LIQUIDITY_PROVIDER"
EXEMPT_DESIGNATED_INDEX_ARBITRAGE = "DESIGNATED_INDEX_ARBITRAGE"
EXEMPT_STOCK_FUTURES_HEDGING = "STOCK_FUTURES_HEDGING"
EXEMPT_STRUCTURED_PRODUCT_HEDGING = "STRUCTURED_PRODUCT_HEDGING"
EXEMPT_OPTIONS_HEDGING = "OPTIONS_HEDGING"

KNOWN_EXEMPT_SHORT_SELL_CATEGORIES: FrozenSet[str] = frozenset(
    {
        EXEMPT_SECURITIES_MARKET_MAKER,
        EXEMPT_DUAL_COUNTER_MARKET_MAKER,
        EXEMPT_STRUCTURED_PRODUCT_LIQUIDITY_PROVIDER,
        EXEMPT_DESIGNATED_INDEX_ARBITRAGE,
        EXEMPT_STOCK_FUTURES_HEDGING,
        EXEMPT_STRUCTURED_PRODUCT_HEDGING,
        EXEMPT_OPTIONS_HEDGING,
    }
)

# --- Violation codes --------------------------------------------------------
VIOLATION_KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
VIOLATION_ALGO_NOT_AUTHORISED = "ALGO_NOT_AUTHORISED"
VIOLATION_ALGO_NOT_TESTED = "ALGO_NOT_TESTED"
VIOLATION_OPERATOR_NOT_APPROVED = "OPERATOR_NOT_APPROVED"
VIOLATION_ILLEGAL_NAKED_SHORT = "ILLEGAL_NAKED_SHORT"
VIOLATION_SHORT_SELL_ASSURANCE_MISSING = "SHORT_SELL_ASSURANCE_MISSING"
VIOLATION_SHORT_SELL_NOT_FLAGGED = "SHORT_SELL_NOT_FLAGGED"
VIOLATION_SHORT_SELL_NOT_DESIGNATED = "SHORT_SELL_NOT_DESIGNATED"
VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED = "SHORT_SELL_ORDER_TYPE_NOT_PERMITTED"
VIOLATION_SHORT_SELL_TICK_RULE = "SHORT_SELL_TICK_RULE"
VIOLATION_MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
VIOLATION_ORDER_VALUE_LIMIT = "ORDER_VALUE_LIMIT"
VIOLATION_ORDER_QUANTITY_LIMIT = "ORDER_QUANTITY_LIMIT"
VIOLATION_PRICE_DEVIATION_LIMIT = "PRICE_DEVIATION_LIMIT"
VIOLATION_ADV_PARTICIPATION_LIMIT = "ADV_PARTICIPATION_LIMIT"
VIOLATION_CHILD_PRICE_EXCEEDS_PARENT = "CHILD_PRICE_EXCEEDS_PARENT"
VIOLATION_CHILD_QUANTITY_EXCEEDS_PARENT = "CHILD_QUANTITY_EXCEEDS_PARENT"
VIOLATION_MESSAGE_RATE_LIMIT = "MESSAGE_RATE_LIMIT"

#: Deterministic reporting order. Every breach is recorded in
#: ``HkSfcComplianceReport.violations``; the *first* one in this order supplies
#: the headline ``status``. Statutory breaches outrank Exchange rules, which
#: outrank firm-calibrated thresholds -- so an order that is both a naked short
#: and oversized is reported as the criminal offence it is.
VIOLATION_PRECEDENCE: Tuple[str, ...] = (
    VIOLATION_KILL_SWITCH_ACTIVE,
    VIOLATION_ALGO_NOT_AUTHORISED,
    VIOLATION_ALGO_NOT_TESTED,
    VIOLATION_OPERATOR_NOT_APPROVED,
    VIOLATION_ILLEGAL_NAKED_SHORT,
    VIOLATION_SHORT_SELL_ASSURANCE_MISSING,
    VIOLATION_SHORT_SELL_NOT_FLAGGED,
    VIOLATION_SHORT_SELL_NOT_DESIGNATED,
    VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED,
    VIOLATION_SHORT_SELL_TICK_RULE,
    VIOLATION_MISSING_MARKET_DATA,
    VIOLATION_ORDER_VALUE_LIMIT,
    VIOLATION_ORDER_QUANTITY_LIMIT,
    VIOLATION_PRICE_DEVIATION_LIMIT,
    VIOLATION_ADV_PARTICIPATION_LIMIT,
    VIOLATION_CHILD_PRICE_EXCEEDS_PARENT,
    VIOLATION_CHILD_QUANTITY_EXCEEDS_PARENT,
    VIOLATION_MESSAGE_RATE_LIMIT,
)

#: Breaches of the Securities and Futures Ordinance or the Rules of the
#: Exchange, as distinct from breaches of firm-calibrated thresholds. Logged at
#: CRITICAL because they are not a tuning question.
STATUTORY_OR_EXCHANGE_VIOLATIONS: FrozenSet[str] = frozenset(
    {
        VIOLATION_ILLEGAL_NAKED_SHORT,
        VIOLATION_SHORT_SELL_ASSURANCE_MISSING,
        VIOLATION_SHORT_SELL_NOT_FLAGGED,
        VIOLATION_SHORT_SELL_NOT_DESIGNATED,
        VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED,
        VIOLATION_SHORT_SELL_TICK_RULE,
    }
)

STATUS_APPROVED = "SFC_COMPLIANT_APPROVED"
"""Status when no control was breached. Approval by this gate is not legal
advice and does not discharge Schedule 7 paragraph 3.3.2 post-trade review."""


def _rejected_status(violation: str) -> str:
    """Headline status string for a violation code."""
    return "REJECTED_" + violation


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _usable_positive_number(value: Optional[float]) -> bool:
    """Usable only if present, finite and strictly positive.

    Applied to every externally sourced quantity the controls depend on -- a
    nominal price, a short selling reference price, an average daily volume.
    ``None``, ``NaN``, ``inf`` and ``0`` all mean "cannot be evaluated".
    """
    return value is not None and _is_finite_number(value) and float(value) > 0.0


@dataclass(frozen=True)
class HkSfcOrderRequest:
    """One order presented to the pre-trade gate.

    Fields fall into three groups: identifiers, the order itself, and the
    external context (market data, reference data and firm attestations) the
    controls need. Anything the caller cannot assert defaults to the *unsafe*
    value so that an unstated fact blocks a short sale rather than waving it
    through.
    """

    algo_id: str
    stock_code: str
    side: str
    order_price: float
    order_quantity: int

    # --- Market data (external; missing values fail closed) ----------------
    #: Nominal / last traded price, used for the firm's price band. SEHK's own
    #: Rules 505A and 506A/507A police order prices against the nominal price
    #: and the current bid/ask, not against the last trade -- this band is a
    #: firm erroneous-order control, not the Exchange's.
    market_last_price: Optional[float] = None
    #: Session-dependent short selling reference price (Eleventh Schedule
    #: Regulation (15) / Rule 501(G)(3)(d)): the best current ask in CTS, the
    #: POS reference price in POS, the CAS reference price in CAS.
    short_sell_reference_price: Optional[float] = None
    #: Average daily volume for the optional participation cap, in shares.
    average_daily_volume: Optional[float] = None

    # --- Session and order type --------------------------------------------
    session: str = SESSION_CTS
    order_type: str = ORDER_TYPE_LIMIT

    # --- Firm attestations (Schedule 7 paragraphs 1.1, 3.1, 3.2) ------------
    #: The algorithm is signed off for production use under the firm's
    #: governance process (Schedule 7 paragraphs 1.1.1(b) and (d)).
    algo_authorised_for_production: bool = True
    #: Pre-deployment testing was completed for this version of the algorithm
    #: (Schedule 7 paragraph 3.2.1).
    algo_testing_signed_off: bool = True
    #: The submitting operator is approved to use the algorithmic trading
    #: system (Schedule 7 paragraph 3.1.2).
    operator_approved_to_use: bool = True

    # --- Short selling ------------------------------------------------------
    is_short_sell: bool = False
    #: SFO section 170 cover: a presently exercisable and unconditional right
    #: to vest the securities in the purchaser (typically a confirmed borrow).
    has_locate_borrow: bool = False
    #: Reference to the SFO section 171 documentary assurance held for this
    #: order. The gate checks that one is recorded, not that it is valid.
    documentary_assurance_ref: Optional[str] = None
    #: The order will be marked "short" on input (SFO section 172, Eleventh
    #: Schedule Regulation (5)(b)).
    short_sell_flagged: bool = False
    #: The stock is on SEHK's Designated Securities Eligible for Short Selling
    #: list *as at this order's date*. The list is revised periodically.
    is_designated_security: bool = False
    #: One of ``KNOWN_EXEMPT_SHORT_SELL_CATEGORIES`` where Rule 563D(1)
    #: disapplies the Designated Securities restriction. Never exempts SFO
    #: section 170 cover in this module.
    exempt_short_sell_category: Optional[str] = None

    # --- Parent / child context (SFC 2016 circular, Good Practices) ---------
    client_id: Optional[str] = None
    parent_order_id: Optional[str] = None
    parent_limit_price: Optional[float] = None
    parent_remaining_quantity: Optional[int] = None

    #: Caller-supplied unique reference for the audit log (Schedule 7 Annex).
    #: Generated from the decision sequence when absent.
    order_reference: Optional[str] = None


@dataclass(frozen=True)
class KillSwitchState:
    """An active emergency shutdown, with the attribution the SFC expects."""

    scope: str
    key: Optional[str]
    reason: str
    activated_by: str
    activated_at: str

    def label(self) -> str:
        return self.scope if self.key is None else f"{self.scope}:{self.key}"


@dataclass(frozen=True)
class HkSfcComplianceReport:
    """The compliance decision and the evidence behind it.

    This object *is* the audit-log entry contemplated by Schedule 7 paragraph
    1.3.1(c) and its Annex: a time-stamped, uniquely referenced record of the
    order and of every compliance validation exception raised against it.
    """

    algo_id: str
    stock_code: str
    session: str
    side: str
    order_reference: str
    decision_time_utc: str
    order_value_hkd: float
    #: ``None`` when no usable nominal price was supplied, in which case
    #: ``MISSING_MARKET_DATA`` appears in ``violations``. Never reported as 0.0.
    price_deviation_pct: Optional[float]
    is_short_sell: bool
    #: ``None`` when the order is not a short sale -- the question does not
    #: arise. ``False`` means a short sale breached at least one of SFO
    #: sections 170-172 or the Exchange's short selling rules, *or* that a
    #: control could not be evaluated for want of market data.
    is_short_sell_legal: Optional[bool]
    is_algo_authorised: bool
    is_kill_switch_active: bool
    kill_switch_scopes: Tuple[str, ...]
    status: str
    violations: Tuple[str, ...]
    blocks_order: bool
    audit_notes: str

    def as_audit_record(self) -> Dict[str, object]:
        """Flat dict for persistence to an append-only store.

        Schedule 7 paragraph 1.3.2(b) requires audit logs to be retained for
        not less than 2 years, and paragraph 3.4.2 requires the parameters the
        algorithm took into account for each order to be retained for the same
        period. Persisting this record satisfies neither on its own -- the
        store must be durable and the algorithm's own parameters recorded
        alongside it.
        """
        return {
            "order_reference": self.order_reference,
            "decision_time_utc": self.decision_time_utc,
            "algo_id": self.algo_id,
            "stock_code": self.stock_code,
            "session": self.session,
            "side": self.side,
            "order_value_hkd": self.order_value_hkd,
            "price_deviation_pct": self.price_deviation_pct,
            "is_short_sell": self.is_short_sell,
            "is_short_sell_legal": self.is_short_sell_legal,
            "is_algo_authorised": self.is_algo_authorised,
            "is_kill_switch_active": self.is_kill_switch_active,
            "kill_switch_scopes": list(self.kill_switch_scopes),
            "status": self.status,
            "violations": list(self.violations),
            "blocks_order": self.blocks_order,
            "audit_notes": self.audit_notes,
        }


class HkSfcAlgorithmicTradingEngine:
    """Pre-trade compliance gate for algorithmic trading into SEHK.

    Every order is evaluated against all applicable controls; the engine does
    not stop at the first breach, because Schedule 7's Annex expects compliance
    validation exceptions to be logged and a single-reason rejection hides the
    rest of what was wrong with the order.

    Thresholds are firm parameters, not regulatory ones. The defaults exist so
    the class is usable in a test; the SFC's expectation (2016 circular, risk
    management finding i) is documented analysis behind whatever values you
    choose.

    The engine holds mutable state -- kill switches, message counters and the
    audit trail -- and is guarded by an internal re-entrant lock so it can sit
    on a shared order path.

    ``audit_sink`` is called once per decision, after the lock is released, and
    its exceptions propagate to the caller. That is deliberate: if the durable
    record required by Schedule 7 paragraph 1.3.2(b) cannot be written, the
    order should not proceed on the strength of a decision nobody can produce
    later.
    """

    def __init__(
        self,
        max_order_value_hkd: float = 10_000_000.0,
        max_price_deviation_pct: float = 5.0,
        max_order_quantity: Optional[int] = None,
        max_adv_participation_pct: Optional[float] = None,
        max_messages_per_interval: Optional[int] = None,
        message_interval_seconds: float = 1.0,
        clock: Optional[Callable[[], datetime]] = None,
        audit_sink: Optional[Callable[[HkSfcComplianceReport], None]] = None,
    ) -> None:
        if not _is_finite_number(max_order_value_hkd) or float(max_order_value_hkd) <= 0.0:
            raise ValueError("max_order_value_hkd must be a finite positive number")
        if not _is_finite_number(max_price_deviation_pct) or float(max_price_deviation_pct) <= 0.0:
            raise ValueError("max_price_deviation_pct must be a finite positive number")
        if max_order_quantity is not None and (
            not isinstance(max_order_quantity, int)
            or isinstance(max_order_quantity, bool)
            or max_order_quantity <= 0
        ):
            raise ValueError("max_order_quantity must be a positive integer or None")
        if max_adv_participation_pct is not None and (
            not _is_finite_number(max_adv_participation_pct) or float(max_adv_participation_pct) <= 0.0
        ):
            raise ValueError("max_adv_participation_pct must be a finite positive number or None")
        if max_messages_per_interval is not None and (
            not isinstance(max_messages_per_interval, int)
            or isinstance(max_messages_per_interval, bool)
            or max_messages_per_interval <= 0
        ):
            raise ValueError("max_messages_per_interval must be a positive integer or None")
        if not _is_finite_number(message_interval_seconds) or float(message_interval_seconds) <= 0.0:
            raise ValueError("message_interval_seconds must be a finite positive number")

        self.max_order_value_hkd = float(max_order_value_hkd)
        self.max_price_deviation_pct = float(max_price_deviation_pct)
        self.max_order_quantity = max_order_quantity
        self.max_adv_participation_pct = (
            None if max_adv_participation_pct is None else float(max_adv_participation_pct)
        )
        self.max_messages_per_interval = max_messages_per_interval
        self.message_interval = timedelta(seconds=float(message_interval_seconds))

        self._clock: Callable[[], datetime] = clock or _default_clock
        self._audit_sink = audit_sink
        self._lock = threading.RLock()
        self._kill_switches: Dict[Tuple[str, Optional[str]], KillSwitchState] = {}
        self._message_times: Dict[str, Deque[datetime]] = {}
        self._audit_trail: List[HkSfcComplianceReport] = []
        self._decision_count = 0

    # -- Kill switch (Schedule 7 paragraph 1.2.1) ---------------------------

    @property
    def is_kill_switch_active(self) -> bool:
        """True while any kill switch -- firm, algo or client -- is engaged."""
        with self._lock:
            return bool(self._kill_switches)

    def trigger_sfc_kill_switch(
        self,
        reason: str,
        activated_by: str,
        scope: str = KILL_SWITCH_SCOPE_FIRM,
        key: Optional[str] = None,
    ) -> str:
        """Engage an emergency shutdown at ``scope``.

        Schedule 7 paragraph 1.2.1 requires the ability to immediately prevent
        the system from generating and sending orders *and* to cancel
        unexecuted orders already in the market. This method does the first
        half: it blocks new submissions at the gate. Cancelling resting orders
        is the caller's job and must be wired to the broker or exchange
        session -- an engaged switch here does not touch the order book.

        ``reason`` and ``activated_by`` are mandatory. The SFC's 2016 circular
        singled out a control override authorised by nothing more than a verbal
        approval; an unattributed shutdown is the same defect.
        """
        scope = self._validate_kill_switch_scope(scope, key)
        reason = self._require_text(reason, "reason")
        activated_by = self._require_text(activated_by, "activated_by")

        with self._lock:
            state = KillSwitchState(
                scope=scope,
                key=key,
                reason=reason,
                activated_by=activated_by,
                activated_at=self._now_iso(),
            )
            self._kill_switches[(scope, key)] = state

        msg = (
            f"HK SFC KILL SWITCH ACTIVATED [{state.label()}] by {activated_by} "
            f"at {state.activated_at}: {reason}. New algo order submissions HALTED at the "
            f"pre-trade gate -- cancellation of resting orders must be initiated separately "
            f"(Schedule 7 paragraph 1.2.1(b))."
        )
        logger.critical(msg)
        return msg

    def reset_kill_switch(
        self,
        reason: str,
        reset_by: str,
        scope: str = KILL_SWITCH_SCOPE_FIRM,
        key: Optional[str] = None,
    ) -> None:
        """Release a kill switch, recording who released it and why.

        Releasing is a control override in everything but name: it restores
        order flow that a human stopped. It is logged at CRITICAL for the same
        reason the activation is.
        """
        scope = self._validate_kill_switch_scope(scope, key)
        reason = self._require_text(reason, "reason")
        reset_by = self._require_text(reset_by, "reset_by")

        with self._lock:
            state = self._kill_switches.pop((scope, key), None)

        label = scope if key is None else f"{scope}:{key}"
        if state is None:
            logger.warning(
                "HK SFC kill switch reset requested by %s for [%s] but no such switch was engaged.",
                reset_by,
                label,
            )
            return

        logger.critical(
            "HK SFC KILL SWITCH RELEASED [%s] by %s at %s: %s (engaged by %s at %s: %s).",
            label,
            reset_by,
            self._now_iso(),
            reason,
            state.activated_by,
            state.activated_at,
            state.reason,
        )

    def active_kill_switches(self) -> Tuple[KillSwitchState, ...]:
        """Every engaged kill switch, for supervisory display."""
        with self._lock:
            return tuple(self._kill_switches.values())

    # -- Audit trail (Schedule 7 paragraph 1.3) -----------------------------

    @property
    def audit_trail(self) -> Tuple[HkSfcComplianceReport, ...]:
        """In-memory decisions, newest last.

        A reference adapter only. It does not survive a restart and therefore
        cannot satisfy the 2-year retention in paragraph 1.3.2(b); pass
        ``audit_sink`` to write each decision to a durable append-only store.
        """
        with self._lock:
            return tuple(self._audit_trail)

    # -- The gate ------------------------------------------------------------

    def audit_sfc_compliance(self, req: HkSfcOrderRequest) -> HkSfcComplianceReport:
        """Evaluate one order and return the compliance decision.

        Raises ``ValueError`` if the request is structurally invalid -- that is
        a defect in the calling strategy, not a compliance outcome.
        """
        self._validate_request(req)

        is_short = req.is_short_sell or req.side.upper() == SIDE_SHORT_SELL
        violations: List[str] = []

        with self._lock:
            engaged = self._engaged_switches_for(req)
            if engaged:
                violations.append(VIOLATION_KILL_SWITCH_ACTIVE)
            if self._record_message_and_check_rate(req):
                violations.append(VIOLATION_MESSAGE_RATE_LIMIT)

            # Qualification, testing and authorisation (Schedule 7 1.1, 3.1, 3.2).
            if not req.algo_authorised_for_production:
                violations.append(VIOLATION_ALGO_NOT_AUTHORISED)
            if not req.algo_testing_signed_off:
                violations.append(VIOLATION_ALGO_NOT_TESTED)
            if not req.operator_approved_to_use:
                violations.append(VIOLATION_OPERATOR_NOT_APPROVED)

            order_value_dec = self._order_value(req)
            order_value = float(order_value_dec)
            deviation_pct = self._price_deviation_pct(req)

            # Firm-calibrated pre-trade thresholds (Schedule 7 2.1.1(a), 3.3.1).
            if order_value_dec > Decimal(str(self.max_order_value_hkd)):
                violations.append(VIOLATION_ORDER_VALUE_LIMIT)
            if self.max_order_quantity is not None and req.order_quantity > self.max_order_quantity:
                violations.append(VIOLATION_ORDER_QUANTITY_LIMIT)
            if deviation_pct is None:
                violations.append(VIOLATION_MISSING_MARKET_DATA)
            elif deviation_pct > self.max_price_deviation_pct:
                # Compared unrounded: a 5.004% deviation rounded to 2dp reads as
                # exactly 5.00% and would slip past a 5.00% limit.
                violations.append(VIOLATION_PRICE_DEVIATION_LIMIT)

            if self.max_adv_participation_pct is not None:
                if not _usable_positive_number(req.average_daily_volume):
                    if VIOLATION_MISSING_MARKET_DATA not in violations:
                        violations.append(VIOLATION_MISSING_MARKET_DATA)
                else:
                    participation = 100.0 * req.order_quantity / float(req.average_daily_volume)
                    if participation > self.max_adv_participation_pct:
                        violations.append(VIOLATION_ADV_PARTICIPATION_LIMIT)

            violations.extend(self._child_order_violations(req))

            short_sell_violations: List[str] = []
            if is_short:
                short_sell_violations = self._short_sell_violations(req)
                violations.extend(short_sell_violations)

            ordered = tuple(v for v in VIOLATION_PRECEDENCE if v in violations)
            report = self._build_report(
                req=req,
                is_short=is_short,
                order_value=order_value,
                deviation_pct=deviation_pct,
                violations=ordered,
                short_sell_violations=tuple(short_sell_violations),
                engaged=engaged,
            )
            self._audit_trail.append(report)

        self._log_report(report)
        if self._audit_sink is not None:
            self._audit_sink(report)
        return report

    # -- Individual control groups ------------------------------------------

    def _short_sell_violations(self, req: HkSfcOrderRequest) -> List[str]:
        """Cover, assurance, flagging, eligibility, order type and tick rule."""
        violations: List[str] = []

        # SFO section 170 -- never waived here, even for an exempt category.
        # Section 3 of the Short Selling and SBL (Miscellaneous) Rules does
        # disapply section 170(1) for certain market makers, but that is a
        # determination for the firm's legal function, not a flag on an order.
        if not req.has_locate_borrow:
            violations.append(VIOLATION_ILLEGAL_NAKED_SHORT)

        # SFO section 171 -- documentary assurance at the time of the order.
        if not (req.documentary_assurance_ref or "").strip():
            violations.append(VIOLATION_SHORT_SELL_ASSURANCE_MISSING)

        # SFO section 172 / Eleventh Schedule Regulation (5)(b) -- mark short.
        if not req.short_sell_flagged:
            violations.append(VIOLATION_SHORT_SELL_NOT_FLAGGED)

        exempt = req.exempt_short_sell_category is not None

        # Rule 563D(1) -- Designated Securities only, outside the exempt
        # categories.
        if not exempt and not req.is_designated_security:
            violations.append(VIOLATION_SHORT_SELL_NOT_DESIGNATED)

        # Rule 563D(1) -- in POS and CAS only at-auction limit orders may be
        # input as short selling orders.
        if req.session in SHORT_SELL_AUCTION_SESSIONS and req.order_type != ORDER_TYPE_AT_AUCTION_LIMIT:
            violations.append(VIOLATION_SHORT_SELL_ORDER_TYPE_NOT_PERMITTED)

        # Eleventh Schedule Regulation (15) -- the tick rule. Exempt categories
        # and Commission-approved Market Making Securities fall outside it.
        if not exempt:
            if not _usable_positive_number(req.short_sell_reference_price):
                violations.append(VIOLATION_MISSING_MARKET_DATA)
            elif Decimal(str(req.order_price)) < Decimal(str(req.short_sell_reference_price)):
                violations.append(VIOLATION_SHORT_SELL_TICK_RULE)

        return violations

    def _child_order_violations(self, req: HkSfcOrderRequest) -> List[str]:
        """Child orders must not exceed the parent's price or remaining size."""
        violations: List[str] = []
        if req.parent_order_id is None:
            return violations

        if _usable_positive_number(req.parent_limit_price):
            parent_price = Decimal(str(req.parent_limit_price))
            child_price = Decimal(str(req.order_price))
            # A buy child must not bid above the parent limit; a sell or short
            # sell child must not offer below it.
            too_aggressive = (
                child_price > parent_price
                if req.side.upper() == SIDE_BUY
                else child_price < parent_price
            )
            if too_aggressive:
                violations.append(VIOLATION_CHILD_PRICE_EXCEEDS_PARENT)

        if req.parent_remaining_quantity is not None and req.order_quantity > req.parent_remaining_quantity:
            violations.append(VIOLATION_CHILD_QUANTITY_EXCEEDS_PARENT)

        return violations

    def _engaged_switches_for(self, req: HkSfcOrderRequest) -> Tuple[KillSwitchState, ...]:
        """Kill switches whose scope covers this order."""
        keys: List[Tuple[str, Optional[str]]] = [(KILL_SWITCH_SCOPE_FIRM, None)]
        keys.append((KILL_SWITCH_SCOPE_ALGO, req.algo_id))
        if req.client_id is not None:
            keys.append((KILL_SWITCH_SCOPE_CLIENT, req.client_id))
        return tuple(self._kill_switches[k] for k in keys if k in self._kill_switches)

    def _record_message_and_check_rate(self, req: HkSfcOrderRequest) -> bool:
        """Count this submission and report whether it breaches the rate cap.

        Counts every submission presented to the gate, including ones it goes
        on to reject: a rejected order still consumed a message from the
        strategy, and the SFC's concern is "abnormally large volumes of
        messages ... within a given time interval".
        """
        if self.max_messages_per_interval is None:
            return False

        now = self._clock()
        window = self._message_times.setdefault(req.algo_id, deque())
        window.append(now)
        cutoff = now - self.message_interval
        while window and window[0] <= cutoff:
            window.popleft()
        return len(window) > self.max_messages_per_interval

    # -- Metrics -------------------------------------------------------------

    def _order_value(self, req: HkSfcOrderRequest) -> Decimal:
        """Notional in HKD, computed in Decimal so an order sitting exactly on
        the limit is not rejected by binary floating-point drift."""
        return Decimal(str(req.order_price)) * Decimal(req.order_quantity)

    def _price_deviation_pct(self, req: HkSfcOrderRequest) -> Optional[float]:
        """Absolute deviation from the nominal price, in percent.

        ``None`` when no usable nominal price was supplied -- the control could
        not be evaluated, which is reported as ``MISSING_MARKET_DATA`` rather
        than as a deviation of 0.0%.
        """
        if not _usable_positive_number(req.market_last_price):
            logger.error(
                "HK SFC gate [%s/%s]: no usable nominal price supplied (%r); "
                "price band cannot be evaluated and the order is blocked.",
                req.algo_id,
                req.stock_code,
                req.market_last_price,
            )
            return None
        nominal = float(req.market_last_price)
        return abs(req.order_price - nominal) / nominal * 100.0

    # -- Report assembly -----------------------------------------------------

    def _build_report(
        self,
        req: HkSfcOrderRequest,
        is_short: bool,
        order_value: float,
        deviation_pct: Optional[float],
        violations: Tuple[str, ...],
        short_sell_violations: Tuple[str, ...],
        engaged: Tuple[KillSwitchState, ...],
    ) -> HkSfcComplianceReport:
        self._decision_count += 1
        order_reference = req.order_reference or f"{req.algo_id}-{self._decision_count:08d}"
        status = STATUS_APPROVED if not violations else _rejected_status(violations[0])

        deviation_text = "n/a" if deviation_pct is None else f"{deviation_pct:.2f}%"
        if violations:
            notes = (
                f"SFC REJECT [{order_reference}] {req.side} {req.order_quantity} {req.stock_code} "
                f"@ HKD {req.order_price:,.4f} in {req.session} "
                f"(value HKD {order_value:,.2f}, deviation {deviation_text}): "
                + ", ".join(violations)
                + "."
            )
        else:
            notes = (
                f"SFC COMPLIANT APPROVED [{order_reference}] {req.side} {req.order_quantity} "
                f"{req.stock_code} @ HKD {req.order_price:,.4f} in {req.session} "
                f"(value HKD {order_value:,.2f}, deviation {deviation_text})."
            )

        return HkSfcComplianceReport(
            algo_id=req.algo_id,
            stock_code=req.stock_code,
            session=req.session,
            side=req.side,
            order_reference=order_reference,
            decision_time_utc=self._now_iso(),
            order_value_hkd=order_value,
            price_deviation_pct=deviation_pct,
            is_short_sell=is_short,
            is_short_sell_legal=(
                None
                if not is_short
                else not (
                    short_sell_violations
                    or VIOLATION_MISSING_MARKET_DATA in violations
                )
            ),
            is_algo_authorised=(
                req.algo_authorised_for_production
                and req.algo_testing_signed_off
                and req.operator_approved_to_use
            ),
            is_kill_switch_active=bool(engaged),
            kill_switch_scopes=tuple(state.label() for state in engaged),
            status=status,
            violations=violations,
            blocks_order=bool(violations),
            audit_notes=notes,
        )

    def _log_report(self, report: HkSfcComplianceReport) -> None:
        if not report.violations:
            logger.info(report.audit_notes)
        elif any(v in STATUTORY_OR_EXCHANGE_VIOLATIONS for v in report.violations):
            logger.critical(report.audit_notes)
        elif VIOLATION_KILL_SWITCH_ACTIVE in report.violations:
            logger.critical(report.audit_notes)
        else:
            logger.warning(report.audit_notes)

    # -- Validation ----------------------------------------------------------

    def _validate_request(self, req: HkSfcOrderRequest) -> None:
        """Reject structurally invalid orders loudly.

        These are the caller's own fields. A silent pass here is how a
        malformed order reaches the Exchange; market-data gaps are handled
        separately and fail closed.
        """
        if not isinstance(req, HkSfcOrderRequest):
            raise TypeError("req must be an HkSfcOrderRequest")

        self._require_text(req.algo_id, "algo_id")
        self._require_text(req.stock_code, "stock_code")

        side = req.side.upper() if isinstance(req.side, str) else req.side
        if side not in KNOWN_SIDES:
            raise ValueError(f"side {req.side!r} is not one of {sorted(KNOWN_SIDES)}")
        if req.session not in KNOWN_SESSIONS:
            raise ValueError(f"session {req.session!r} is not one of {sorted(KNOWN_SESSIONS)}")
        if req.order_type not in KNOWN_ORDER_TYPES:
            raise ValueError(f"order_type {req.order_type!r} is not one of {sorted(KNOWN_ORDER_TYPES)}")
        if (
            req.exempt_short_sell_category is not None
            and req.exempt_short_sell_category not in KNOWN_EXEMPT_SHORT_SELL_CATEGORIES
        ):
            raise ValueError(
                f"exempt_short_sell_category {req.exempt_short_sell_category!r} is not one of "
                f"{sorted(KNOWN_EXEMPT_SHORT_SELL_CATEGORIES)}"
            )

        if not _is_finite_number(req.order_price) or float(req.order_price) <= 0.0:
            raise ValueError("order_price must be a finite positive number")
        if (
            not isinstance(req.order_quantity, int)
            or isinstance(req.order_quantity, bool)
            or req.order_quantity <= 0
        ):
            raise ValueError("order_quantity must be a positive integer")

        if side == SIDE_BUY and req.is_short_sell:
            raise ValueError("is_short_sell=True is inconsistent with side='BUY'")
        if req.exempt_short_sell_category is not None and not (
            req.is_short_sell or side == SIDE_SHORT_SELL
        ):
            raise ValueError("exempt_short_sell_category is only meaningful on a short sale")

        if req.parent_remaining_quantity is not None and (
            not isinstance(req.parent_remaining_quantity, int)
            or isinstance(req.parent_remaining_quantity, bool)
            or req.parent_remaining_quantity < 0
        ):
            raise ValueError("parent_remaining_quantity must be a non-negative integer or None")

    def _validate_kill_switch_scope(self, scope: str, key: Optional[str]) -> str:
        if scope not in KNOWN_KILL_SWITCH_SCOPES:
            raise ValueError(f"scope {scope!r} is not one of {sorted(KNOWN_KILL_SWITCH_SCOPES)}")
        if scope in KEYED_KILL_SWITCH_SCOPES:
            self._require_text(key, f"key for scope {scope}")
        elif key is not None:
            raise ValueError(f"scope {scope} does not take a key")
        return scope

    @staticmethod
    def _require_text(value: Optional[str], name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    def _now_iso(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat()
