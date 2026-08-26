"""
multi-strategy-capital-allocation-limits: Allocates and caps capital across
multiple concurrently-running strategies sharing one brokerage account.

Exposure convention
-------------------
Every exposure figure handled here is GROSS notional in the account's base
currency and is therefore non-negative: a $50k long plus a $50k short consumes
$100k of capital, not $0. Netting longs against shorts would hand a
market-neutral strategy unlimited headroom, so it is deliberately not done.

``order_value_usd`` is the *change in gross notional* the order would cause:
positive for exposure-increasing orders, negative or zero for exposure-reducing
ones. Exposure-reducing orders are never vetoed, even when the strategy is
already over its cap.

In-flight orders
----------------
A pre-trade capital check that counts only settled exposure will approve two
orders that each fit the cap but jointly breach it, because neither has filled
yet when the second is checked. Capacity is therefore consumed at reservation
time (:meth:`MultiStrategyCapitalAllocator.reserve`) and released on fill,
cancel or reject. Callers that place orders from more than one thread must use
``reserve``; :meth:`MultiStrategyCapitalAllocator.check_order` is an advisory,
non-reserving preview and is subject to check-then-trade races.

Scope
-----
This module caps *notional capital allocation*. It is not a margin engine, a
leverage calculator, or a substitute for broker-side buying-power arithmetic.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Absolute (not proportional) slack used when comparing dollar amounts against
# a cap, so that float round-trips through percentage arithmetic cannot reject
# an order that sits exactly at the limit. Deliberately fixed at one cent rather
# than scaled to NAV: the tolerance should absorb representation noise, not
# grant a NAV-proportional overdraft.
AMOUNT_TOLERANCE_USD = 0.01

# Slack for percentage-budget comparisons (allocations summing to exactly the
# investable fraction must pass).
PCT_TOLERANCE = 1e-9


class AllocationError(ValueError):
    """Raised when capital allocation configuration or state is invalid."""


class RejectionCode:
    """Stable machine-readable reasons for a blocked order.

    Prefer matching on these over parsing ``rejection_reason`` prose.
    """

    UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
    INVALID_INPUT = "INVALID_INPUT"
    STRATEGY_CAP = "STRATEGY_CAP"
    PORTFOLIO_CAP = "PORTFOLIO_CAP"


@dataclass
class StrategyAllocation:
    """Per-strategy capital budget and the capital currently consumed against it.

    ``current_exposure_usd`` is settled gross notional (mark-to-market).
    ``pending_exposure_usd`` is gross notional reserved for orders that are
    working but not yet settled. Both are non-negative.
    """

    strategy_name: str
    max_allocation_pct: float  # e.g. 0.40 = 40% of NAV
    current_exposure_usd: float = 0.0
    pending_exposure_usd: float = 0.0

    @property
    def committed_exposure_usd(self) -> float:
        """Settled exposure plus capital reserved for in-flight orders."""
        return self.current_exposure_usd + self.pending_exposure_usd


@dataclass
class Reservation:
    """Capital held for a single working order, keyed by client order id."""

    order_id: str
    strategy_name: str
    requested_usd: float  # signed change in gross notional as submitted
    reserved_usd: float  # capacity actually held (never negative)


@dataclass
class AllocationCheckResult:
    """Outcome of a pre-trade capital allocation check."""

    approved: bool
    strategy_name: str
    requested_usd: float
    current_exposure_usd: float
    max_allowed_usd: float
    remaining_capacity_usd: float
    rejection_reason: Optional[str] = None
    pending_exposure_usd: float = 0.0
    rejection_code: Optional[str] = None
    order_id: Optional[str] = None


class MultiStrategyCapitalAllocator:
    """
    Enforces per-strategy capital allocation limits across multiple strategies
    sharing a single brokerage account, plus an account-level cap that keeps the
    configured cash reserve intact.

    All public methods are safe to call from multiple threads; state mutation is
    serialised on an internal re-entrant lock. Thread safety alone does not make
    ``check_order`` race-free for a caller (see module docstring) -- only
    ``reserve`` performs the check and the capacity claim atomically.
    """

    def __init__(self, cash_reserve_pct: float = 0.10):
        """
        Args:
            cash_reserve_pct: Minimum cash reserve as a fraction of NAV
                (default 10%). Strategy allocations may not sum above
                ``1 - cash_reserve_pct``, and the account-level pre-trade check
                enforces the same ceiling on actual committed exposure.

        Raises:
            AllocationError: if ``cash_reserve_pct`` is not a finite value in [0, 1).
        """
        if not _is_finite(cash_reserve_pct) or not 0.0 <= cash_reserve_pct < 1.0:
            raise AllocationError(
                f"Invalid cash_reserve_pct {cash_reserve_pct!r}. Must be a finite value in [0, 1)."
            )
        self.cash_reserve_pct = float(cash_reserve_pct)
        self.strategies: Dict[str, StrategyAllocation] = {}
        self._reservations: Dict[str, Reservation] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def max_investable_pct(self) -> float:
        """Fraction of NAV that may be deployed once the cash reserve is held back."""
        return 1.0 - self.cash_reserve_pct

    def register_strategy(self, strategy_name: str, max_allocation_pct: float) -> None:
        """Register a strategy with its maximum capital allocation percentage.

        The account-wide budget is enforced at registration time, so the
        allocator can never be left holding an over-allocated roster: the
        offending call raises and is not applied.

        Re-registering an existing name raises rather than silently resetting
        that strategy's tracked exposure to zero -- a config reload must not be
        able to zero live exposure and thereby unbind the cap. Use
        :meth:`update_allocation` to change a cap while preserving state.

        Raises:
            AllocationError: on an invalid percentage, a duplicate name, or a
                registration that would breach the investable budget.
        """
        if not isinstance(strategy_name, str) or not strategy_name:
            raise AllocationError(
                f"Strategy name must be a non-empty string, got {strategy_name!r}."
            )
        _validate_allocation_pct(strategy_name, max_allocation_pct)

        with self._lock:
            if strategy_name in self.strategies:
                raise AllocationError(
                    f"Strategy '{strategy_name}' is already registered "
                    f"(allocation {self.strategies[strategy_name].max_allocation_pct:.2%}). "
                    f"Use update_allocation() to change its cap without discarding "
                    f"tracked exposure."
                )
            self._assert_budget_fits(strategy_name, max_allocation_pct)
            self.strategies[strategy_name] = StrategyAllocation(
                strategy_name=strategy_name,
                max_allocation_pct=float(max_allocation_pct),
            )

    def update_allocation(self, strategy_name: str, max_allocation_pct: float) -> None:
        """Change a registered strategy's cap, preserving its tracked exposure.

        Raises:
            AllocationError: if the strategy is unknown, the percentage is
                invalid, or the new cap would breach the investable budget.
        """
        _validate_allocation_pct(strategy_name, max_allocation_pct)
        with self._lock:
            alloc = self._require_strategy(strategy_name)
            self._assert_budget_fits(strategy_name, max_allocation_pct)
            alloc.max_allocation_pct = float(max_allocation_pct)

    def validate_allocations(self) -> None:
        """Validate that total allocations + cash reserve don't exceed 100%.

        Registration and :meth:`update_allocation` already enforce this
        invariant, so this is a defence-in-depth re-check for callers that want
        an explicit gate (for example, right after loading a config file).

        Raises:
            AllocationError: if the roster is over-allocated.
        """
        with self._lock:
            total = sum(s.max_allocation_pct for s in self.strategies.values())
            if total > self.max_investable_pct + PCT_TOLERANCE:
                raise AllocationError(
                    f"Total strategy allocations ({total:.2%}) exceed investable capital "
                    f"({self.max_investable_pct:.2%} after {self.cash_reserve_pct:.2%} "
                    f"cash reserve)."
                )

    # ------------------------------------------------------------------
    # Exposure tracking
    # ------------------------------------------------------------------

    def update_exposure(self, strategy_name: str, current_exposure_usd: float) -> None:
        """Set the current settled GROSS mark-to-market exposure for a strategy.

        Args:
            current_exposure_usd: Non-negative gross notional. Netting longs
                against shorts here would manufacture headroom the account does
                not have (see module docstring).

        Raises:
            AllocationError: if the strategy is unknown, or the value is
                negative or non-finite.
        """
        if not _is_finite(current_exposure_usd):
            raise AllocationError(
                f"Non-finite exposure {current_exposure_usd!r} for '{strategy_name}'."
            )
        if current_exposure_usd < 0:
            raise AllocationError(
                f"Exposure must be gross (non-negative) notional; got "
                f"{current_exposure_usd} for '{strategy_name}'."
            )
        with self._lock:
            alloc = self._require_strategy(strategy_name)
            alloc.current_exposure_usd = float(current_exposure_usd)

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def check_order(
        self,
        strategy_name: str,
        order_value_usd: float,
        portfolio_nav: float,
    ) -> AllocationCheckResult:
        """
        Advisory pre-trade check: would this order fit within the strategy's
        capital allocation and the account-level investable ceiling?

        This does NOT consume capacity. Between this call and the order actually
        reaching the venue another order can claim the same headroom, so use
        :meth:`reserve` on the live order path and keep this for previews,
        sizing hints and reporting.

        Never raises on bad numeric input: invalid values are rejected (fail
        closed) so a malformed order cannot crash the trading loop, and cannot
        be waved through by NaN comparisons silently evaluating false.
        """
        with self._lock:
            return self._evaluate(strategy_name, order_value_usd, portfolio_nav)

    def reserve(
        self,
        strategy_name: str,
        order_value_usd: float,
        portfolio_nav: float,
        order_id: str,
    ) -> AllocationCheckResult:
        """
        Atomically check the order and, if approved, hold the capital against the
        strategy's cap until the order settles or is released.

        ``order_id`` should be the client order id used with the broker, which
        makes the reservation idempotent: re-reserving the same id (a retry
        after an ambiguous submission) returns the existing claim instead of
        double-counting the capital.

        Exposure-reducing orders (``order_value_usd <= 0``) are approved and
        recorded, but hold no capacity -- capital is only released once the
        reduction actually settles.

        Unlike :meth:`check_order`, the returned ``remaining_capacity_usd`` and
        ``pending_exposure_usd`` describe the state *after* this reservation, so
        a caller sizing a follow-up order does not double-spend the headroom
        this call just claimed.

        A retry can come back rejected if NAV moved against the account since
        the original reservation. That does not release the existing claim --
        the order may already be live, so reconcile with the broker rather than
        releasing on a rejection.

        Raises:
            AllocationError: if ``order_id`` is empty, or if it is already in
                use for a different strategy or a different amount (a genuine
                caller bug -- fail closed rather than guess which is correct).
        """
        if not isinstance(order_id, str) or not order_id:
            raise AllocationError(f"order_id must be a non-empty string, got {order_id!r}.")

        with self._lock:
            existing = self._reservations.get(order_id)
            if existing is not None:
                if existing.strategy_name != strategy_name or not _amounts_equal(
                    existing.requested_usd, order_value_usd
                ):
                    raise AllocationError(
                        f"order_id '{order_id}' is already reserved for strategy "
                        f"'{existing.strategy_name}' at {existing.requested_usd}; refusing "
                        f"to re-reserve it for '{strategy_name}' at {order_value_usd}."
                    )
                logger.info(
                    "Reservation '%s' already held for '%s' (%.2f); returning existing claim.",
                    order_id, strategy_name, existing.reserved_usd,
                )
                result = self._evaluate(
                    strategy_name,
                    order_value_usd,
                    portfolio_nav,
                    exclude_order_id=order_id,
                    order_id=order_id,
                )
                return _apply_reserved(result, existing.reserved_usd)

            result = self._evaluate(
                strategy_name, order_value_usd, portfolio_nav, order_id=order_id
            )
            if not result.approved:
                return result

            reserved = max(float(order_value_usd), 0.0)
            self._reservations[order_id] = Reservation(
                order_id=order_id,
                strategy_name=strategy_name,
                requested_usd=float(order_value_usd),
                reserved_usd=reserved,
            )
            self.strategies[strategy_name].pending_exposure_usd += reserved
            return _apply_reserved(result, reserved)

    def release_reservation(self, order_id: str) -> bool:
        """Release capital held for an order that was rejected, cancelled or expired.

        Idempotent: releasing an unknown or already-released id is a no-op that
        returns ``False``, so retry-safe cleanup paths need no bookkeeping.
        """
        with self._lock:
            reservation = self._reservations.get(order_id)
            if reservation is None:
                return False
            self._release_locked(reservation)
            return True

    def settle_reservation(
        self,
        order_id: str,
        filled_usd: Optional[float] = None,
        close: bool = True,
    ) -> bool:
        """Convert a reservation into settled exposure once the order fills.

        Args:
            order_id: The client order id passed to :meth:`reserve`.
            filled_usd: Signed change in gross notional actually filled.
                Defaults to the full reserved amount.
            close: ``True`` (default) releases the whole reservation -- use for
                a complete fill, or for a partial fill whose remainder was
                cancelled. ``False`` keeps the unfilled remainder reserved,
                which is what a partial fill on a still-working order needs.

        Returns:
            ``True`` if a reservation was found and applied, ``False`` otherwise
            (idempotent, like :meth:`release_reservation`).

        Raises:
            AllocationError: if ``filled_usd`` is non-finite.
        """
        if filled_usd is not None and not _is_finite(filled_usd):
            raise AllocationError(
                f"Non-finite filled_usd {filled_usd!r} for order '{order_id}'."
            )

        with self._lock:
            reservation = self._reservations.get(order_id)
            if reservation is None:
                return False

            applied = reservation.requested_usd if filled_usd is None else float(filled_usd)
            alloc = self.strategies.get(reservation.strategy_name)
            if alloc is not None:
                new_exposure = alloc.current_exposure_usd + applied
                if new_exposure < 0:
                    logger.warning(
                        "Settlement of order '%s' would drive '%s' exposure negative "
                        "(%.2f + %.2f); clamping to 0. Check the sign convention on "
                        "filled_usd -- it is a change in GROSS notional.",
                        order_id, reservation.strategy_name,
                        alloc.current_exposure_usd, applied,
                    )
                    new_exposure = 0.0
                alloc.current_exposure_usd = new_exposure

            if close:
                self._release_locked(reservation)
            else:
                consumed = min(max(applied, 0.0), reservation.reserved_usd)
                reservation.reserved_usd -= consumed
                # A fill can exceed its reservation (the notional estimate was
                # low, or the price moved). Clamp the remainder at zero rather
                # than letting it flip sign, which would make a later default
                # settle_reservation() move exposure the wrong way.
                remaining = reservation.requested_usd - applied
                if remaining * reservation.requested_usd < 0:
                    remaining = 0.0
                reservation.requested_usd = remaining
                if alloc is not None:
                    alloc.pending_exposure_usd = max(
                        alloc.pending_exposure_usd - consumed, 0.0
                    )
            return True

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_utilization_report(self, portfolio_nav: float) -> List[Dict[str, Any]]:
        """Returns a per-strategy utilization report.

        Raises:
            AllocationError: if ``portfolio_nav`` is not finite and positive --
                a report computed against a bad NAV is worse than no report.
        """
        _validate_nav(portfolio_nav)
        with self._lock:
            report: List[Dict[str, Any]] = []
            for name, alloc in self.strategies.items():
                max_usd = alloc.max_allocation_pct * portfolio_nav
                committed = alloc.committed_exposure_usd
                util_pct = (alloc.current_exposure_usd / max_usd) if max_usd > 0 else 0.0
                report.append({
                    "strategy": name,
                    "allocation_pct": alloc.max_allocation_pct,
                    "max_usd": max_usd,
                    "current_exposure_usd": alloc.current_exposure_usd,
                    "pending_exposure_usd": alloc.pending_exposure_usd,
                    "committed_exposure_usd": committed,
                    "utilization_pct": util_pct,
                    "remaining_usd": max(max_usd - committed, 0.0),
                    # utilization_pct falls back to 0.0 when the cap itself is 0,
                    # which would otherwise read as "unused" for a strategy that
                    # is in fact over cap -- hence the explicit flag.
                    "is_over_cap": committed > max_usd + AMOUNT_TOLERANCE_USD,
                })
            return report

    def get_portfolio_summary(self, portfolio_nav: float) -> Dict[str, Any]:
        """Account-level view of committed capital against the investable ceiling.

        Raises:
            AllocationError: if ``portfolio_nav`` is not finite and positive.
        """
        _validate_nav(portfolio_nav)
        with self._lock:
            investable = self.max_investable_pct * portfolio_nav
            exposure = sum(s.current_exposure_usd for s in self.strategies.values())
            pending = sum(s.pending_exposure_usd for s in self.strategies.values())
            committed = exposure + pending
            return {
                "portfolio_nav": portfolio_nav,
                "cash_reserve_pct": self.cash_reserve_pct,
                "investable_usd": investable,
                "allocated_pct": sum(
                    s.max_allocation_pct for s in self.strategies.values()
                ),
                "total_exposure_usd": exposure,
                "total_pending_usd": pending,
                "total_committed_usd": committed,
                "remaining_usd": max(investable - committed, 0.0),
                "open_reservations": len(self._reservations),
                "is_over_cap": committed > investable + AMOUNT_TOLERANCE_USD,
            }

    # ------------------------------------------------------------------
    # Internals (all called with self._lock held)
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        strategy_name: str,
        order_value_usd: float,
        portfolio_nav: float,
        exclude_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> AllocationCheckResult:
        """Shared pre-trade decision logic for check_order() and reserve()."""
        alloc = self.strategies.get(strategy_name)
        if alloc is None:
            return AllocationCheckResult(
                approved=False,
                strategy_name=strategy_name,
                requested_usd=order_value_usd if _is_finite(order_value_usd) else 0.0,
                current_exposure_usd=0.0,
                max_allowed_usd=0.0,
                remaining_capacity_usd=0.0,
                rejection_reason=f"Unregistered strategy: '{strategy_name}'",
                rejection_code=RejectionCode.UNKNOWN_STRATEGY,
                order_id=order_id,
            )

        # Fail closed on NaN/Inf. Without this, `projected > cap` evaluates False
        # for a NaN projection and the order is silently APPROVED.
        invalid = _describe_invalid_amounts(order_value_usd, portfolio_nav)
        if invalid is not None:
            reason = f"INVALID INPUT: {invalid} for strategy '{strategy_name}'. Order blocked."
            logger.warning(reason)
            return AllocationCheckResult(
                approved=False,
                strategy_name=strategy_name,
                requested_usd=order_value_usd if _is_finite(order_value_usd) else 0.0,
                current_exposure_usd=alloc.current_exposure_usd,
                max_allowed_usd=0.0,
                remaining_capacity_usd=0.0,
                rejection_reason=reason,
                rejection_code=RejectionCode.INVALID_INPUT,
                pending_exposure_usd=alloc.pending_exposure_usd,
                order_id=order_id,
            )

        # A re-reservation of an existing order id must not be charged twice.
        held = 0.0
        if exclude_order_id is not None:
            existing = self._reservations.get(exclude_order_id)
            if existing is not None:
                held = existing.reserved_usd

        pending = max(alloc.pending_exposure_usd - held, 0.0)
        max_allowed = alloc.max_allocation_pct * portfolio_nav
        committed = alloc.current_exposure_usd + pending
        # Exposure-reducing orders consume no capacity and are never vetoed.
        increment = max(float(order_value_usd), 0.0)
        projected = committed + increment
        remaining = max(max_allowed - committed, 0.0)

        result = AllocationCheckResult(
            approved=True,
            strategy_name=strategy_name,
            requested_usd=order_value_usd,
            current_exposure_usd=alloc.current_exposure_usd,
            max_allowed_usd=max_allowed,
            remaining_capacity_usd=remaining,
            pending_exposure_usd=pending,
            order_id=order_id,
        )

        if increment > 0 and projected > max_allowed + AMOUNT_TOLERANCE_USD:
            result.approved = False
            result.rejection_code = RejectionCode.STRATEGY_CAP
            result.rejection_reason = (
                f"ALLOCATION BREACH: Strategy '{strategy_name}' projected exposure "
                f"${projected:,.2f} (settled ${alloc.current_exposure_usd:,.2f} + in-flight "
                f"${pending:,.2f} + order ${increment:,.2f}) exceeds cap ${max_allowed:,.2f} "
                f"({alloc.max_allocation_pct:.0%} of ${portfolio_nav:,.2f} NAV). Order blocked."
            )
            logger.warning(result.rejection_reason)
            return result

        # Account-level ceiling. Per-strategy caps alone stop bounding the total
        # once mark-to-market drift pushes a strategy above its own cap, and the
        # cash reserve has to survive that drift.
        investable = self.max_investable_pct * portfolio_nav
        portfolio_committed = (
            sum(s.committed_exposure_usd for s in self.strategies.values()) - held
        )
        portfolio_projected = portfolio_committed + increment
        if increment > 0 and portfolio_projected > investable + AMOUNT_TOLERANCE_USD:
            result.approved = False
            result.rejection_code = RejectionCode.PORTFOLIO_CAP
            # Report the binding constraint, so a caller that downsizes to
            # remaining_capacity_usd is not immediately rejected again.
            result.remaining_capacity_usd = min(
                remaining, max(investable - portfolio_committed, 0.0)
            )
            result.rejection_reason = (
                f"PORTFOLIO ALLOCATION BREACH: account committed capital would reach "
                f"${portfolio_projected:,.2f}, above the investable ceiling "
                f"${investable:,.2f} ({self.max_investable_pct:.0%} of "
                f"${portfolio_nav:,.2f} NAV, holding a {self.cash_reserve_pct:.0%} cash "
                f"reserve). Order for '{strategy_name}' blocked."
            )
            logger.warning(result.rejection_reason)
            return result

        return result

    def _release_locked(self, reservation: Reservation) -> None:
        self._reservations.pop(reservation.order_id, None)
        alloc = self.strategies.get(reservation.strategy_name)
        if alloc is not None:
            alloc.pending_exposure_usd = max(
                alloc.pending_exposure_usd - reservation.reserved_usd, 0.0
            )

    def _require_strategy(self, strategy_name: str) -> StrategyAllocation:
        alloc = self.strategies.get(strategy_name)
        if alloc is None:
            raise AllocationError(f"Unknown strategy: '{strategy_name}'")
        return alloc

    def _assert_budget_fits(self, strategy_name: str, max_allocation_pct: float) -> None:
        """Reject a cap that would push the roster past the investable budget."""
        others = sum(
            s.max_allocation_pct
            for name, s in self.strategies.items()
            if name != strategy_name
        )
        total = others + max_allocation_pct
        if total > self.max_investable_pct + PCT_TOLERANCE:
            raise AllocationError(
                f"Allocating {max_allocation_pct:.2%} to '{strategy_name}' would bring total "
                f"strategy allocations to {total:.2%}, exceeding investable capital "
                f"({self.max_investable_pct:.2%} after {self.cash_reserve_pct:.2%} "
                f"cash reserve)."
            )


# ----------------------------------------------------------------------
# Module-level validation helpers
# ----------------------------------------------------------------------

def _is_finite(value: Any) -> bool:
    """True only for real numbers that are neither NaN nor infinite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _apply_reserved(
    result: AllocationCheckResult, reserved_usd: float
) -> AllocationCheckResult:
    """Restate a check result so it describes the state *after* the reservation."""
    result.pending_exposure_usd += reserved_usd
    result.remaining_capacity_usd = max(result.remaining_capacity_usd - reserved_usd, 0.0)
    return result


def _amounts_equal(a: float, b: float) -> bool:
    return _is_finite(a) and _is_finite(b) and abs(a - b) <= AMOUNT_TOLERANCE_USD


def _validate_allocation_pct(strategy_name: str, max_allocation_pct: float) -> None:
    if not _is_finite(max_allocation_pct) or max_allocation_pct <= 0 or max_allocation_pct > 1.0:
        raise AllocationError(
            f"Invalid allocation {max_allocation_pct!r} for '{strategy_name}'. "
            f"Must be a finite fraction in (0, 1.0]."
        )


def _validate_nav(portfolio_nav: float) -> None:
    if not _is_finite(portfolio_nav) or portfolio_nav <= 0:
        raise AllocationError(
            f"portfolio_nav must be a finite positive number, got {portfolio_nav!r}."
        )


def _describe_invalid_amounts(
    order_value_usd: float, portfolio_nav: float
) -> Optional[str]:
    """Returns a human-readable problem description, or None if both are usable."""
    if not _is_finite(order_value_usd):
        return f"non-finite order value {order_value_usd!r}"
    if not _is_finite(portfolio_nav):
        return f"non-finite portfolio NAV {portfolio_nav!r}"
    if portfolio_nav <= 0:
        return f"non-positive portfolio NAV {portfolio_nav!r}"
    return None
