"""
multi-account-same-strategy-fan-out: apportions one master order quantity across
multiple client sub-accounts and assigns deterministic, collision-free client order
IDs.

The apportionment uses the **largest-remainder (Hamilton) method**: each account's
exact entitlement ``Q_master * basis_i / sum(basis)`` is floored, and the shares left
over by flooring are handed to the accounts with the largest fractional remainders,
ties broken by ``account_id``. The defining property is::

    sum(order.allocated_quantity for order in report.account_orders)
        == report.master_target_qty          # whenever any account is eligible

Rounding each account independently -- ``round(Q * w_i)`` per account -- does *not*
have that property. Three equal-NAV accounts splitting 10 shares round to 3 each and
silently drop a share; seven equal accounts drop three of ten (30% of the signal).
That residual is a real un-traded position, so this module never rounds per account.

What this module does NOT do
----------------------------
- **It does not dispatch orders.** It returns instructions. Submission, retry
  classification and fill tracking belong to the broker adapter (see
  ``order-placement-idempotency``). Concurrency is therefore the caller's concern.
- **It does not equalize fill prices.** Dispatching N separate per-account orders
  yields N different fills, so one client is filled better than another by luck of
  the matching engine -- concurrency narrows the latency skew but cannot remove the
  price dispersion. The mechanism that removes it is the opposite pattern: place one
  bunched order and allocate the fills post-execution at a single average price
  (CFTC 17 CFR 1.35(b)(5); CME Rule 553 Average Price System; IBKR FA allocation
  groups, where one order is sent and the broker allocates). Use this module when
  per-account orders are genuinely required -- separate broker connections, separate
  venues, no bunched-order facility -- not as a substitute for average pricing.
- **It does not know positions.** ``nav_usd`` sizes an *entry*. Allocating an exit by
  NAV over-sells accounts holding less than their NAV share and under-sells the rest.
  To close positions, pass each account's held quantity as ``allocation_weight`` and
  use ``ALLOCATION_METHOD_WEIGHT``.
- **It does not enforce buying power, margin, or restricted lists.** Those are
  pre-trade risk controls and must sit outside the allocator.

Fairness note (verified, and deliberately left to the caller)
-------------------------------------------------------------
17 CFR 1.35(b)(5)(iv)(B) requires that allocations be fair and equitable and that no
account or group of accounts receive consistently favorable or unfavorable treatment.
Largest-remainder is fair *within a single batch*, but the remainder shares are not
randomised: given stable NAVs the same accounts tend to win them batch after batch.
``AccountOrder.received_remainder_share`` is recorded precisely so that bias is
auditable and a rotation policy can be layered on top; no rotation is imposed here,
because the right policy is a firm-level compliance decision, not a library default.
"""
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Apportion by each account's Net Asset Value.
ALLOCATION_METHOD_NAV = "PRO_RATA_NAV"

#: Apportion by an explicit per-account weight (target mandate weight, or held
#: quantity when unwinding). Every active account must carry ``allocation_weight``.
ALLOCATION_METHOD_WEIGHT = "EXPLICIT_WEIGHT"

VALID_ALLOCATION_METHODS = (ALLOCATION_METHOD_NAV, ALLOCATION_METHOD_WEIGHT)

VALID_ACTIONS = ("BUY", "SELL")
VALID_ORDER_TYPES = ("MARKET", "LIMIT")

#: Reasons an active account can end up with no order in a batch.
EXCLUDED_ZERO_ENTITLEMENT = "ZERO_ENTITLEMENT"
EXCLUDED_BELOW_MIN_ORDER_QTY = "BELOW_MIN_ORDER_QTY"


@dataclass
class ClientAccount:
    account_id: str
    nav_usd: float
    is_active: bool = True
    allocation_weight: Optional[float] = None


@dataclass
class AccountOrder:
    client_order_id: str
    account_id: str
    symbol: str
    action: str  # "BUY" or "SELL"
    allocated_quantity: int
    order_type: str = "MARKET"
    limit_price: Optional[float] = None
    # Audit trail: the inputs that produced allocated_quantity. 17 CFR 1.35(b)(5)(iv)(C)
    # requires an allocation method objective enough to permit independent verification.
    allocation_basis: float = 0.0        # NAV or explicit weight used as the numerator
    allocation_weight: float = 0.0       # basis / total_basis
    exact_quantity: float = 0.0          # entitlement before flooring
    received_remainder_share: bool = False


@dataclass
class ExcludedAccount:
    account_id: str
    reason: str
    exact_quantity: float


@dataclass
class FanOutReport:
    master_symbol: str
    master_action: str
    master_target_qty: int
    total_allocated_qty: int
    account_orders: List[AccountOrder]
    batch_id: str = ""
    allocation_method: str = ALLOCATION_METHOD_NAV
    total_basis: float = 0.0
    #: Active accounts that received no order this batch, with the reason why.
    excluded_accounts: List[ExcludedAccount] = field(default_factory=list)
    #: Epoch milliseconds at which the allocation was computed.
    allocated_at_ms: int = 0

    @property
    def is_fully_allocated(self) -> bool:
        """True when every share of the master signal was assigned to an account."""
        return self.total_allocated_qty == self.master_target_qty


def _validate_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}.")
    if any(ch.isspace() for ch in value):
        raise ValueError(f"{label} must not contain whitespace, got {value!r}.")
    return value


def _validate_positive_finite(value: Optional[float], label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    if numeric <= 0.0:
        raise ValueError(f"{label} must be > 0, got {value!r}.")
    return numeric


def apportion_largest_remainder(
    bases: Dict[str, float],
    total_quantity: int,
) -> Tuple[Dict[str, int], Dict[str, float], List[str]]:
    """
    Largest-remainder (Hamilton) apportionment of ``total_quantity`` over ``bases``.

    Returns ``(allocations, exact_entitlements, remainder_winners)``. The allocations
    always sum to ``total_quantity`` exactly -- that is the whole point of the method
    and the reason per-account ``round()`` is not used.

    Ties on the fractional remainder are broken by ``account_id`` ascending, so the
    result is deterministic and independently reproducible from the report's recorded
    bases (17 CFR 1.35(b)(5)(iv)(C)). Python's ``round()`` would additionally apply
    banker's rounding -- ``round(2.5) == 2`` but ``round(3.5) == 4`` -- which makes
    two identically-entitled accounts receive different quantities.
    """
    if total_quantity < 0:
        raise ValueError(f"total_quantity must be >= 0, got {total_quantity}.")
    if not bases:
        return {}, {}, []

    if any(not math.isfinite(basis) or basis <= 0.0 for basis in bases.values()):
        raise ValueError(f"Every allocation basis must be finite and > 0, got {bases}.")

    # Entitlements are computed as exact rationals over the *true* values of the float
    # bases, then floored. Doing this in floating point misfloors entitlements that are
    # mathematically integers: bases 1/20/29 over 100 shares entitle the third account
    # to exactly 58, but 100 * (29.0 / 50.0) evaluates to 57.99999999999999 and floors
    # to 57. Largest-remainder usually hands that share straight back, but not reliably
    # once several accounts are affected, and the recovered share is then misreported as
    # a remainder award. Fraction removes the failure mode outright; n is the number of
    # sub-accounts, so the cost is irrelevant.
    exact_ratio = {acc: Fraction(basis) for acc, basis in bases.items()}
    total_basis = sum(exact_ratio.values(), Fraction(0))
    if total_basis <= 0:
        raise ValueError(f"Sum of allocation bases must be > 0, got {float(total_basis)}.")

    entitlement = {
        acc: Fraction(total_quantity) * basis / total_basis
        for acc, basis in exact_ratio.items()
    }
    allocations = {acc: value.numerator // value.denominator for acc, value in entitlement.items()}
    exact = {acc: float(value) for acc, value in entitlement.items()}

    shares_left = total_quantity - sum(allocations.values())
    # Flooring n exact entitlements strands at most n - 1 units.
    if not 0 <= shares_left < max(1, len(bases)):
        raise AssertionError(f"Largest-remainder invariant broken: {shares_left} left over.")

    ranked = sorted(bases, key=lambda acc: (-(entitlement[acc] - allocations[acc]), acc))
    winners = ranked[:shares_left]
    for acc in winners:
        allocations[acc] += 1

    return allocations, exact, winners


class MultiAccountStrategyFanOut:
    """
    Apportions a master order quantity across client sub-accounts.

    Thread-safe: the account registry is guarded by a re-entrant lock and each batch
    computes from a snapshot taken under that lock, so a registration landing
    mid-batch cannot produce a half-updated allocation.

    Client order IDs are ``{prefix}_{batch_id}_{account_id}`` and are a pure function
    of the batch: re-running a batch with the same ``batch_id`` after an ambiguous
    timeout reproduces byte-identical IDs, which is what lets the broker reject the
    duplicate rather than double-executing (see ``order-placement-idempotency``). A
    fresh ``batch_id`` is generated per call when none is supplied.
    """

    def __init__(
        self,
        min_order_qty: int = 1,
        allocation_method: str = ALLOCATION_METHOD_NAV,
        client_order_id_prefix: str = "CLORD",
    ) -> None:
        if isinstance(min_order_qty, bool) or not isinstance(min_order_qty, int):
            raise ValueError(f"min_order_qty must be an int, got {min_order_qty!r}.")
        if min_order_qty < 1:
            raise ValueError(f"min_order_qty must be >= 1, got {min_order_qty}.")
        if allocation_method not in VALID_ALLOCATION_METHODS:
            raise ValueError(
                f"allocation_method must be one of {VALID_ALLOCATION_METHODS}, "
                f"got {allocation_method!r}.")
        _validate_identifier(client_order_id_prefix, "client_order_id_prefix")

        self.accounts: Dict[str, ClientAccount] = {}
        self.min_order_qty = min_order_qty
        self.allocation_method = allocation_method
        self.client_order_id_prefix = client_order_id_prefix
        self._lock = threading.RLock()

    def register_account(
        self,
        account_id: str,
        nav_usd: float,
        allocation_weight: Optional[float] = None,
    ) -> None:
        """
        Registers a client sub-account.

        Raises on a duplicate ``account_id`` rather than overwriting: a silent
        overwrite turns a config-loading bug into a wrong allocation that nothing
        reports. Use ``update_account_nav`` to refresh a NAV.
        """
        _validate_identifier(account_id, "account_id")
        nav = _validate_positive_finite(nav_usd, f"nav_usd for account {account_id}")
        weight = (
            None if allocation_weight is None
            else _validate_positive_finite(
                allocation_weight, f"allocation_weight for account {account_id}")
        )
        with self._lock:
            if account_id in self.accounts:
                raise ValueError(
                    f"Account {account_id} is already registered; use update_account_nav().")
            self.accounts[account_id] = ClientAccount(
                account_id=account_id, nav_usd=nav, allocation_weight=weight)

    def update_account_nav(
        self,
        account_id: str,
        nav_usd: float,
        allocation_weight: Optional[float] = None,
    ) -> None:
        """Refreshes a registered account's NAV (and optionally its explicit weight)."""
        nav = _validate_positive_finite(nav_usd, f"nav_usd for account {account_id}")
        weight = (
            None if allocation_weight is None
            else _validate_positive_finite(
                allocation_weight, f"allocation_weight for account {account_id}")
        )
        with self._lock:
            account = self.accounts.get(account_id)
            if account is None:
                raise KeyError(f"Account {account_id} is not registered.")
            account.nav_usd = nav
            if weight is not None:
                account.allocation_weight = weight

    def set_account_active(self, account_id: str, is_active: bool) -> None:
        """
        Suspends or resumes an account.

        A suspended account is removed from the denominator entirely, so the master
        quantity is redistributed across the remainder rather than being under-filled.
        """
        with self._lock:
            account = self.accounts.get(account_id)
            if account is None:
                raise KeyError(f"Account {account_id} is not registered.")
            account.is_active = bool(is_active)

    def _new_batch_id(self) -> str:
        # Millisecond timestamp for human-orderable IDs, plus random entropy so two
        # processes (or a restarted one) cannot mint the same batch in the same ms.
        return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    def _client_order_id(self, batch_id: str, account_id: str) -> str:
        return f"{self.client_order_id_prefix}_{batch_id}_{account_id}"

    def _allocation_bases(self, accounts: List[ClientAccount]) -> Dict[str, float]:
        if self.allocation_method not in VALID_ALLOCATION_METHODS:
            raise ValueError(
                f"allocation_method must be one of {VALID_ALLOCATION_METHODS}, "
                f"got {self.allocation_method!r}.")
        if self.allocation_method == ALLOCATION_METHOD_WEIGHT:
            missing = [a.account_id for a in accounts if a.allocation_weight is None]
            if missing:
                raise ValueError(
                    f"allocation_method={ALLOCATION_METHOD_WEIGHT} requires an "
                    f"allocation_weight on every active account; missing for {missing}.")
            return {a.account_id: float(a.allocation_weight) for a in accounts}
        return {a.account_id: a.nav_usd for a in accounts}

    def calculate_fanout_orders(
        self,
        symbol: str,
        action: str,
        total_target_quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        batch_id: Optional[str] = None,
    ) -> FanOutReport:
        """
        Apportions ``total_target_quantity`` across the active sub-accounts.

        The returned quantities sum to ``total_target_quantity`` exactly whenever at
        least one account clears ``min_order_qty``; check
        ``FanOutReport.is_fully_allocated`` before dispatching, and inspect
        ``excluded_accounts`` for the accounts that received nothing and why.
        """
        _validate_identifier(symbol, "symbol")
        if action not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {VALID_ACTIONS}, got {action!r}.")
        if order_type not in VALID_ORDER_TYPES:
            raise ValueError(
                f"order_type must be one of {VALID_ORDER_TYPES}, got {order_type!r}.")
        if isinstance(total_target_quantity, bool) or not isinstance(total_target_quantity, int):
            raise ValueError(
                f"total_target_quantity must be an int, got {total_target_quantity!r}. "
                "Encode direction with `action`, never with a negative quantity.")
        if total_target_quantity <= 0:
            raise ValueError(
                f"total_target_quantity must be > 0, got {total_target_quantity}.")
        if order_type == "LIMIT":
            _validate_positive_finite(limit_price, "limit_price for a LIMIT order")
        elif limit_price is not None:
            raise ValueError(
                f"limit_price must be None for a {order_type} order, got {limit_price!r}.")
        if batch_id is None:
            batch_id = self._new_batch_id()
        else:
            _validate_identifier(batch_id, "batch_id")
            if "_" in batch_id:
                # `_` is the client_order_id field separator; allowing it here would
                # make {prefix}_{batch}_{account} ambiguous to parse back apart.
                raise ValueError(f"batch_id must not contain '_', got {batch_id!r}.")

        with self._lock:
            active = sorted(
                (ClientAccount(a.account_id, a.nav_usd, a.is_active, a.allocation_weight)
                 for a in self.accounts.values() if a.is_active),
                key=lambda a: a.account_id,
            )

        now_ms = int(time.time() * 1000)
        if not active:
            logger.warning(
                "Fan-out %s: no active accounts for %s %s %s; nothing allocated.",
                batch_id, action, total_target_quantity, symbol)
            return FanOutReport(
                master_symbol=symbol,
                master_action=action,
                master_target_qty=total_target_quantity,
                total_allocated_qty=0,
                account_orders=[],
                batch_id=batch_id,
                allocation_method=self.allocation_method,
                allocated_at_ms=now_ms,
            )

        bases = self._allocation_bases(active)
        total_basis = math.fsum(bases.values())

        # Accounts entitled to a non-zero but sub-minimum quantity are dropped and the
        # freed shares are re-apportioned across the survivors. The floor is never used
        # to *raise* an allocation: `max(min_order_qty, share)` would mint quantity the
        # master signal never authorised (50 accounts x a 1-share floor turns a 10-share
        # signal into 50 shares) and, on a SELL, would open a short in an account whose
        # fair share was zero.
        eligible = dict(bases)
        excluded: List[ExcludedAccount] = []
        allocations: Dict[str, int] = {}
        exact: Dict[str, float] = {}
        winners: List[str] = []
        while eligible:
            allocations, exact, winners = apportion_largest_remainder(
                eligible, total_target_quantity)
            below_floor = sorted(
                acc for acc, qty in allocations.items()
                if 0 < qty < self.min_order_qty
            )
            if not below_floor:
                break
            for acc in below_floor:
                excluded.append(
                    ExcludedAccount(acc, EXCLUDED_BELOW_MIN_ORDER_QTY, exact[acc]))
                del eligible[acc]
        else:
            # Every account fell below the floor; nothing can be traded this batch.
            allocations, exact, winners = {}, {}, []

        winner_set = set(winners)
        account_orders: List[AccountOrder] = []
        allocated_sum = 0
        for acc in sorted(allocations):
            quantity = allocations[acc]
            if quantity == 0:
                excluded.append(ExcludedAccount(acc, EXCLUDED_ZERO_ENTITLEMENT, exact[acc]))
                continue
            account_orders.append(AccountOrder(
                client_order_id=self._client_order_id(batch_id, acc),
                account_id=acc,
                symbol=symbol,
                action=action,
                allocated_quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                allocation_basis=bases[acc],
                allocation_weight=bases[acc] / total_basis,
                exact_quantity=exact[acc],
                received_remainder_share=acc in winner_set,
            ))
            allocated_sum += quantity

        report = FanOutReport(
            master_symbol=symbol,
            master_action=action,
            master_target_qty=total_target_quantity,
            total_allocated_qty=allocated_sum,
            account_orders=account_orders,
            batch_id=batch_id,
            allocation_method=self.allocation_method,
            total_basis=total_basis,
            excluded_accounts=sorted(excluded, key=lambda e: e.account_id),
            allocated_at_ms=now_ms,
        )

        if report.is_fully_allocated:
            logger.info(
                "Fan-out %s: %s %s %s apportioned across %s/%s accounts by %s.",
                batch_id, action, total_target_quantity, symbol,
                len(account_orders), len(active), self.allocation_method)
        else:
            logger.warning(
                "Fan-out %s: %s %s %s -- only %s shares allocated across %s accounts "
                "(%s excluded, min_order_qty=%s). The shortfall was NOT traded.",
                batch_id, action, total_target_quantity, symbol,
                allocated_sum, len(account_orders), len(excluded), self.min_order_qty)

        return report
