"""SEBI / Indian exchange algorithmic-order tagging and OTR compliance gate.

Evaluates a single algorithmic order against the Indian requirements that a stock
broker's system must satisfy *before* the order reaches an exchange, and produces
an auditable record of the decision.

Regulatory anchors (full citations in ``references/standards.md``):

* **SEBI** is the **Securities and Exchange Board of India**. (Not "Securities and
  Futures Board of India" -- there is no such body.)

* **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 February 2025** --
  *Safer participation of retail investors in Algorithmic trading*. The current
  retail-algo framework.

  - **para 5.I(b)** -- "All algo orders originating/flowing through Application
    Programming Interface (API) extended by brokers to algo providers, shall be
    tagged with a unique identifier provided by Stock Exchange."
  - **para 5.I(c)** -- algos written by tech-savvy retail investors themselves
    "shall also be registered with the Exchange, through their broker, **only if
    they cross the specified order per second threshold**". Footnote 2 is
    important: that threshold "shall be evolved by the Broker's Industry
    Standards Forum, under the aegis of the stock exchanges and in consultation
    with SEBI" -- **the number 10 is not in the SEBI circular.** It comes from
    the exchange implementation standards below.
  - **para 5.I(d)** -- no open APIs; access "only through a unique vendor client
    specific API key and static IP whitelisted by the broker"; OAuth-only
    authentication; two-factor authentication; empanelled algo providers only.
  - **para 5.II(a), (b)** -- algo trading offered "only after obtaining requisite
    permission of the stock exchange for each algo"; all algo orders tagged with
    an Exchange-provided unique identifier "in order to establish audit trail",
    with Exchange approval sought for any modification to an approved algo.
  - **para 5.V** -- white box (execution / disclosed logic) versus black box
    algos; a black box algo provider must register as a Research Analyst.
  - **para 7(b)** -- applicable with effect from 1 August 2025, later re-phased
    (see ``references/standards.md``); full applicability for all brokers from
    1 April 2026.

* **NSE/INVG/67858, 5 May 2025** -- *Implementation Standards for safer
  participation of retail investors in Algorithmic trading*, issued under
  para 7(a) of the SEBI circular. This is where the operative numbers live:

  - **Annexure B.2 / F** -- "The Threshold Order Per Second (TOPS) is initially
    set at not exceeding 10 orders per second per exchange/segment and may be
    adjusted by the stock exchanges as needed after due notice to the market."
    Below TOPS the client need not register the algo. "The threshold will be
    applied basis the calendar clock second of the broker server." A broker may
    set a *lower* client-level limit, "not exceeding the current prescribed
    Threshold Order Per Second".
  - **Annexure B.3** -- below-threshold algo orders are still tagged: "a generic
    algo ID shall be provided by the Exchange for such Algos".
  - **Annexure B.5** -- "If the broker receives orders that exceed the Threshold
    OPS limit, the broker shall reject/not accept/not process any orders
    exceeding the OPS limit, in accordance with their policy." *This* is the
    control this module implements, not a warning.
  - **Annexure C.1** -- to place orders faster than TOPS the client "must
    register their algorithm with each Exchange where the algorithm is intended
    to be used", and the orders are then tagged with the exchange-provided
    algorithm ID (C.2).
  - **Annexure A.1, A.5** -- static IP is mandatory for API access, for
    client-generated algos (the client's IP), for empanelled-provider algos (the
    vendor's or the client's) and for broker-generated algos (the broker's or
    the client's).
  - **Annexure G** -- "All algo orders (Below and above the threshold) shall be
    tagged with a unique identifier provided by the Exchange in order to
    establish audit trail."
  - **Annexure J.1** -- "These standards do not apply to trading under Direct
    Market Access (DMA), which will remain governed by the relevant provisions."

* **Order-to-Trade Ratio.** The OTR penalty framework is a *separate*, older
  regime from algo tagging, and it binds the **trading member**, not the order:

  - **CIR/MRD/DP/09/2012 (30 Mar 2012)** and **CIR/MRD/DP/16/2013 (21 May 2013)**
    -- exchanges operate "a framework of economic disincentives for high daily
    order-to-trade ratio of orders placed from trading algorithms".
  - **SEBI/HO/MRD/DP/CIR/P/2018/62 (9 Apr 2018), para 14** -- "orders placed
    within +/-0.75% of the LTP shall be exempted from the framework for imposing
    penalty for high OTR"; the framework was extended to the cash segment.
  - **SEBI/HO/MRD1/DSAP/CIR/P/2020/107 (24 Jun 2020)** -- exchanges may add slabs
    up to an OTR of 2000 (from the then-existing 500). "On the third instance of
    OTR being 2000 or more, in last 30 days (rolling basis), the concerned
    member shall not be permitted to place any orders for the first 15 minutes
    on the next trading day." **A single day at 2000 is a charge, not a
    suspension.**
  - **HO/47/11/16(2)2025-MRD-POD2/I/4113/2026 (4 Feb 2026), effective 6 Apr 2026**
    -- algo orders by Designated Market Makers for market making are excluded
    from OTR computation, and an equity-option premium band was added to the
    exemptions.

  Because *which* messages count is set by these exemptions, this module does not
  guess: the caller passes the exempt message count explicitly.

* **Algo market orders are prohibited.** NSE/SURV/55281 (17 Jan 2023) reiterated
  the Market Price Protection check -- algorithmic orders must not be placed as
  market orders -- and NSE/CMTR/68802 (30 Jun 2025, effective 7 Jul 2025)
  extended pre-emptive exchange rejection of algo market orders to the capital
  market segment, identifying an algo "as per the 13th digit of the 15-digit NNF
  field in the order structure". In the commodity segment IOC orders are barred
  as well (NSE/MSD/67753, 29 Apr 2025, para 8.1.2.1).

Three deliberate error-handling rules run through the module:

* **The caller's own order fields raise.** An unknown exchange, segment, order
  source, order type or tag kind, a blank symbol, a non-positive quantity, a
  non-finite price or a negative counter is a defect in the calling strategy, not
  a compliance decision. ``ValueError`` is loud; a silent pass is how a malformed
  order reaches the exchange.
* **An unevaluable control fails closed.** An OTR that cannot be computed because
  nothing traded is ``OTR_UNDEFINED_NO_TRADES`` and is escalated for review -- it
  is never reported as a low ratio.
* **Every control is evaluated and every breach recorded**, even after the
  headline rejection is decided, so the audit record says what was actually
  stopped rather than only the first thing noticed.

This module is a decision and evidence engine, not a trading system. It does not
throttle order flow, does not query exchange registration, and holds no state --
the rolling 30-day OTR instance count is supplied by the caller from the firm's
own durable records.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Exchanges in scope -----------------------------------------------------
# The retail-algo implementation standards are issued by each recognised stock
# exchange. NSE's are quoted throughout; BSE and MCX issued matching standards.
EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"
EXCHANGE_MCX = "MCX"
VALID_EXCHANGES: FrozenSet[str] = frozenset({EXCHANGE_NSE, EXCHANGE_BSE, EXCHANGE_MCX})

# --- Segments ---------------------------------------------------------------
SEGMENT_CM = "CM"
"""Capital market (cash equities)."""
SEGMENT_FO = "FO"
"""Equity derivatives (futures and options)."""
SEGMENT_CD = "CD"
"""Currency derivatives."""
SEGMENT_COM = "COM"
"""Commodity derivatives."""
VALID_SEGMENTS: FrozenSet[str] = frozenset(
    {SEGMENT_CM, SEGMENT_FO, SEGMENT_CD, SEGMENT_COM}
)

# --- Order origination channel ---------------------------------------------
# Determines whose static IP must be whitelisted (NSE/INVG/67858 Annexure A.5)
# and whether the retail-algo standards apply at all (Annexure J.1).
SOURCE_CLIENT_API = "CLIENT_API"
"""Tech-savvy client's own algo over the broker's client API."""
SOURCE_VENDOR_API = "VENDOR_API"
"""Empanelled algo provider over a vendor API key."""
SOURCE_BROKER_ALGO = "BROKER_ALGO"
"""Broker-generated algo (NSE/INVG/67858 Annexure D)."""
SOURCE_IBT_STWT = "IBT_STWT"
"""Internet Based Trading / wireless member front-end. Per the NSE retail-algo
FAQ (3 Nov 2025, Q3 and Q6), a client static IP is required *only* for a
tech-savvy investor using an API, so this channel does not carry that gate."""
SOURCE_DMA = "DMA"
"""Direct Market Access. Expressly outside these standards (Annexure J.1)."""
VALID_ORDER_SOURCES: FrozenSet[str] = frozenset(
    {
        SOURCE_CLIENT_API,
        SOURCE_VENDOR_API,
        SOURCE_BROKER_ALGO,
        SOURCE_IBT_STWT,
        SOURCE_DMA,
    }
)
_STATIC_IP_REQUIRED_SOURCES: FrozenSet[str] = frozenset(
    {SOURCE_CLIENT_API, SOURCE_VENDOR_API, SOURCE_BROKER_ALGO}
)

# --- Algo tag kind ----------------------------------------------------------
TAG_REGISTERED = "REGISTERED"
"""An algo registered with the exchange, carrying its own exchange algo ID
(NSE/INVG/67858 Annexure C.2, D.1, E.2)."""
TAG_GENERIC = "GENERIC"
"""The standardised / generic exchange-provided tag used for sub-threshold algo
flow that needs no registration (Annexure B.3)."""
VALID_TAG_KINDS: FrozenSet[str] = frozenset({TAG_REGISTERED, TAG_GENERIC})

# --- Client account category ------------------------------------------------
# NOTE: PRO/CLI is an exchange order attribute distinguishing a member's
# proprietary account from a client account. It is a long-standing requirement
# in its own right and is *not* imposed by the algo-tagging circulars; it is
# checked here because an algo order still has to carry it correctly.
CATEGORY_PRO = "PRO"
CATEGORY_CLI = "CLI"
VALID_CLIENT_CATEGORIES: FrozenSet[str] = frozenset({CATEGORY_PRO, CATEGORY_CLI})

# --- Order types ------------------------------------------------------------
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_IOC = "IOC"
ORDER_TYPE_STOP_LOSS = "STOP_LOSS"
VALID_ORDER_TYPES: FrozenSet[str] = frozenset(
    {ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET, ORDER_TYPE_IOC, ORDER_TYPE_STOP_LOSS}
)

VALID_SIDES: FrozenSet[str] = frozenset({"BUY", "SELL"})

# --- Defaults ---------------------------------------------------------------
DEFAULT_THRESHOLD_OPS = 10.0
"""Threshold Order Per Second. NSE/INVG/67858 Annexure B.2 and F: "initially set
at not exceeding 10 orders per second per exchange/segment and may be adjusted by
the stock exchanges as needed after due notice to the market." Confirm the
current value with the exchange before relying on it."""

DEFAULT_OTR_PENALTY_SLAB_FLOOR = 500.0
"""The OTR level that was the top penalty slab before SEBI/HO/MRD1/DSAP/CIR/P/
2020/107 permitted exchanges to add slabs up to 2000. Reaching it means a
per-algo-order charge, not a suspension. Actual slab rates and boundaries are set
by each exchange -- check the exchange's own penalty circular."""

DEFAULT_OTR_COOLING_OFF_LEVEL = 2000.0
"""The OTR level referenced by the cooling-off rule in SEBI/HO/MRD1/DSAP/CIR/P/
2020/107."""

DEFAULT_COOLING_OFF_INSTANCE_COUNT = 3
"""SEBI/HO/MRD1/DSAP/CIR/P/2020/107: the suspension bites "on the third instance
of OTR being 2000 or more, in last 30 days (rolling basis)"."""

DEFAULT_COOLING_OFF_LOOKBACK_DAYS = 30
"""Rolling window for the instance count, per the same circular. Recorded on the
report for the audit trail; the caller owns the window bookkeeping."""

DEFAULT_ALGO_NNF_DIGITS: FrozenSet[str] = frozenset({"0", "2", "4"})
"""Accepted values of the 13th digit of the 15-digit NNF field for an algo order.
NSE identifies an algo order by that digit (NSE/CMTR/68802, 30 Jun 2025), and the
NSE retail-algo FAQ of 3 November 2025 (Q7) gives "0", "2" or "4" as the 13th
digit for retail algo orders sent through a client direct API or a member
front-end. Verify against the current NNF/order-structure protocol for your
segment before relying on this set -- pass your own via ``algo_nnf_digits``."""

NNF_ID_LENGTH = 15
"""The NNF field is 15 digits; the 13th identifies algo versus non-algo."""

# --- OTR status codes -------------------------------------------------------
OTR_NORMAL = "OTR_NORMAL"
OTR_PENALTY_SLAB = "OTR_PENALTY_SLAB"
OTR_COOLING_OFF_LEVEL_REACHED = "OTR_COOLING_OFF_LEVEL_REACHED"
OTR_COOLING_OFF_TRIGGERED = "OTR_COOLING_OFF_TRIGGERED"
OTR_UNDEFINED_NO_TRADES = "OTR_UNDEFINED_NO_TRADES"

# --- Order decision status codes -------------------------------------------
STATUS_APPROVED = "SEBI_TAGGING_APPROVED"
STATUS_UNTAGGED = "REJECTED_UNTAGGED_ALGO"
STATUS_NNF_NOT_ALGO = "REJECTED_NNF_TAG_NOT_ALGO"
STATUS_UNREGISTERED = "REJECTED_UNREGISTERED_ALGO"
STATUS_OPS_BREACH = "REJECTED_OPS_THRESHOLD_BREACH"
STATUS_MARKET_ORDER = "REJECTED_ALGO_MARKET_ORDER"
STATUS_RESTRICTED_ORDER_TYPE = "REJECTED_RESTRICTED_ORDER_TYPE"
STATUS_STATIC_IP = "REJECTED_STATIC_IP_NOT_WHITELISTED"
STATUS_INVALID_CATEGORY = "REJECTED_INVALID_CATEGORY"
STATUS_OUT_OF_SCOPE_DMA = "OUT_OF_SCOPE_DMA"

# --- Violation codes --------------------------------------------------------
V_UNTAGGED = "UNTAGGED_ALGO_ORDER"
V_NNF_NOT_ALGO = "NNF_13TH_DIGIT_NOT_ALGO"
V_NNF_MALFORMED = "NNF_ID_MALFORMED"
V_UNREGISTERED = "ALGO_NOT_REGISTERED_WITH_EXCHANGE"
V_OPS_BREACH = "OPS_ABOVE_THRESHOLD_WITHOUT_REGISTRATION"
V_OPS_AT_BOUNDARY = "OPS_EXACTLY_AT_THRESHOLD_BOUNDARY"
V_MARKET_ORDER = "ALGO_MARKET_ORDER_PROHIBITED"
V_RESTRICTED_ORDER_TYPE = "ORDER_TYPE_RESTRICTED_IN_SEGMENT"
V_STATIC_IP = "STATIC_IP_NOT_WHITELISTED"
V_INVALID_CATEGORY = "INVALID_CLIENT_CATEGORY"
V_OTR_SLAB = "OTR_PENALTY_SLAB_REACHED"
V_OTR_COOLING_OFF_LEVEL = "OTR_AT_COOLING_OFF_LEVEL"
V_OTR_COOLING_OFF_TRIGGERED = "OTR_COOLING_OFF_SUSPENSION_TRIGGERED"
V_OTR_UNDEFINED = "OTR_UNDEFINED_NO_TRADES"

# Most severe first. The headline ``status`` is the first of these present.
_STATUS_PRECEDENCE: Tuple[Tuple[str, str], ...] = (
    (V_UNTAGGED, STATUS_UNTAGGED),
    (V_NNF_MALFORMED, STATUS_NNF_NOT_ALGO),
    (V_NNF_NOT_ALGO, STATUS_NNF_NOT_ALGO),
    (V_UNREGISTERED, STATUS_UNREGISTERED),
    (V_OPS_BREACH, STATUS_OPS_BREACH),
    (V_MARKET_ORDER, STATUS_MARKET_ORDER),
    (V_RESTRICTED_ORDER_TYPE, STATUS_RESTRICTED_ORDER_TYPE),
    (V_STATIC_IP, STATUS_STATIC_IP),
    (V_INVALID_CATEGORY, STATUS_INVALID_CATEGORY),
)


def _require_token(value: str, valid: FrozenSet[str], label: str) -> str:
    """Normalise and validate an enumerated order field, or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string, got {type(value).__name__}")
    token = value.strip().upper()
    if token not in valid:
        raise ValueError(
            f"{label} {value!r} is not one of {sorted(valid)}"
        )
    return token


@dataclass(frozen=True)
class SebiAlgoOrderPayload:
    """One algorithmic order presented for pre-trade compliance evaluation.

    ``algo_id`` is the identifier issued by the exchange -- either a registered
    algo's own ID or the generic/standardised tag for sub-threshold flow. It is
    never something the broker or client invents (SEBI 2025 circular para
    5.II(b); NSE/INVG/67858 Annexure G).
    """

    algo_id: str
    client_category: str
    symbol: str
    exchange: str
    side: str
    price: float
    quantity: int
    algo_tag_kind: str = TAG_REGISTERED
    segment: str = SEGMENT_CM
    order_source: str = SOURCE_CLIENT_API
    order_type: str = ORDER_TYPE_LIMIT
    is_registered_with_exchange: bool = True
    orders_per_second_ops: float = 1.0
    static_ip_whitelisted: bool = True
    nnf_id: Optional[str] = None
    """Optional 15-digit NNF field. When supplied, its 13th digit is checked
    against the algo digit set; when ``None`` the check is simply not performed
    and no claim is made about it."""


@dataclass(frozen=True)
class SebiOtrMetrics:
    """Daily Order-to-Trade Ratio inputs for one trading member and segment.

    The OTR framework operates at **trading member** level, per segment, per
    trading day -- not per order and not per algo. These figures therefore
    describe the member's whole day, and the same metrics apply to every order
    audited for that member/segment/day.

    ``exempt_order_messages`` is the count of algo order messages that do **not**
    count towards the penalty framework: orders placed within +/-0.75% of the LTP
    (SEBI/HO/MRD/DP/CIR/P/2018/62 para 14), Designated Market Maker market-making
    orders and the equity-option premium band (SEBI circular of 4 Feb 2026,
    effective 6 Apr 2026), and orders the exchange rejected outright. Leaving it
    at 0 computes a deliberately conservative upper bound on the OTR, not the
    figure the exchange will bill.

    ``prior_cooling_off_instances_30d`` is how many **earlier** days inside the
    rolling 30-day window already recorded an OTR at or above the cooling-off
    level. The engine adds today's instance itself; it keeps no state.
    """

    total_order_messages: int
    """Algo order messages -- submissions, modifications and cancellations."""
    total_executed_trades: int
    exempt_order_messages: int = 0
    prior_cooling_off_instances_30d: int = 0

    def __post_init__(self) -> None:
        if self.total_order_messages < 0:
            raise ValueError("total_order_messages must be >= 0")
        if self.total_executed_trades < 0:
            raise ValueError("total_executed_trades must be >= 0")
        if self.exempt_order_messages < 0:
            raise ValueError("exempt_order_messages must be >= 0")
        if self.exempt_order_messages > self.total_order_messages:
            raise ValueError(
                "exempt_order_messages cannot exceed total_order_messages "
                f"({self.exempt_order_messages} > {self.total_order_messages})"
            )
        if self.prior_cooling_off_instances_30d < 0:
            raise ValueError("prior_cooling_off_instances_30d must be >= 0")

    @property
    def chargeable_order_messages(self) -> int:
        """Order messages that count towards the OTR penalty framework."""
        return self.total_order_messages - self.exempt_order_messages


@dataclass(frozen=True)
class SebiAlgoTaggingReport:
    """Auditable outcome of one order evaluation.

    ``calculated_otr_ratio`` is ``None`` when the ratio is undefined because no
    algo trade was executed. It is never a stand-in count.
    """

    algo_id: str
    algo_tag_kind: str
    client_category: str
    exchange: str
    segment: str
    order_source: str
    is_algo_id_valid: bool
    is_category_valid: bool
    is_ops_within_threshold: bool
    is_order_type_permitted: bool
    is_static_ip_compliant: bool
    threshold_ops: float
    chargeable_order_messages: int
    calculated_otr_ratio: Optional[float]
    otr_status: str
    otr_cooling_off_instances_30d: int
    otr_cooling_off_lookback_days: int
    status: str
    blocks_order: bool
    violations: Tuple[str, ...] = field(default_factory=tuple)
    audit_notes: str = ""


class SebiAlgoTaggingEngine:
    """Pre-trade gate for SEBI / Indian exchange algorithmic-order requirements.

    Enforces exchange-provided algo-ID tagging, the exchange Threshold Order Per
    Second, PRO/CLI account tagging, static-IP whitelisting and the prohibition
    on algo market orders, and classifies the trading member's daily
    Order-to-Trade Ratio against the penalty and cooling-off framework.

    Every threshold is a constructor parameter because every one of them is set
    by an exchange and can be revised after notice to the market. Do not cite
    this class as authority for a default it merely ships with.
    """

    def __init__(
        self,
        threshold_ops: float = DEFAULT_THRESHOLD_OPS,
        otr_penalty_slab_floor: float = DEFAULT_OTR_PENALTY_SLAB_FLOOR,
        otr_cooling_off_level: float = DEFAULT_OTR_COOLING_OFF_LEVEL,
        cooling_off_instance_count: int = DEFAULT_COOLING_OFF_INSTANCE_COUNT,
        cooling_off_lookback_days: int = DEFAULT_COOLING_OFF_LOOKBACK_DAYS,
        algo_nnf_digits: FrozenSet[str] = DEFAULT_ALGO_NNF_DIGITS,
    ) -> None:
        if not math.isfinite(threshold_ops) or threshold_ops <= 0:
            raise ValueError("threshold_ops must be a finite positive number")
        if not math.isfinite(otr_penalty_slab_floor) or otr_penalty_slab_floor <= 0:
            raise ValueError("otr_penalty_slab_floor must be a finite positive number")
        if not math.isfinite(otr_cooling_off_level) or otr_cooling_off_level <= 0:
            raise ValueError("otr_cooling_off_level must be a finite positive number")
        if otr_cooling_off_level < otr_penalty_slab_floor:
            raise ValueError(
                "otr_cooling_off_level must be >= otr_penalty_slab_floor "
                f"({otr_cooling_off_level} < {otr_penalty_slab_floor})"
            )
        if cooling_off_instance_count < 1:
            raise ValueError("cooling_off_instance_count must be >= 1")
        if cooling_off_lookback_days < 1:
            raise ValueError("cooling_off_lookback_days must be >= 1")
        if not algo_nnf_digits:
            raise ValueError("algo_nnf_digits must not be empty")

        self.threshold_ops = float(threshold_ops)
        self.otr_penalty_slab_floor = float(otr_penalty_slab_floor)
        self.otr_cooling_off_level = float(otr_cooling_off_level)
        self.cooling_off_instance_count = int(cooling_off_instance_count)
        self.cooling_off_lookback_days = int(cooling_off_lookback_days)
        self.algo_nnf_digits = frozenset(algo_nnf_digits)

    # ------------------------------------------------------------------
    # Order-to-Trade Ratio
    # ------------------------------------------------------------------
    @staticmethod
    def _exact_otr(metrics: SebiOtrMetrics) -> Optional[float]:
        """Unrounded OTR, or ``None`` when no algo trade was executed.

        Thresholds are tested against *this* value, never against the rounded
        one: rounding first turns a true ratio of 1999.999 into "2000.00" and
        manufactures an instance towards a cooling-off suspension that never
        actually occurred.
        """
        if metrics.total_executed_trades <= 0:
            return None
        return metrics.chargeable_order_messages / float(metrics.total_executed_trades)

    def calculate_otr(self, metrics: SebiOtrMetrics) -> Optional[float]:
        """Daily OTR = chargeable algo order messages / executed algo trades.

        Rounded to two decimal places for reporting. Returns ``None`` when no
        algo trade was executed, because the ratio is then genuinely undefined.
        It deliberately does *not* fall back to the message count: 400 messages
        and no trades is not "an OTR of 400", and reporting it as one would place
        the worst possible day below the penalty slab floor. Callers must handle
        ``None`` -- see :meth:`classify_otr`, which escalates it.
        """
        exact = self._exact_otr(metrics)
        return None if exact is None else round(exact, 2)

    def classify_otr(self, metrics: SebiOtrMetrics) -> Tuple[Optional[float], str, int]:
        """Classify the member's day against the OTR framework.

        Returns ``(reported_otr, otr_status, cooling_off_instances_in_window)``,
        where ``reported_otr`` is rounded for display but every threshold test
        was made against the unrounded ratio.

        The cooling-off suspension does **not** follow from a single day at the
        cooling-off level. Per SEBI/HO/MRD1/DSAP/CIR/P/2020/107 it bites on the
        third such instance within the rolling 30-day window, so a day at or
        above the level is reported as ``OTR_COOLING_OFF_LEVEL_REACHED`` until
        the instance count reaches the configured threshold.
        """
        exact = self._exact_otr(metrics)
        instances = metrics.prior_cooling_off_instances_30d

        if exact is None:
            return None, OTR_UNDEFINED_NO_TRADES, instances

        reported = round(exact, 2)

        if exact >= self.otr_cooling_off_level:
            instances += 1
            if instances >= self.cooling_off_instance_count:
                return reported, OTR_COOLING_OFF_TRIGGERED, instances
            return reported, OTR_COOLING_OFF_LEVEL_REACHED, instances

        if exact >= self.otr_penalty_slab_floor:
            return reported, OTR_PENALTY_SLAB, instances

        return reported, OTR_NORMAL, instances

    # ------------------------------------------------------------------
    # Order audit
    # ------------------------------------------------------------------
    def audit_sebi_algo_order(
        self,
        payload: SebiAlgoOrderPayload,
        otr_metrics: SebiOtrMetrics,
    ) -> SebiAlgoTaggingReport:
        """Evaluate one algo order and the member's daily OTR.

        Raises ``ValueError`` for a structurally invalid payload -- that is a
        defect in the calling strategy, not a compliance outcome. Compliance
        breaches are returned in the report.

        Every control is evaluated even once a rejection is certain, so
        ``violations`` lists all of them and the OTR figures are populated on
        rejected orders too. ``status`` is the most severe violation by the
        precedence in ``_STATUS_PRECEDENCE``.
        """
        exchange = _require_token(payload.exchange, VALID_EXCHANGES, "exchange")
        segment = _require_token(payload.segment, VALID_SEGMENTS, "segment")
        source = _require_token(payload.order_source, VALID_ORDER_SOURCES, "order_source")
        order_type = _require_token(payload.order_type, VALID_ORDER_TYPES, "order_type")
        tag_kind = _require_token(payload.algo_tag_kind, VALID_TAG_KINDS, "algo_tag_kind")
        _require_token(payload.side, VALID_SIDES, "side")

        if not isinstance(payload.symbol, str) or not payload.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(payload.quantity, int) or isinstance(payload.quantity, bool):
            raise ValueError("quantity must be an int")
        if payload.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {payload.quantity}")
        if not math.isfinite(payload.price) or payload.price < 0:
            raise ValueError(f"price must be finite and non-negative, got {payload.price}")
        if not math.isfinite(payload.orders_per_second_ops) or payload.orders_per_second_ops < 0:
            raise ValueError(
                "orders_per_second_ops must be finite and non-negative, "
                f"got {payload.orders_per_second_ops}"
            )

        algo_id = payload.algo_id.strip() if isinstance(payload.algo_id, str) else ""
        category = (
            payload.client_category.strip().upper()
            if isinstance(payload.client_category, str)
            else ""
        )

        # Direct Market Access is expressly carved out of the retail-algo
        # implementation standards (NSE/INVG/67858 Annexure J.1). Returning a
        # determination rather than an approval keeps the carve-out auditable and
        # stops this gate from being mistaken for DMA compliance.
        if source == SOURCE_DMA:
            # The OTR framework is older than, and independent of, these
            # standards -- it binds "the concerned member" with no DMA carve-out
            # -- so the ratio is still computed and reported honestly. Only the
            # tagging determination is withheld.
            dma_otr, dma_otr_status, dma_instances = self.classify_otr(otr_metrics)
            notes = (
                "OUT OF SCOPE: order source is DMA. The retail-algo implementation "
                "standards (NSE/INVG/67858 Annexure J.1) expressly do not apply to "
                "Direct Market Access, which remains governed by the DMA provisions. "
                "This gate has made no tagging determination for this order; the "
                "member-level OTR below is reported because that framework has no "
                "DMA carve-out."
            )
            logger.warning(notes)
            return SebiAlgoTaggingReport(
                algo_id=algo_id,
                algo_tag_kind=tag_kind,
                client_category=category,
                exchange=exchange,
                segment=segment,
                order_source=source,
                # False here means "not established by this gate", not "failed".
                is_algo_id_valid=False,
                is_category_valid=False,
                is_ops_within_threshold=False,
                is_order_type_permitted=False,
                is_static_ip_compliant=False,
                threshold_ops=self.threshold_ops,
                chargeable_order_messages=otr_metrics.chargeable_order_messages,
                calculated_otr_ratio=dma_otr,
                otr_status=dma_otr_status,
                otr_cooling_off_instances_30d=dma_instances,
                otr_cooling_off_lookback_days=self.cooling_off_lookback_days,
                status=STATUS_OUT_OF_SCOPE_DMA,
                blocks_order=False,
                violations=(),
                audit_notes=notes,
            )

        violations: List[str] = []

        # 1. Exchange-provided algo ID. SEBI 2025 circular para 5.II(b) and
        #    NSE/INVG/67858 Annexure G: ALL algo orders, below and above the
        #    threshold, carry an exchange-provided unique identifier.
        is_algo_id_valid = bool(algo_id)
        if not is_algo_id_valid:
            violations.append(V_UNTAGGED)

        # 2. NNF 13th digit, when the caller supplied one. NSE identifies an algo
        #    order by that digit (NSE/CMTR/68802).
        if payload.nnf_id is not None:
            nnf = payload.nnf_id.strip() if isinstance(payload.nnf_id, str) else ""
            if len(nnf) != NNF_ID_LENGTH or not nnf.isdigit():
                violations.append(V_NNF_MALFORMED)
            elif nnf[12] not in self.algo_nnf_digits:
                violations.append(V_NNF_NOT_ALGO)

        # 3. Registration. A REGISTERED tag asserts an exchange registration;
        #    if the firm cannot stand behind that assertion the order is not
        #    tagged with what it claims to be.
        if tag_kind == TAG_REGISTERED and not payload.is_registered_with_exchange:
            violations.append(V_UNREGISTERED)

        # 4. Threshold Order Per Second. The rejection duty in NSE/INVG/67858
        #    Annexure B.5 sits in section B, "Standards around APIs *without
        #    registering algo*" -- it is the consequence of running unregistered
        #    flow above the threshold, so it does not gate a registered algo.
        #
        #    Boundary note: Annexure B.2/F say the threshold is "not exceeding 10
        #    orders per second" and that flow "below" it needs no registration.
        #    Those two wordings differ at exactly 10 OPS, and the source does not
        #    resolve it. The rejection duty is worded "exceed"/"exceeding", so
        #    the gate rejects strictly above the threshold and flags the exact
        #    boundary rather than silently picking a reading.
        is_ops_within_threshold = True
        if tag_kind == TAG_GENERIC:
            if payload.orders_per_second_ops > self.threshold_ops:
                is_ops_within_threshold = False
                violations.append(V_OPS_BREACH)
            elif payload.orders_per_second_ops == self.threshold_ops:
                violations.append(V_OPS_AT_BOUNDARY)

        # 5. Order type. Algo orders must not be market orders (NSE/SURV/55281;
        #    pre-emptive exchange rejection per NSE/CMTR/68802). In the commodity
        #    segment IOC orders are barred as well (NSE/MSD/67753 para 8.1.2.1).
        is_order_type_permitted = True
        if order_type == ORDER_TYPE_MARKET:
            is_order_type_permitted = False
            violations.append(V_MARKET_ORDER)
        elif order_type == ORDER_TYPE_IOC and segment == SEGMENT_COM:
            is_order_type_permitted = False
            violations.append(V_RESTRICTED_ORDER_TYPE)

        # 6. Static IP whitelisting (SEBI 2025 circular para 5.I(d);
        #    NSE/INVG/67858 Annexure A.1 and A.5).
        is_static_ip_compliant = True
        if source in _STATIC_IP_REQUIRED_SOURCES and not payload.static_ip_whitelisted:
            is_static_ip_compliant = False
            violations.append(V_STATIC_IP)

        # 7. PRO/CLI account category -- an exchange order attribute, checked
        #    here because an algo order still has to carry it correctly.
        is_category_valid = category in VALID_CLIENT_CATEGORIES
        if not is_category_valid:
            violations.append(V_INVALID_CATEGORY)

        # 8. Member-level daily OTR. Computed for approved and rejected orders
        #    alike -- a blocked order whose report shows a zeroed OTR no longer
        #    says what was actually stopped.
        otr, otr_status, instances = self.classify_otr(otr_metrics)
        if otr_status == OTR_COOLING_OFF_TRIGGERED:
            violations.append(V_OTR_COOLING_OFF_TRIGGERED)
        elif otr_status == OTR_COOLING_OFF_LEVEL_REACHED:
            violations.append(V_OTR_COOLING_OFF_LEVEL)
        elif otr_status == OTR_PENALTY_SLAB:
            violations.append(V_OTR_SLAB)
        elif otr_status == OTR_UNDEFINED_NO_TRADES and otr_metrics.total_order_messages > 0:
            violations.append(V_OTR_UNDEFINED)

        status = STATUS_APPROVED
        for violation_code, mapped_status in _STATUS_PRECEDENCE:
            if violation_code in violations:
                status = mapped_status
                break

        blocks_order = status != STATUS_APPROVED
        notes = self._build_notes(
            status=status,
            algo_id=algo_id,
            tag_kind=tag_kind,
            category=category,
            exchange=exchange,
            segment=segment,
            otr=otr,
            otr_status=otr_status,
            instances=instances,
            violations=violations,
        )

        if blocks_order or otr_status == OTR_COOLING_OFF_TRIGGERED:
            logger.critical(notes)
        elif otr_status in (OTR_COOLING_OFF_LEVEL_REACHED, OTR_PENALTY_SLAB, OTR_UNDEFINED_NO_TRADES):
            logger.warning(notes)
        else:
            logger.info(notes)

        return SebiAlgoTaggingReport(
            algo_id=algo_id,
            algo_tag_kind=tag_kind,
            client_category=category,
            exchange=exchange,
            segment=segment,
            order_source=source,
            is_algo_id_valid=is_algo_id_valid,
            is_category_valid=is_category_valid,
            is_ops_within_threshold=is_ops_within_threshold,
            is_order_type_permitted=is_order_type_permitted,
            is_static_ip_compliant=is_static_ip_compliant,
            threshold_ops=self.threshold_ops,
            chargeable_order_messages=otr_metrics.chargeable_order_messages,
            calculated_otr_ratio=otr,
            otr_status=otr_status,
            otr_cooling_off_instances_30d=instances,
            otr_cooling_off_lookback_days=self.cooling_off_lookback_days,
            status=status,
            blocks_order=blocks_order,
            violations=tuple(violations),
            audit_notes=notes,
        )

    # ------------------------------------------------------------------
    def _build_notes(
        self,
        *,
        status: str,
        algo_id: str,
        tag_kind: str,
        category: str,
        exchange: str,
        segment: str,
        otr: Optional[float],
        otr_status: str,
        instances: int,
        violations: List[str],
    ) -> str:
        """Compose the human-readable audit line for the report."""
        label = algo_id or "<untagged>"
        otr_text = "undefined (no algo trades)" if otr is None else f"{otr:,.2f}"

        if status == STATUS_APPROVED:
            head = (
                f"SEBI ALGO TAGGING APPROVED [{label} / {tag_kind} / {category}]: "
                f"{exchange} {segment}."
            )
        else:
            head = (
                f"SEBI ALGO ORDER BLOCKED [{label} / {tag_kind}]: {status} on "
                f"{exchange} {segment}."
            )

        otr_text = f" Member daily OTR = {otr_text} ({otr_status})."
        if otr_status == OTR_COOLING_OFF_TRIGGERED:
            otr_text += (
                f" Instance {instances} of >= {self.otr_cooling_off_level:,.0f} within the "
                f"rolling {self.cooling_off_lookback_days}-day window: cooling-off applies "
                "-- no orders for the first 15 minutes of the next trading day."
            )
        elif otr_status == OTR_COOLING_OFF_LEVEL_REACHED:
            otr_text += (
                f" Instance {instances} of {self.cooling_off_instance_count} within the "
                f"rolling {self.cooling_off_lookback_days}-day window; a charge applies but "
                "no suspension yet."
            )
        elif otr_status == OTR_UNDEFINED_NO_TRADES:
            otr_text += (
                " Order messages with no executed algo trade are also assessed under the "
                "exchange's separate low-trade-count / quote-stuffing penalties."
            )

        breach_text = f" Violations: {', '.join(violations)}." if violations else ""
        return head + otr_text + breach_text
