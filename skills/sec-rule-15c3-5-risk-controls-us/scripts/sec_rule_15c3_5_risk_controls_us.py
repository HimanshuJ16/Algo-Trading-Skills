"""Pre-trade market access risk controls under SEC Rule 15c3-5 (17 CFR 240.15c3-5).

The engine maps each control it applies onto a specific clause of the rule:

* ``(c)(1)(i)``  credit/capital thresholds, "in the aggregate for each customer **and**
  the broker or dealer" -- hence both an account-level and a firm-level limb.
* ``(c)(1)(ii)`` erroneous orders "that exceed appropriate price or size parameters,
  on an order-by-order basis **or over a short period of time**, or that indicate
  duplicative orders" -- hence the size, notional and collar checks *plus* the burst
  and duplicate checks.
* ``(c)(2)(i)``  regulatory requirements "that must be satisfied on a pre-order entry
  basis" -- here, the Regulation SHO Rule 203(b)(1) locate for short sales.
* ``(c)(2)(ii)`` orders in securities the person "is restricted from trading".

**The rule prescribes no numeric thresholds.** Every default in
:class:`SecRule15c35Limits` is an engineering placeholder, not a regulatory figure, and
must be replaced with the firm's own calibrated values before live use.

Two failure classes are kept apart:

* **Mis-configuration** (a non-positive cap, a negative collar, a zero message rate)
  raises ``ValueError``/``TypeError`` at construction. A gate that cannot state its own
  limits must not issue verdicts.
* **Order-level problems** (NaN quantity, an unusable reference price, an unrecognised
  side) are *rejections*, because malformed order data genuinely arrives at a live gate
  and must be blocked there rather than crash it.

Threshold convention, applied uniformly: **the configured limit value is itself
permitted; a breach requires exceeding it.** An order of exactly
``max_single_order_qty`` passes.

Scope: this is a single-process reference implementation. The burst and duplicate
controls keep state in this process only, so a fleet of gateway processes each running
their own instance does not enforce a firm-wide limit -- see the "Cumulative controls"
note in ``references/workflows.md``.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Deque, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Order sides this gate recognises. Anything else is rejected rather than guessed at:
#: mapping an unknown side onto a default branch is how a short sale slips past the
#: Regulation SHO locate check.
VALID_SIDES: FrozenSet[str] = frozenset({"BUY", "SELL", "SELL_SHORT"})

#: Sides that constitute a short sale for Regulation SHO Rule 203(b)(1) purposes.
SHORT_SALE_SIDES: FrozenSet[str] = frozenset({"SELL_SHORT"})


@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str


class MarketAccessRuleCode(str, Enum):
    """Identifiers for each control, annotated with the clause that requires it."""

    CREDIT_CAP_EXCEEDED = "CREDIT_CAP_EXCEEDED"                    # (c)(1)(i) customer
    FIRM_CREDIT_CAP_EXCEEDED = "FIRM_CREDIT_CAP_EXCEEDED"          # (c)(1)(i) firm
    SINGLE_ORDER_NOTIONAL_CAP = "SINGLE_ORDER_NOTIONAL_CAP"        # (c)(1)(ii) size
    SINGLE_ORDER_QTY_CAP = "SINGLE_ORDER_QTY_CAP"                  # (c)(1)(ii) size
    PRICE_COLLAR_FAT_FINGER = "PRICE_COLLAR_FAT_FINGER"            # (c)(1)(ii) price
    SHORT_SALE_LOCATE_MISSING = "SHORT_SALE_LOCATE_MISSING"        # (c)(2)(i)
    RESTRICTED_SECURITY = "RESTRICTED_SECURITY"                    # (c)(2)(ii)
    RAPID_ORDER_BURST = "RAPID_ORDER_BURST"                        # (c)(1)(ii) period
    DUPLICATE_ORDER_DETECTED = "DUPLICATE_ORDER_DETECTED"          # (c)(1)(ii) dupes
    INVALID_ORDER = "INVALID_ORDER"                                # fail-closed
    REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"    # fail-closed


def _is_real_number(value: Any) -> bool:
    """True only for a finite int/float. Rejects bool, NaN, +/-inf and non-numerics.

    Every limit comparison against NaN evaluates to ``False``, so an unvalidated NaN
    quantity breaches no cap and the order is allowed -- the classic fail-open.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _normalise_symbol(symbol: Any) -> str:
    """Upper-case, whitespace-stripped symbol; ``''`` for anything non-textual."""
    if not isinstance(symbol, str):
        return ""
    return symbol.strip().upper()


@dataclass
class MarketAccessOrder:
    """An order presented to the pre-trade market access gate.

    ``nbbo_mid_price`` has **no default reference price**. A missing reference price
    rejects with ``REFERENCE_PRICE_UNAVAILABLE`` rather than being silently compared
    against a manufactured value.

    ``accumulated_credit_used_usd`` is this account's already-committed exposure and
    ``accumulated_firm_credit_used_usd`` the firm's, both supplied by the caller: the
    gate keeps no positions. 17 CFR 240.15c3-5(c)(1)(i) requires the threshold to be
    applied "in the aggregate for each customer and the broker or dealer", so a gate
    fed only the account figure enforces half the clause.

    ``timestamp_sec`` feeds the burst and duplicate windows. Leave it ``None`` to use
    the engine's clock; if supplied, every order must carry a timestamp from the same
    monotonic clock domain, or the rolling window is meaningless.

    ``is_bona_fide_market_making`` asserts the Rule 203(b)(2)(iii) locate exception for
    this order. It is honoured only when the firm has also enabled
    ``SecRule15c35Limits.allow_market_maker_locate_exception``.
    """

    order_id: str
    account_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL', 'SELL_SHORT'
    quantity: float
    price: float
    nbbo_mid_price: Optional[float] = None
    accumulated_credit_used_usd: float = 0.0
    short_locate_id: Optional[str] = None
    timestamp_sec: Optional[float] = None
    accumulated_firm_credit_used_usd: float = 0.0
    is_bona_fide_market_making: bool = False


@dataclass(frozen=True)
class SecRule15c35Limits:
    """Firm-calibrated pre-trade limits.

    Frozen deliberately. FINRA's 2026 Annual Regulatory Oversight Report cites
    inadequate oversight of intra-day changes to credit and capital thresholds --
    including obtaining approval before adjusting them, and temporary adjustments that
    never revert -- as Market Access Rule findings. Mutating a live limit set in place
    leaves no record; use :meth:`SecRule15C35RiskControlsUsEngine.replace_limits`,
    which requires an authoriser and a reason and writes an audit line.

    **None of these defaults is a regulatory threshold.** Rule 15c3-5 prescribes no
    numeric price or size parameters; the adopting release leaves them to the firm.
    Replace every value before live use.
    """

    firm_credit_cap_usd: float = 10000000.0
    account_credit_cap_usd: float = 1000000.0
    max_single_order_notional_usd: float = 250000.0
    max_single_order_qty: float = 5000.0
    max_price_collar_pct: float = 0.05    # 5% placeholder collar, fractional
    max_order_rate_per_sec: int = 100
    restricted_symbols: Set[str] = field(default_factory=set)
    burst_window_sec: float = 1.0
    duplicate_window_sec: float = 1.0
    allow_market_maker_locate_exception: bool = False

    def __post_init__(self) -> None:
        for name in (
            "firm_credit_cap_usd",
            "account_credit_cap_usd",
            "max_single_order_notional_usd",
            "max_single_order_qty",
        ):
            value = getattr(self, name)
            # An absent or zero cap is a mis-configured limit set, not an unlimited one.
            if not _is_real_number(value) or value <= 0:
                raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
        if self.account_credit_cap_usd > self.firm_credit_cap_usd:
            raise ValueError(
                "account_credit_cap_usd must not exceed firm_credit_cap_usd "
                f"({self.account_credit_cap_usd} > {self.firm_credit_cap_usd})"
            )
        if not _is_real_number(self.max_price_collar_pct) or self.max_price_collar_pct < 0:
            raise ValueError(
                "max_price_collar_pct must be a finite fraction >= 0 (0.05 == 5%), "
                f"got {self.max_price_collar_pct!r}"
            )
        if (
            isinstance(self.max_order_rate_per_sec, bool)
            or not isinstance(self.max_order_rate_per_sec, int)
            or self.max_order_rate_per_sec < 1
        ):
            raise ValueError(
                "max_order_rate_per_sec must be an int >= 1, got "
                f"{self.max_order_rate_per_sec!r}"
            )
        for name in ("burst_window_sec", "duplicate_window_sec"):
            value = getattr(self, name)
            if not _is_real_number(value) or value <= 0:
                raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
        if not isinstance(self.allow_market_maker_locate_exception, bool):
            raise TypeError("allow_market_maker_locate_exception must be a bool")
        if isinstance(self.restricted_symbols, (str, bytes)):
            # A bare string would iterate into single characters and quietly restrict
            # every one-letter ticker instead of the symbol intended.
            raise TypeError(
                "restricted_symbols must be a collection of symbols, not a bare string"
            )
        if not isinstance(self.restricted_symbols, Iterable):
            raise TypeError("restricted_symbols must be an iterable of symbols")
        # Normalise once at construction so a lowercase-configured restricted list is
        # not silently ineffective against an uppercase incoming symbol.
        normalised = frozenset(
            _normalise_symbol(s) for s in self.restricted_symbols if _normalise_symbol(s)
        )
        object.__setattr__(self, "restricted_symbols", normalised)

    def with_updates(self, **changes: Any) -> "SecRule15c35Limits":
        """Return a new, re-validated limit set with ``changes`` applied."""
        return replace(self, **changes)


@dataclass
class MarketAccessCheckResult:
    order_id: str
    is_allowed: bool
    triggered_violations: List[MarketAccessRuleCode]
    rejection_reasons: List[str]
    latency_microseconds: float
    audit_notes: str
    notional_usd: float = 0.0


class SecRule15C35RiskControlsUsEngine:
    """Pre-trade market access risk controls under 17 CFR 240.15c3-5.

    Applies the financial controls of (c)(1) and those regulatory controls of (c)(2)
    that can be evaluated on a pre-order-entry basis, returning an auditable
    :class:`MarketAccessCheckResult` per order.

    What this engine is **not**: the firm's whole 15c3-5 programme. It does not
    restrict system access to authorised persons ((c)(2)(iii)), route post-trade
    execution reports to surveillance ((c)(2)(iv)), conduct the annual effectiveness
    review ((e)(1)) or produce the CEO certification ((e)(2)). Nor does it cover every
    pre-order-entry regulatory requirement the adopting release names -- exchange rules
    on special order types, trading halts and odd lots, and Regulation NMS requirements
    are out of scope here.
    """

    def __init__(
        self,
        limits: Optional[SecRule15c35Limits] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limits is not None and not isinstance(limits, SecRule15c35Limits):
            raise TypeError("limits must be a SecRule15c35Limits")
        if not callable(clock):
            raise TypeError("clock must be a callable returning a monotonic float")
        self.limits = limits or SecRule15c35Limits()
        self._clock = clock
        self._lock = threading.Lock()
        # Per-account rolling message timestamps, for the "over a short period of time"
        # limb of (c)(1)(ii).
        self._recent_order_times: Dict[str, Deque[float]] = {}
        # Fingerprints of recently *accepted* orders, for the duplicate limb.
        self._recent_fingerprints: Deque[Tuple[float, Tuple[str, ...]]] = deque()
        self._accepted_fingerprints: Dict[Tuple[str, ...], float] = {}

    # ------------------------------------------------------------------ legacy API

    def run_checks(self, trade_data: dict) -> ComplianceResult:
        """Legacy structural sanity check retained for backward compatibility.

        This is **not** a Rule 15c3-5 control: it evaluates none of (c)(1) or (c)(2).
        Call :meth:`evaluate_market_access_order` for the market access gate. Kept
        fail-closed so it cannot be mistaken for one -- an empty, malformed, NaN or
        non-positive payload is non-compliant.
        """
        if not isinstance(trade_data, dict) or not trade_data:
            return ComplianceResult(False, "Empty or non-dict trade data")
        size = trade_data.get("size")
        if not _is_real_number(size):
            return ComplianceResult(False, f"Size {size!r} is not a finite number")
        if float(size) <= 0:
            return ComplianceResult(False, f"Size {size!r} is not positive")
        return ComplianceResult(True, "OK")

    # ------------------------------------------------------- limit change control

    def replace_limits(
        self, limits: SecRule15c35Limits, authorised_by: str, reason: str
    ) -> None:
        """Swap the live limit set, recording who authorised the change and why.

        FINRA's 2026 oversight report flags intra-day threshold changes made without
        prior approval, and temporary adjustments that never revert, as Market Access
        Rule findings. Route every change through here so it stays attributable.
        """
        if not isinstance(limits, SecRule15c35Limits):
            raise TypeError("limits must be a SecRule15c35Limits")
        for name, value in (("authorised_by", authorised_by), ("reason", reason)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        previous, self.limits = self.limits, limits
        logger.warning(
            "SEC RULE 15C3-5 LIMIT CHANGE authorised_by=%s reason=%s previous=%r new=%r",
            authorised_by, reason, previous, limits,
        )

    # ------------------------------------------------------------- the actual gate

    def evaluate_market_access_order(
        self, order: MarketAccessOrder
    ) -> MarketAccessCheckResult:
        """Evaluate one order against the pre-order-entry controls of Rule 15c3-5.

        Fails closed: an order whose fields cannot be compared against a limit is
        rejected with ``INVALID_ORDER`` and no further check is attempted, because a
        violation list computed from unusable input misleads the audit trail.
        """
        t0 = time.perf_counter_ns()
        if not isinstance(order, MarketAccessOrder):
            raise TypeError("order must be a MarketAccessOrder")

        limits = self.limits
        violations: List[MarketAccessRuleCode] = []
        reasons: List[str] = []

        invalid = self._validate_order(order)
        if invalid:
            violations.append(MarketAccessRuleCode.INVALID_ORDER)
            reasons.extend(invalid)
            return self._finish(order, 0.0, violations, reasons, t0)

        quantity = float(order.quantity)
        price = float(order.price)
        side = order.side.strip().upper()
        symbol = _normalise_symbol(order.symbol)
        notional_usd = quantity * price

        # 1. Single order quantity cap -- (c)(1)(ii), size parameter.
        if quantity > limits.max_single_order_qty:
            violations.append(MarketAccessRuleCode.SINGLE_ORDER_QTY_CAP)
            reasons.append(
                f"Qty {quantity} > Max allowed qty {limits.max_single_order_qty}"
            )

        # 2. Single order notional cap -- (c)(1)(ii), size parameter.
        if notional_usd > limits.max_single_order_notional_usd:
            violations.append(MarketAccessRuleCode.SINGLE_ORDER_NOTIONAL_CAP)
            reasons.append(
                f"Notional ${notional_usd:,.2f} > Max allowed single order notional "
                f"${limits.max_single_order_notional_usd:,.2f}"
            )

        # 3. Credit thresholds -- (c)(1)(i), "in the aggregate for each customer and
        #    the broker or dealer": both limbs, not the account alone.
        projected_credit = float(order.accumulated_credit_used_usd) + notional_usd
        if projected_credit > limits.account_credit_cap_usd:
            violations.append(MarketAccessRuleCode.CREDIT_CAP_EXCEEDED)
            reasons.append(
                f"Projected account credit ${projected_credit:,.2f} > Credit cap "
                f"${limits.account_credit_cap_usd:,.2f}"
            )
        projected_firm_credit = (
            float(order.accumulated_firm_credit_used_usd) + notional_usd
        )
        if projected_firm_credit > limits.firm_credit_cap_usd:
            violations.append(MarketAccessRuleCode.FIRM_CREDIT_CAP_EXCEEDED)
            reasons.append(
                f"Projected firm credit ${projected_firm_credit:,.2f} > Firm cap "
                f"${limits.firm_credit_cap_usd:,.2f}"
            )

        # 4. Fat-finger price collar -- (c)(1)(ii), price parameter.
        mid = order.nbbo_mid_price
        if not _is_real_number(mid) or float(mid) <= 0:
            # Skipping the collar on a missing reference price disables the control
            # exactly when a stale or absent feed makes a fat finger likeliest.
            violations.append(MarketAccessRuleCode.REFERENCE_PRICE_UNAVAILABLE)
            reasons.append(
                f"NBBO mid price {mid!r} is unusable; the price collar cannot be "
                "evaluated, so the order is blocked"
            )
        else:
            mid_f = float(mid)
            deviation = abs(price - mid_f)
            # Multiply rather than divide: `abs(p - m) / m > collar` spuriously rejects
            # an order priced at exactly the collar for a subset of reference prices
            # (mid 402.69, price 422.8245 divides to 0.05000000000000001 at a 5% collar).
            if deviation > limits.max_price_collar_pct * mid_f:
                violations.append(MarketAccessRuleCode.PRICE_COLLAR_FAT_FINGER)
                reasons.append(
                    f"Price ${price} deviates {deviation / mid_f:.2%} from NBBO mid "
                    f"${mid_f} (Max allowed {limits.max_price_collar_pct:.2%})"
                )

        # 5. Regulation SHO locate -- (c)(2)(i). The adopting release names "SEC rules
        #    under Regulation SHO" among the pre-order-entry regulatory requirements.
        if side in SHORT_SALE_SIDES:
            locate = order.short_locate_id
            has_locate = isinstance(locate, str) and bool(locate.strip())
            if not has_locate:
                if (
                    order.is_bona_fide_market_making
                    and limits.allow_market_maker_locate_exception
                ):
                    # Rule 203(b)(2)(iii). Claiming it is a firm determination about
                    # bona-fide market making, so it is logged, never silent.
                    logger.warning(
                        "SEC RULE 15C3-5 (%s): short sale in %s accepted without a "
                        "locate under the Rule 203(b)(2)(iii) bona-fide market making "
                        "exception", order.order_id, symbol,
                    )
                else:
                    violations.append(MarketAccessRuleCode.SHORT_SALE_LOCATE_MISSING)
                    reasons.append(
                        "Short sale order missing mandatory Reg SHO short locate ID."
                    )

        # 6. Restricted security -- (c)(2)(ii).
        if symbol in limits.restricted_symbols:
            violations.append(MarketAccessRuleCode.RESTRICTED_SECURITY)
            reasons.append(
                f"Symbol '{order.symbol}' is on the firm restricted trading list."
            )

        # 7 & 8. Cumulative controls -- (c)(1)(ii), "over a short period of time, or
        #        that indicate duplicative orders".
        now = (
            float(order.timestamp_sec)
            if _is_real_number(order.timestamp_sec)
            else float(self._clock())
        )
        fingerprint = self._fingerprint(order, side, symbol, quantity, price)
        with self._lock:
            self._expire(now)
            rate = self._record_message(order.account_id, now)
            if rate > limits.max_order_rate_per_sec:
                violations.append(MarketAccessRuleCode.RAPID_ORDER_BURST)
                reasons.append(
                    f"Account {order.account_id} sent {rate} messages in the last "
                    f"{limits.burst_window_sec}s > Max allowed "
                    f"{limits.max_order_rate_per_sec}/s"
                )
            if fingerprint in self._accepted_fingerprints:
                violations.append(MarketAccessRuleCode.DUPLICATE_ORDER_DETECTED)
                reasons.append(
                    "Order duplicates an order already accepted within the last "
                    f"{limits.duplicate_window_sec}s "
                    f"({symbol} {side} {quantity} @ {price})"
                )
            if not violations:
                # Only accepted orders seed the duplicate window: a rejected order was
                # never sent, so a corrected resubmission is not a duplicate of it.
                self._accepted_fingerprints[fingerprint] = now
                self._recent_fingerprints.append((now, fingerprint))

        return self._finish(order, notional_usd, violations, reasons, t0)

    # ------------------------------------------------------------------- internals

    @staticmethod
    def _validate_order(order: MarketAccessOrder) -> List[str]:
        """Structural checks whose failure makes every limit comparison meaningless."""
        problems: List[str] = []
        for name in ("order_id", "account_id", "symbol"):
            value = getattr(order, name)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{name} must be a non-empty string, got {value!r}")
        if not _is_real_number(order.quantity) or float(order.quantity) <= 0:
            # A negative quantity makes `quantity * price` negative, which slips under
            # every positive notional cap, and under the quantity cap as well.
            problems.append(
                f"quantity must be a finite number > 0, got {order.quantity!r}"
            )
        if not _is_real_number(order.price) or float(order.price) <= 0:
            problems.append(f"price must be a finite number > 0, got {order.price!r}")
        for name in ("accumulated_credit_used_usd", "accumulated_firm_credit_used_usd"):
            value = getattr(order, name)
            if not _is_real_number(value) or float(value) < 0:
                problems.append(f"{name} must be a finite number >= 0, got {value!r}")
        side = order.side.strip().upper() if isinstance(order.side, str) else None
        if side not in VALID_SIDES:
            # An unrecognised side must not fall through to a default branch: that is
            # how "SHORT" or "sell short" bypasses the Reg SHO locate check entirely.
            problems.append(
                f"side must be one of {sorted(VALID_SIDES)}, got {order.side!r}"
            )
        if not isinstance(order.is_bona_fide_market_making, bool):
            problems.append("is_bona_fide_market_making must be a bool")
        if order.timestamp_sec is not None and not _is_real_number(order.timestamp_sec):
            problems.append(
                "timestamp_sec must be None or a finite number, got "
                f"{order.timestamp_sec!r}"
            )
        return problems

    @staticmethod
    def _fingerprint(
        order: MarketAccessOrder, side: str, symbol: str, quantity: float, price: float
    ) -> Tuple[str, ...]:
        """Economic identity of an order, for the duplicative-orders limb.

        ``account_id`` is used verbatim -- it is an opaque system-issued key, and
        case-folding it would both collide two genuinely distinct accounts into one
        duplicate namespace and disagree with the burst counter, which keys on the raw
        id. ``symbol`` is normalised, because "aapl" and "AAPL" are one instrument.
        """
        return (
            order.account_id,
            symbol,
            side,
            repr(quantity),
            repr(price),
        )

    def _expire(self, now: float) -> None:
        """Drop window entries older than the configured horizons. Caller holds lock."""
        burst_floor = now - self.limits.burst_window_sec
        for account_id, stamps in list(self._recent_order_times.items()):
            while stamps and stamps[0] <= burst_floor:
                stamps.popleft()
            if not stamps:
                del self._recent_order_times[account_id]
        dupe_floor = now - self.limits.duplicate_window_sec
        while self._recent_fingerprints and self._recent_fingerprints[0][0] <= dupe_floor:
            _, stale = self._recent_fingerprints.popleft()
            if self._accepted_fingerprints.get(stale, math.inf) <= dupe_floor:
                del self._accepted_fingerprints[stale]

    def _record_message(self, account_id: str, now: float) -> int:
        """Record this message and return the account's count in the burst window."""
        stamps = self._recent_order_times.setdefault(account_id, deque())
        stamps.append(now)
        return len(stamps)

    def _finish(
        self,
        order: MarketAccessOrder,
        notional_usd: float,
        violations: List[MarketAccessRuleCode],
        reasons: List[str],
        t0: int,
    ) -> MarketAccessCheckResult:
        dt_us = round((time.perf_counter_ns() - t0) / 1000.0, 2)
        is_allowed = not violations
        status = "ALLOWED" if is_allowed else "REJECTED_15C3_5_BREACH"
        notes = (
            f"SEC RULE 15C3-5 [{status}] ({order.order_id}): Symbol = {order.symbol}, "
            f"Notional = ${notional_usd:,.2f}, "
            f"Violations = {[v.value for v in violations]}, Latency = {dt_us}us."
        )
        if is_allowed:
            logger.info(notes)
        else:
            logger.warning(notes)
        return MarketAccessCheckResult(
            order_id=order.order_id,
            is_allowed=is_allowed,
            triggered_violations=violations,
            rejection_reasons=reasons,
            latency_microseconds=dt_us,
            audit_notes=notes,
            notional_usd=notional_usd,
        )
