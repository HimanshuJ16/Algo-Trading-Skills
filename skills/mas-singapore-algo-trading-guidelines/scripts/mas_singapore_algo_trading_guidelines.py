"""Pre-trade compliance gate for algorithmic orders sent to Singapore Exchange.

WHAT SINGAPORE ACTUALLY REQUIRES
--------------------------------
There is **no MAS algorithm registration regime and no MAS-issued algorithm
identifier**. Singapore does not operate anything equivalent to the SEBI
exchange-assigned Algo-ID: MAS registers *entities* and *representatives*, and
SGX registers *members and Approved Traders*, never individual algorithms. The
obligations that actually attach to an SGX algorithmic order come from three
distinct layers, and this module keeps them distinct:

  1. MAS / Securities and Futures Act 2001 (SFA) -- entity-level licensing.
     Dealing in capital markets products requires a Capital Markets Services
     licence or an exemption, and the individuals who deal must be appointed
     representatives under the SFA representative notification framework.
     Market misconduct provisions (SFA Part 12, e.g. false trading and market
     rigging under s. 197, market manipulation under s. 201) bind algorithmic
     order flow exactly as they bind manual order flow.
  2. SGX rules -- the per-order layer this module gates on:
       * Approved Trader registration: SGX Futures Trading Rules 2.13.2 and
         2.13.4 (registration of, and register of, Approved Traders and
         Registered Representatives).
       * Pre-execution risk limits: Clearing Members must set pre-execution
         limits on their trading participants -- SGX FTR 3.9.1(3) and
         Practice Note 3.9.1(3) (Pre-Execution Checks). Every order is checked
         either at the Clearing Member's hosted system or by SGX's own
         exchange-hosted Pre-Trade Risk Controls module. The LIMIT VALUES are
         set by the firm and its Clearing Member; SGX publishes none.
       * Forced Order Range: SGX-ST Practice Note 8.6 and Regulatory Notice
         11.4.2(g) (Application of the Force Key). An order priced outside the
         Forced Order Range must be confirmed with the Force Key before it may
         be submitted -- a fat-finger control, not an absolute prohibition.
       * Circuit breakers: SGX-ST Rule 8.14 with Regulatory Notice 8.14.1 and
         Practice Note 8.10A; SGX-DT price limits under FTR 4.1.15 and the
         individual contract specifications.
       * Automated trading controls: SGX RegCo's Algorithmic Trading
         Regulatory Guide, key aspects of which were formalised into the
         Futures Trading Rules and the SGX-ST Rules following the SGX RegCo
         consultation of 21 September 2023.
  3. Firm-set controls -- order value ceilings, message rate ceilings, kill
     switch, pre-deployment testing sign-off. Required in substance; the
     numbers are the firm's own and are audited here as house limits, never
     presented as regulatory thresholds.

CIRCUIT BREAKER SEMANTICS -- THE PART MOST GATES GET WRONG
----------------------------------------------------------
The SGX-ST circuit breaker is **not an order-price collar**. A Cooling-Off
Period is triggered when an *incoming order seeks to be matched, wholly or
partly, with an existing order at a price outside the price band*; the incoming
order is not matched outside the band and the quantity left unfilled at the
commencement of the Cooling-Off Period is rejected. Three consequences follow,
all of them modelled here:

  * A non-marketable limit order priced far outside the band is NOT rejected on
    entry. It rests. It becomes a latent trigger for whoever aggresses it later.
  * The reference price is the last traded price at least five minutes earlier,
    not the current mid and not the current last done. A gate that compares
    against the live mid will disagree with the exchange in exactly the fast
    markets where the mechanism matters.
  * The band is inclusive -- trading must be within *or at* the thresholds --
    so a breach requires the potential trade price to be strictly outside it.
    Deviations are therefore compared unrounded and rounded only for reporting.

Circuit breakers apply only to eligible instruments (SGX-ST assesses
eligibility daily; the reference price at the start of the Market Day must be
at least 0.50 in the instrument's underlying currency, or JPY 500 for
yen-denominated instruments) and only during continuous trading -- not during
the opening and closing routines. The 10% band is the SGX-ST securities figure;
SGX-DT price limits are per contract and MUST be overridden per instrument.

Every threshold in this module is a caller-supplied parameter. Defaults are
dated snapshots of published SGX values, not constants of the law.

Sources: see references/standards.md.
"""
import logging
import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# SGX-ST securities circuit breaker band, in percent either side of the
# reference price (SGX-ST Regulatory Notice 8.14.1). Snapshot verified
# 2026-08-25. SGX-DT contracts use per-contract price limits under FTR 4.1.15
# and their contract specifications -- override per instrument.
DEFAULT_SGX_ST_CIRCUIT_BREAKER_PCT: float = 10.0

# Forced Order Range, expressed in minimum bid sizes either side of the Forced
# Order Range reference price (SGX-ST Practice Note 8.6). +/-30 bids is the
# published figure for stocks with bid sizes below 0.20, and for ETFs and
# debentures at all bid sizes. The range varies by product class and bid size
# -- override per instrument.
DEFAULT_FORCED_ORDER_RANGE_BIDS: int = 30

# Circuit breakers operate during continuous trading only. The opening routine,
# the closing routine and the Trade-at-Close auction sit outside the mechanism.
DEFAULT_CIRCUIT_BREAKER_SESSIONS: Tuple[str, ...] = ("CONTINUOUS",)

VALID_ORDER_SIDES: Tuple[str, ...] = ("BUY", "SELL")

STATUS_APPROVED: str = "SGX_PRE_TRADE_APPROVED"

# Precedence used to pick the headline `status` when an order breaches several
# requirements at once. `breaches` always carries the complete set.
_BREACH_SEVERITY_ORDER: Tuple[str, ...] = (
    "REJECTED_UNLICENSED_ENTITY",
    "REJECTED_UNREGISTERED_APPROVED_TRADER",
    "REJECTED_ALGO_ID_MISMATCH",
    "REJECTED_ALGO_NOT_TESTED",
    "REJECTED_NO_KILL_SWITCH",
    "REJECTED_UNPRICEABLE_ORDER",
    "REJECTED_PRE_EXECUTION_LIMIT",
    "REJECTED_CIRCUIT_BREAKER_BAND",
    "REJECTED_FORCED_ORDER_RANGE",
    "REJECTED_ORDER_RATE_LIMIT",
)

# Tolerance for threshold comparisons, so that a value sitting exactly on a
# threshold in decimal terms is not pushed over it by binary representation.
_EPSILON: float = 1e-9


@dataclass(frozen=True)
class SingaporeAlgoControlConfig:
    """Entity- and firm-level controls in force for one trading algorithm.

    Attributes:
        algo_id: The firm's OWN identifier for the algorithm. This is a house
            audit-trail tag. It is NOT a regulatory registration: MAS issues no
            algorithm identifiers and SGX does not register algorithms.
        approved_trader_id: Identifier of the SGX-registered Approved Trader or
            Registered Representative under whose authority the order is
            entered (SGX FTR 2.13.2, 2.13.4). Algorithmic order flow still
            answers to a registered natural person.
        is_approved_trader_registered: That registration is current.
        has_cms_licence_or_exemption: The entity holds a Capital Markets
            Services licence under the SFA, or a documented exemption. The
            engine records the caller's assertion; it cannot verify it. The
            authoritative source is the MAS Financial Institutions Directory.
        is_pre_deployment_tested: Pre-deployment testing of this algorithm has
            been signed off, per SGX RegCo's Algorithmic Trading Regulatory
            Guide and the automated trading requirements formalised into the
            SGX rulebooks.
        has_kill_switch: A control able to withdraw the algorithm's unexecuted
            orders and stop further order entry is armed and reachable.
        max_order_value: FIRM-SET pre-execution order value ceiling, in
            `limit_currency`, consistent with the pre-execution limits a
            Clearing Member sets under SGX FTR 3.9.1(3). SGX publishes no
            figure; this default is a placeholder that MUST be calibrated.
        limit_currency: Currency of `max_order_value`. SGX lists counters in
            several currencies; comparing a USD notional against an SGD ceiling
            silently understates risk, so a mismatch is rejected outright.
        max_order_rate_per_sec: FIRM-SET ceiling on order messages per second
            for this algorithm. Not an SGX-published number.
        circuit_breaker_band_pct: Band half-width in percent. Defaults to the
            SGX-ST securities figure; override per SGX-DT contract.
        forced_order_range_bids: Forced Order Range half-width in minimum bid
            sizes. Defaults to the published +/-30; varies by product class.
    """

    algo_id: str
    approved_trader_id: str
    is_approved_trader_registered: bool
    has_cms_licence_or_exemption: bool
    is_pre_deployment_tested: bool
    has_kill_switch: bool
    max_order_value: float = 1_000_000.0
    limit_currency: str = "SGD"
    max_order_rate_per_sec: int = 50
    circuit_breaker_band_pct: float = DEFAULT_SGX_ST_CIRCUIT_BREAKER_PCT
    forced_order_range_bids: int = DEFAULT_FORCED_ORDER_RANGE_BIDS


@dataclass(frozen=True)
class SgxOrderRequest:
    """A single order presented for a pre-trade SGX compliance audit.

    Attributes:
        algo_id: Algorithm tag carried on the order. Must match the config's
            `algo_id`; a mismatch means the order is being audited against
            another algorithm's limits.
        symbol: SGX instrument code, e.g. 'D05' (DBS), 'Z74' (Singtel).
        side: 'BUY' or 'SELL'.
        quantity: Order quantity in units of the instrument. Must be a positive
            integer. Board-lot rounding is a separate concern -- see
            `minimum-fill-size-and-lot-rounding-logic`.
        limit_price: Limit price, or None for a market order. A market order
            cannot be bounded ex ante, so it is treated as marketable and, for
            valuation, priced off `opposite_best_price`.
        currency: Currency the order is priced in. Must match the config's
            `limit_currency`.
        min_bid_size: The instrument's minimum bid size (tick). Required to
            evaluate the Forced Order Range; None skips that check and records
            a warning.
        forced_order_range_ref_price: Reference price the Forced Order Range is
            measured from. Its derivation is exchange-determined; supply the
            value the trading system uses rather than deriving one here.
        circuit_breaker_ref_price: Circuit breaker reference price -- the last
            traded price AT LEAST FIVE MINUTES EARLIER, not the current mid and
            not the current last done.
        opposite_best_price: Best price on the opposite side of the book (best
            ask for a BUY, best bid for a SELL). Used to decide marketability.
            None means unknown, which resolves conservatively to "may be
            marketable".
        is_circuit_breaker_eligible: Whether SGX-ST has the circuit breaker
            switched on for this instrument today. Eligibility is assessed
            daily against the reference price at the start of the Market Day.
            None means unknown and resolves conservatively to eligible.
        session: Trading session phase, e.g. 'CONTINUOUS', 'PRE_OPEN',
            'CLOSING_ROUTINE', 'TRADE_AT_CLOSE'.
        force_key_confirmed: The Force Key confirmation has been given for an
            order priced outside the Forced Order Range.
        current_order_rate_per_sec: Order messages sent by this algorithm in
            the current one-second window, INCLUDING this one. The caller owns
            the counter; this engine is stateless and cannot enforce a rate on
            its own.
    """

    algo_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Optional[float] = None
    currency: str = "SGD"
    min_bid_size: Optional[float] = None
    forced_order_range_ref_price: Optional[float] = None
    circuit_breaker_ref_price: Optional[float] = None
    opposite_best_price: Optional[float] = None
    is_circuit_breaker_eligible: Optional[bool] = None
    session: str = "CONTINUOUS"
    force_key_confirmed: bool = False
    current_order_rate_per_sec: int = 1


@dataclass(frozen=True)
class SgxPreTradeComplianceReport:
    """Outcome of the audit. Any check that did not run reports None.

    A compliance report that carries a figure for a check that never ran is an
    audit trail that lies, so unevaluated checks are None rather than 0.0.
    """

    algo_id: str
    approved_trader_id: str
    symbol: str
    order_value: Optional[float]
    order_currency: str
    is_marketable: Optional[bool]
    circuit_breaker_deviation_pct: Optional[float]
    forced_order_range_bids_away: Optional[float]
    is_compliant: bool
    status: str
    breaches: Tuple[str, ...]
    warnings: Tuple[str, ...]
    audit_notes: str


class MasSingaporeAlgoComplianceEngine:
    """Stateless pre-trade gate for algorithmic orders routed to SGX.

    All checks run on every call; nothing short-circuits, because an order can
    breach several requirements at once and remediation needs the full list.
    The headline `status` reports the most serious breach in
    `_BREACH_SEVERITY_ORDER`; `breaches` carries them all.
    """

    def __init__(
        self,
        circuit_breaker_sessions: Optional[Iterable[str]] = None,
    ) -> None:
        """
        Args:
            circuit_breaker_sessions: Session phases during which the circuit
                breaker mechanism operates. Defaults to continuous trading
                only, matching SGX-ST, which does not run the mechanism during
                the opening and closing routines.

        Raises:
            ValueError: `circuit_breaker_sessions` is empty or holds a blank
                session name.
        """
        # An explicitly empty iterable is a caller error, not a request for the
        # default: silently restoring the default would switch the circuit
        # breaker check back on for a caller who asked to switch it off.
        supplied = (
            DEFAULT_CIRCUIT_BREAKER_SESSIONS
            if circuit_breaker_sessions is None
            else circuit_breaker_sessions
        )
        sessions = tuple(str(s).strip().upper() for s in supplied)
        if not sessions or any(not s for s in sessions):
            raise ValueError(
                "circuit_breaker_sessions must contain at least one non-empty session name."
            )
        self.circuit_breaker_sessions: Tuple[str, ...] = sessions

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def validate_sgx_order_compliance(
        self,
        config: SingaporeAlgoControlConfig,
        order: SgxOrderRequest,
    ) -> SgxPreTradeComplianceReport:
        """Audit one order against SGX pre-trade rules and firm-set controls.

        Args:
            config: Entity- and firm-level controls for the algorithm.
            order: The order to audit.

        Returns:
            An `SgxPreTradeComplianceReport` in which every check has run.

        Raises:
            TypeError: A field has the wrong type.
            ValueError: A field is structurally invalid -- a non-finite or
                non-positive price, a non-positive quantity, an unknown side, a
                negative rate counter, or a currency mismatch between the order
                and the firm's value ceiling. These are caller bugs, not
                compliance outcomes, and must not be reported as clean audits.
        """
        self._validate_config(config)
        self._validate_order(order)
        if order.currency.strip().upper() != config.limit_currency.strip().upper():
            raise ValueError(
                f"Order currency {order.currency!r} does not match the pre-execution "
                f"limit currency {config.limit_currency!r}. Convert the order value "
                f"before auditing it -- comparing across currencies understates risk."
            )

        breaches: List[str] = []
        warnings: List[str] = []

        # 1. Entity and Approved Trader governance (MAS SFA licensing; SGX FTR 2.13).
        if not config.has_cms_licence_or_exemption:
            breaches.append("REJECTED_UNLICENSED_ENTITY")
        if not config.is_approved_trader_registered or not config.approved_trader_id.strip():
            breaches.append("REJECTED_UNREGISTERED_APPROVED_TRADER")
        if order.algo_id.strip() != config.algo_id.strip():
            breaches.append("REJECTED_ALGO_ID_MISMATCH")

        # 2. Automated trading controls (SGX RegCo Algorithmic Trading
        #    Regulatory Guide, as formalised into the SGX rulebooks).
        if not config.is_pre_deployment_tested:
            breaches.append("REJECTED_ALGO_NOT_TESTED")
        if not config.has_kill_switch:
            breaches.append("REJECTED_NO_KILL_SWITCH")

        # 3. Pre-execution value limit (SGX FTR 3.9.1(3); value set by the firm).
        order_value = self._order_value(order, warnings)
        if order_value is None:
            breaches.append("REJECTED_UNPRICEABLE_ORDER")
        elif order_value > config.max_order_value + _EPSILON:
            breaches.append("REJECTED_PRE_EXECUTION_LIMIT")

        # 4. Circuit breaker band (SGX-ST Rule 8.14).
        is_marketable = self._assess_marketability(order, warnings)
        deviation_pct = self._audit_circuit_breaker(
            config, order, is_marketable, breaches, warnings
        )

        # 5. Forced Order Range / Force Key (SGX-ST Practice Note 8.6).
        bids_away = self._audit_forced_order_range(config, order, breaches, warnings)

        # 6. Message rate ceiling (firm-set).
        if order.current_order_rate_per_sec > config.max_order_rate_per_sec:
            breaches.append("REJECTED_ORDER_RATE_LIMIT")

        breach_tuple = tuple(breaches)
        warning_tuple = tuple(warnings)
        status = self._rank_status(breach_tuple)
        notes = self._compose_notes(
            config, order, order_value, deviation_pct, bids_away, status,
            breach_tuple, warning_tuple,
        )

        if breach_tuple:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SgxPreTradeComplianceReport(
            algo_id=config.algo_id,
            approved_trader_id=config.approved_trader_id,
            symbol=order.symbol,
            order_value=order_value,
            order_currency=order.currency.strip().upper(),
            is_marketable=is_marketable,
            circuit_breaker_deviation_pct=deviation_pct,
            forced_order_range_bids_away=bids_away,
            is_compliant=not breach_tuple,
            status=status,
            breaches=breach_tuple,
            warnings=warning_tuple,
            audit_notes=notes,
        )

    # ------------------------------------------------------------------ #
    # Individual checks
    # ------------------------------------------------------------------ #
    @staticmethod
    def _assess_marketability(
        order: SgxOrderRequest,
        warnings: List[str],
    ) -> Optional[bool]:
        """Would this order match immediately against the resting book?

        A market order always can. A limit order can only if it crosses the
        opposite best price. Unknown book state resolves to None, which the
        circuit breaker check treats conservatively as "may be marketable".
        """
        if order.limit_price is None:
            return True
        if order.opposite_best_price is None:
            warnings.append(
                "opposite_best_price not supplied: marketability unknown, so the "
                "circuit breaker check assumes the order may match immediately."
            )
            return None
        if order.side == "BUY":
            return order.limit_price >= order.opposite_best_price - _EPSILON
        return order.limit_price <= order.opposite_best_price + _EPSILON

    def _audit_circuit_breaker(
        self,
        config: SingaporeAlgoControlConfig,
        order: SgxOrderRequest,
        is_marketable: Optional[bool],
        breaches: List[str],
        warnings: List[str],
    ) -> Optional[float]:
        """Check the potential trade price against the circuit breaker band.

        The price under test is the worst KNOWABLE potential trade price: the
        limit price for a limit order, the opposite best price for a market
        order. Returns its signed deviation from the circuit breaker reference
        price, in percent, or None when the mechanism does not apply or could
        not be evaluated.
        """
        if order.session.strip().upper() not in self.circuit_breaker_sessions:
            return None

        if order.is_circuit_breaker_eligible is None:
            warnings.append(
                "is_circuit_breaker_eligible not supplied: resolved conservatively "
                "as eligible. SGX-ST assesses eligibility daily."
            )
        elif not order.is_circuit_breaker_eligible:
            return None

        ref = order.circuit_breaker_ref_price
        if ref is None:
            warnings.append(
                "circuit_breaker_ref_price not supplied: the circuit breaker band "
                "was NOT evaluated. Supply the last traded price at least five "
                "minutes earlier, not the current mid."
            )
            return None

        # A market order is unbounded above (BUY) or below (SELL): no limit
        # price caps where it could trade. The one price that IS knowable is
        # the touch it will hit first, so the band is checked against that. If
        # even the first fill is outside the band the breach is certain; if it
        # is inside, the order can still walk out of the band deeper in the
        # book, which is a warning rather than a clean pass.
        price_under_test = order.limit_price
        if price_under_test is None:
            price_under_test = order.opposite_best_price
            if price_under_test is None:
                warnings.append(
                    "Market order with no opposite_best_price: exposure to the "
                    "circuit breaker band could NOT be evaluated pre-trade."
                )
                return None
            warnings.append(
                "Market order: the band was checked against the opposite best price "
                "only. The order can still walk past the band deeper in the book, in "
                "which case SGX rejects the quantity left unfilled and starts a "
                "five-minute Cooling-Off Period."
            )

        deviation_pct = (price_under_test - ref) / ref * 100.0
        if abs(deviation_pct) <= config.circuit_breaker_band_pct + _EPSILON:
            return deviation_pct

        if is_marketable is False:
            # The rule bites on the incoming aggressor, not on a resting order.
            # This one rests, and becomes a latent trigger for whoever crosses
            # it later.
            warnings.append(
                f"Order rests {deviation_pct:+.4f}% from the circuit breaker reference "
                f"price, outside the +/-{config.circuit_breaker_band_pct}% band. It is "
                f"not rejected on entry, but it is a latent Cooling-Off trigger for any "
                f"incoming order that later matches against it."
            )
            return deviation_pct

        breaches.append("REJECTED_CIRCUIT_BREAKER_BAND")
        return deviation_pct

    @staticmethod
    def _audit_forced_order_range(
        config: SingaporeAlgoControlConfig,
        order: SgxOrderRequest,
        breaches: List[str],
        warnings: List[str],
    ) -> Optional[float]:
        """Check the order price against the Forced Order Range, in bids."""
        if order.limit_price is None:
            return None
        if order.min_bid_size is None or order.forced_order_range_ref_price is None:
            warnings.append(
                "min_bid_size or forced_order_range_ref_price not supplied: the "
                "Forced Order Range was NOT evaluated."
            )
            return None

        bids_away = (
            abs(order.limit_price - order.forced_order_range_ref_price) / order.min_bid_size
        )
        if bids_away <= config.forced_order_range_bids + _EPSILON:
            return bids_away

        if order.force_key_confirmed:
            # Practice Note 8.6 permits the order once the Force Key has been
            # used. The override is deliberate, so it is recorded, not blocked.
            warnings.append(
                f"Order is {bids_away:.2f} bids from the Forced Order Range reference "
                f"price (range +/-{config.forced_order_range_bids} bids) and was "
                f"submitted under a Force Key confirmation."
            )
            return bids_away

        breaches.append("REJECTED_FORCED_ORDER_RANGE")
        return bids_away

    @staticmethod
    def _order_value(
        order: SgxOrderRequest,
        warnings: List[str],
    ) -> Optional[float]:
        """Value the order for the pre-execution limit check.

        A limit order is valued at its limit. A market order has no price of
        its own and is valued off the opposite best price; with neither
        available the order cannot be valued and the check fails closed.
        """
        price = order.limit_price
        if price is None:
            price = order.opposite_best_price
            if price is None:
                return None
            warnings.append(
                "Market order valued at the opposite best price. The executed value "
                "can exceed this if the order walks the book."
            )
        return price * order.quantity

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rank_status(breaches: Tuple[str, ...]) -> str:
        for candidate in _BREACH_SEVERITY_ORDER:
            if candidate in breaches:
                return candidate
        return STATUS_APPROVED

    @staticmethod
    def _compose_notes(
        config: SingaporeAlgoControlConfig,
        order: SgxOrderRequest,
        order_value: Optional[float],
        deviation_pct: Optional[float],
        bids_away: Optional[float],
        status: str,
        breaches: Tuple[str, ...],
        warnings: Tuple[str, ...],
    ) -> str:
        value_txt = (
            f"{order_value:,.2f} {config.limit_currency}"
            if order_value is not None
            else "not evaluated"
        )
        dev_txt = f"{deviation_pct:+.4f}%" if deviation_pct is not None else "not evaluated"
        bids_txt = f"{bids_away:.2f} bids" if bids_away is not None else "not evaluated"
        note = (
            f"SGX PRE-TRADE {status} [{order.symbol}] {order.side} {order.quantity:,} "
            f"algo={config.algo_id} trader={config.approved_trader_id} "
            f"value={value_txt} cb_deviation={dev_txt} forced_order_range={bids_txt}"
        )
        if breaches:
            note += f" | breaches: {', '.join(breaches)}"
        if warnings:
            note += f" | warnings: {len(warnings)}"
        return note

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_finite_positive(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
        amount = float(value)
        if not math.isfinite(amount) or amount <= 0.0:
            raise ValueError(
                f"{label} must be a finite positive number, got {value!r}. NaN and "
                f"infinity compare False against every ceiling and would be approved."
            )
        return amount

    def _validate_config(self, config: SingaporeAlgoControlConfig) -> None:
        if not isinstance(config, SingaporeAlgoControlConfig):
            raise TypeError(
                f"config must be a SingaporeAlgoControlConfig, got {type(config).__name__}."
            )
        if not isinstance(config.algo_id, str) or not config.algo_id.strip():
            raise ValueError("algo_id must be a non-empty string.")
        if not isinstance(config.approved_trader_id, str):
            raise TypeError(
                f"approved_trader_id must be a string, got "
                f"{type(config.approved_trader_id).__name__}."
            )
        if not isinstance(config.limit_currency, str) or not config.limit_currency.strip():
            raise ValueError("limit_currency must be a non-empty string.")
        self._require_finite_positive(config.max_order_value, "max_order_value")
        self._require_finite_positive(
            config.circuit_breaker_band_pct, "circuit_breaker_band_pct"
        )
        self._require_positive_int(config.max_order_rate_per_sec, "max_order_rate_per_sec")
        self._require_positive_int(config.forced_order_range_bids, "forced_order_range_bids")

    def _validate_order(self, order: SgxOrderRequest) -> None:
        if not isinstance(order, SgxOrderRequest):
            raise TypeError(f"order must be an SgxOrderRequest, got {type(order).__name__}.")
        if not isinstance(order.symbol, str) or not order.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if not isinstance(order.algo_id, str):
            raise TypeError(
                f"order.algo_id must be a string, got {type(order.algo_id).__name__}."
            )
        if order.side not in VALID_ORDER_SIDES:
            raise ValueError(f"side must be one of {VALID_ORDER_SIDES}, got {order.side!r}.")
        if isinstance(order.quantity, bool) or not isinstance(order.quantity, int):
            raise TypeError(f"quantity must be an int, got {type(order.quantity).__name__}.")
        if order.quantity <= 0:
            raise ValueError(
                f"quantity must be a positive integer, got {order.quantity!r}. A "
                f"non-positive quantity produces a non-positive order value that "
                f"passes every value ceiling."
            )
        for label, value in (
            ("limit_price", order.limit_price),
            ("min_bid_size", order.min_bid_size),
            ("forced_order_range_ref_price", order.forced_order_range_ref_price),
            ("circuit_breaker_ref_price", order.circuit_breaker_ref_price),
            ("opposite_best_price", order.opposite_best_price),
        ):
            if value is not None:
                self._require_finite_positive(value, label)
        if not isinstance(order.currency, str) or not order.currency.strip():
            raise ValueError("currency must be a non-empty string.")
        if not isinstance(order.session, str) or not order.session.strip():
            raise ValueError("session must be a non-empty string.")
        if isinstance(order.current_order_rate_per_sec, bool) or not isinstance(
            order.current_order_rate_per_sec, int
        ):
            raise TypeError(
                f"current_order_rate_per_sec must be an int, got "
                f"{type(order.current_order_rate_per_sec).__name__}."
            )
        if order.current_order_rate_per_sec < 0:
            raise ValueError(
                f"current_order_rate_per_sec must be non-negative, got "
                f"{order.current_order_rate_per_sec!r}."
            )
        if order.is_circuit_breaker_eligible is not None and not isinstance(
            order.is_circuit_breaker_eligible, bool
        ):
            raise TypeError(
                f"is_circuit_breaker_eligible must be a bool or None, got "
                f"{type(order.is_circuit_breaker_eligible).__name__}."
            )

    @staticmethod
    def _require_positive_int(value: int, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an int, got {type(value).__name__}.")
        if value <= 0:
            raise ValueError(f"{label} must be positive, got {value!r}.")
        return value
