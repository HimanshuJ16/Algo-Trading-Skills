"""
participation-of-volume-pov-execution — stateful Percentage-of-Volume (POV) scheduler.

Responsibilities
----------------
* Convert an observed stream of *away* market volume (volume traded by everyone
  except this parent order) into whole-share child-order quantities that track a
  target participation rate.
* Keep three quantities strictly separate — **scheduled**, **working** (sent, not
  yet resolved) and **filled** (confirmed) — so that the reported participation
  rate is computed from fills that actually happened, not from orders that were
  merely sent.
* Correct drift: each slice is derived from the *cumulative* target, so shares lost
  to flooring, to a paused thin-volume interval, or to an unfilled child order are
  recovered on later intervals instead of being silently abandoned.

Participation-rate convention
-----------------------------
FIX ``TargetStrategy(847) = 2`` defines a Participate order as one that aims "to be
x percent of the market volume". The market volume in that denominator *includes*
this order's own prints, so if ``V_away`` is the volume traded by everyone else::

    R = Q_own / (V_away + Q_own)   =>   Q_own = R / (1 - R) * V_away

Hence the ``R/(1-R)`` factor: at ``R = 0.15`` the algorithm must trade ~17.65% of
away volume in order to *be* 15% of total volume. Feeding consolidated-tape volume
(which already contains this order's own executions) in as ``V_away`` systematically
over-participates; see ``VolumeBasis``.

Scope / non-goals
-----------------
This module owns *scheduling and participation accounting only*. It does not place,
cancel, price or authenticate orders, it holds no locks, and it is **not thread-safe**.
Route every child order through the caller's idempotency and rate-limit layers
(``order-placement-idempotency``, ``multi-broker-rate-limit-handling``) and keep
pre-trade risk controls outside this class so a scheduling bug cannot disable them.

Quantity invariant
------------------
At all times, absent a broker over-fill::

    filled_qty + working_qty <= parent_order.total_qty

An over-fill (a child order filling more than it was sent for) is recorded truthfully
and surfaced as ``overfill_qty`` rather than being clamped away.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: Absolute tolerance applied before flooring a participation quantity. Without it,
#: rates with no exact binary representation floor one share low: at R = 1/3,
#: ``R/(1-R)`` evaluates to 0.49999999999999994, so 2 away shares would yield 0
#: rather than 1.
_FLOOR_TOL = 1e-9


class OrderSide(str, Enum):
    """Parent-order direction. Recorded for audit; it does not affect sizing."""

    BUY = "BUY"
    SELL = "SELL"


class VolumeBasis(str, Enum):
    """
    What ``process_volume_update`` is being handed.

    The POV arithmetic needs *away* volume. Passing consolidated volume while
    treating it as away volume is the most common way a POV algorithm silently
    over-participates.
    """

    #: Volume traded by everyone except this parent order. Preferred: the caller
    #: has the cleanest view of which prints are its own.
    AWAY = "AWAY"

    #: Consolidated tape volume, which already includes this order's executions.
    #: The engine subtracts the fills reported since the previous update. This is an
    #: approximation — a fill reported before its print reaches the tape is netted
    #: one interval early — and it carries any un-netted remainder forward.
    CONSOLIDATED = "CONSOLIDATED"


class POVStatus(str, Enum):
    """
    Outcome of one volume update.

    Subclasses ``str`` so comparisons against bare literals (``status == "EXECUTING"``)
    keep working.
    """

    EXECUTING = "EXECUTING"              # a child-order quantity was produced
    VOLUME_PAUSED = "VOLUME_PAUSED"      # away volume does not yet justify a slice
    AWAITING_FILLS = "AWAITING_FILLS"    # the whole remainder is already working
    RATE_CAPPED = "RATE_CAPPED"          # realized participation is above max_rate
    COMPLETED = "COMPLETED"              # parent quantity fully filled
    ENGINE_DISABLED = "ENGINE_DISABLED"  # config.enabled is False


@dataclass
class ParticipationOfVolumePovExecutionConfig:
    """Engine-level configuration, independent of any one parent order."""

    enabled: bool = True
    volume_basis: Union[VolumeBasis, str] = VolumeBasis.AWAY

    def __post_init__(self) -> None:
        self.volume_basis = VolumeBasis(self.volume_basis)


@dataclass
class POVParentOrder:
    """
    A parent order to be worked at a participation rate.

    ``target_rate`` and ``max_rate`` are fractions of **total** market volume (away
    volume plus this order's own prints), matching the FIX ``ParticipationRate(849)``
    convention.

    Raises:
        ValueError: on any economically meaningless configuration. Misconfiguration
            is raised rather than silently clamped — a target rate quietly rewritten
            to something else produces an execution nobody authorised.
    """

    symbol: str
    total_qty: int
    side: Union[OrderSide, str]
    target_rate: float = 0.15
    max_rate: float = 0.30
    min_slice_qty: int = 10
    max_slice_qty: int = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        self.side = OrderSide(self.side)

        for name in ("total_qty", "min_slice_qty", "max_slice_qty"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an int, got {type(value).__name__}")
        for name in ("target_rate", "max_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a float, got {type(value).__name__}")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite, got {value!r}")

        if self.total_qty <= 0:
            raise ValueError(f"total_qty must be positive, got {self.total_qty}")
        if not 0.0 < self.target_rate < 1.0:
            raise ValueError(
                f"target_rate must lie in (0, 1) exclusive, got {self.target_rate}. "
                "A rate of 1.0 would demand infinite volume; 0.0 never trades."
            )
        if not 0.0 < self.max_rate < 1.0:
            raise ValueError(f"max_rate must lie in (0, 1) exclusive, got {self.max_rate}")
        if self.target_rate > self.max_rate:
            raise ValueError(
                f"target_rate ({self.target_rate}) exceeds max_rate ({self.max_rate}). "
                "Lower the target or raise the cap explicitly; the engine will not clamp it."
            )
        if self.min_slice_qty < 1:
            raise ValueError(f"min_slice_qty must be >= 1, got {self.min_slice_qty}")
        if self.max_slice_qty < self.min_slice_qty:
            raise ValueError(
                f"max_slice_qty ({self.max_slice_qty}) is below min_slice_qty "
                f"({self.min_slice_qty}); no slice could ever satisfy both bounds."
            )


@dataclass
class POVExecutionReport:
    """Structured audit record emitted by every volume update."""

    symbol: str
    parent_qty: int
    filled_qty: int
    remaining_qty: int
    cum_market_volume: int          # cumulative AWAY volume consumed by the engine
    realized_participation_rate: float
    last_slice_qty: int
    status: POVStatus
    audit_notes: str
    working_qty: int = 0            # sent to the market, not yet filled or released
    cum_target_qty: int = 0         # cumulative quantity the target rate calls for
    overfill_qty: int = 0           # cumulative fills in excess of quantity sent


def away_target_quantity(rate: float, away_volume: int) -> int:
    """
    Whole shares to trade against ``away_volume`` in order to *be* ``rate`` of total
    volume: ``floor(rate / (1 - rate) * away_volume)``.

    Floored, so the target rate is an upper bound rather than something the
    algorithm rounds its way past.

    Raises:
        ValueError: if ``rate`` is outside (0, 1) or ``away_volume`` is negative.
    """
    if not 0.0 < rate < 1.0:
        raise ValueError(f"rate must lie in (0, 1) exclusive, got {rate}")
    if away_volume < 0:
        raise ValueError(f"away_volume must be non-negative, got {away_volume}")
    return int(math.floor((rate / (1.0 - rate)) * away_volume + _FLOOR_TOL))


class ParticipationOfVolumePovExecutionEngine:
    """
    Percentage-of-Volume child-order scheduler.

    Lifecycle per interval:

    1. ``process_volume_update(volume, last_price)`` -> ``(slice_qty, report)``.
       Any non-zero ``slice_qty`` is now **working**, not filled.
    2. Route the child order, then report its outcome with ``record_fill`` and/or
       ``record_unfilled``. Every share sent must eventually be accounted for by one
       of the two, or the engine keeps believing it is still in the market.

    Not thread-safe: serialise calls, or hold one engine per parent order per thread.
    """

    def __init__(
        self,
        config: Optional[ParticipationOfVolumePovExecutionConfig] = None,
        parent_order: Optional[POVParentOrder] = None,
    ) -> None:
        self.config = config or ParticipationOfVolumePovExecutionConfig()
        self.parent_order = parent_order or POVParentOrder("AAPL", 5000, OrderSide.BUY, 0.15)
        self.filled_qty = 0
        self.working_qty = 0
        self.overfill_qty = 0
        self.cum_away_vol = 0
        self.state: POVStatus | str = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []
        #: Fills reported since the last volume update, netted off consolidated tape
        #: volume when ``VolumeBasis.CONSOLIDATED`` is in force.
        self._own_prints_pending = 0

    # ------------------------------------------------------------------ accounting

    @property
    def remaining_qty(self) -> int:
        """Parent quantity neither filled nor currently working — schedulable now."""
        return max(0, self.parent_order.total_qty - self.filled_qty - self.working_qty)

    @property
    def unfilled_qty(self) -> int:
        """Parent quantity not yet filled, including quantity currently working."""
        return max(0, self.parent_order.total_qty - self.filled_qty)

    def realized_participation_rate(self) -> float:
        """
        Confirmed fills as a fraction of total volume: ``F / (V_away + F)``.

        Computed from fills only. Quantity that is merely working has not printed
        and must not be counted as participation.
        """
        denom = self.cum_away_vol + self.filled_qty
        if denom <= 0:
            return 0.0
        return self.filled_qty / float(denom)

    def _projected_participation_rate(self) -> float:
        """Participation if everything currently working filled at this volume."""
        executed_or_working = self.filled_qty + self.working_qty
        denom = self.cum_away_vol + executed_or_working
        if denom <= 0:
            return 0.0
        return executed_or_working / float(denom)

    def record_fill(self, qty: int, price: Optional[float] = None) -> None:
        """
        Confirm that ``qty`` shares filled against quantity previously sent.

        A fill larger than the outstanding working quantity is an over-fill. It is
        recorded truthfully — clamping it would hide a real position mismatch — and
        accumulated in ``overfill_qty``.

        Raises:
            ValueError: if ``qty`` is not a positive int, or ``price`` is supplied
                and is not finite and positive.
        """
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise ValueError(f"qty must be an int, got {type(qty).__name__}")
        if qty <= 0:
            raise ValueError(f"fill qty must be positive, got {qty}")
        if price is not None and (not math.isfinite(price) or price <= 0):
            raise ValueError(f"price must be finite and positive, got {price!r}")

        excess = max(0, qty - self.working_qty)
        if excess:
            self.overfill_qty += excess
            logger.warning(
                "POV OVERFILL [%s]: fill of %d exceeds working quantity %d by %d. "
                "Position and participation now differ from the schedule; reconcile "
                "with the broker before sending further slices.",
                self.parent_order.symbol, qty, self.working_qty, excess,
            )
        self.working_qty = max(0, self.working_qty - qty)
        self.filled_qty += qty
        self._own_prints_pending += qty

        if self.filled_qty > self.parent_order.total_qty:
            logger.warning(
                "POV PARENT OVERFILL [%s]: filled %d against a parent of %d.",
                self.parent_order.symbol, self.filled_qty, self.parent_order.total_qty,
            )

    def record_unfilled(self, qty: int, reason: str = "UNSPECIFIED") -> None:
        """
        Release ``qty`` working shares that will not fill — cancelled, expired, or
        rejected. The quantity returns to the schedule and is re-offered on later
        intervals as away volume permits.

        Raises:
            ValueError: if ``qty`` is not a positive int, or exceeds working quantity.
        """
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise ValueError(f"qty must be an int, got {type(qty).__name__}")
        if qty <= 0:
            raise ValueError(f"released qty must be positive, got {qty}")
        if qty > self.working_qty:
            raise ValueError(
                f"cannot release {qty} shares; only {self.working_qty} are working. "
                "The engine's view of the market has diverged from the broker's."
            )
        self.working_qty -= qty
        logger.info(
            "POV RELEASE [%s]: %d shares returned to schedule (%s).",
            self.parent_order.symbol, qty, reason,
        )

    # ------------------------------------------------------------------- scheduling

    def process_volume_update(
        self, market_interval_volume: int, last_price: float
    ) -> Tuple[int, POVExecutionReport]:
        """
        Consume one interval of market volume and return the next child-order quantity.

        Args:
            market_interval_volume: Volume traded during the interval, on the basis
                declared by ``config.volume_basis``. Must be a non-negative int — a
                negative value would corrupt the cumulative participation
                denominator, so it is rejected rather than absorbed.
            last_price: Last traded price. Recorded in the audit note only.

        Returns:
            ``(slice_qty, report)``. ``slice_qty`` is now working, **not** filled;
            report its outcome via ``record_fill`` / ``record_unfilled``.

        Raises:
            ValueError: on a negative, non-int, or non-finite input.
        """
        if isinstance(market_interval_volume, bool) or not isinstance(market_interval_volume, int):
            raise ValueError(
                "market_interval_volume must be an int, got "
                f"{type(market_interval_volume).__name__}"
            )
        if market_interval_volume < 0:
            raise ValueError(
                f"market_interval_volume must be non-negative, got {market_interval_volume}"
            )
        if (isinstance(last_price, bool) or not isinstance(last_price, (int, float))
                or not math.isfinite(float(last_price)) or last_price <= 0):
            raise ValueError(f"last_price must be finite and positive, got {last_price!r}")

        if not self.config.enabled:
            self.state = POVStatus.ENGINE_DISABLED
            return 0, self._build_report(0, POVStatus.ENGINE_DISABLED, "POV engine disabled.")

        order = self.parent_order

        # Checked *before* accumulating: once the parent is filled its realized
        # participation is a property of the window it actually traded in. Letting
        # later volume into the denominator would dilute a completed order's reported
        # rate towards zero for as long as the caller keeps sending updates.
        if self.filled_qty >= order.total_qty:
            self.state = POVStatus.COMPLETED
            return 0, self._build_report(0, POVStatus.COMPLETED, "Parent order fully filled.")

        away_volume = self._resolve_away_volume(market_interval_volume)
        self.cum_away_vol += away_volume
        cum_target = away_target_quantity(order.target_rate, self.cum_away_vol)

        available = self.remaining_qty
        if available <= 0:
            self.state = POVStatus.AWAITING_FILLS
            return 0, self._build_report(
                0, POVStatus.AWAITING_FILLS,
                f"Entire remainder ({self.working_qty}) is working; awaiting fills.",
                cum_target,
            )

        # Hard cumulative cap. The scheduled curve never exceeds target_rate, so this
        # binds only when realized participation has been pushed above the cap by
        # something the schedule does not control — a broker over-fill, or fills
        # reported against this parent from another source.
        projected = self._projected_participation_rate()
        if projected > order.max_rate:
            self.state = POVStatus.RATE_CAPPED
            return 0, self._build_report(
                0, POVStatus.RATE_CAPPED,
                f"Projected participation {projected:.2%} exceeds max_rate "
                f"{order.max_rate:.2%}; no further quantity scheduled.",
                cum_target,
            )

        # Drift-correcting slice: work from the cumulative target, not from this
        # interval alone, so flooring loss and paused intervals are recovered.
        deficit = cum_target - (self.filled_qty + self.working_qty)
        slice_qty = max(0, min(deficit, order.max_slice_qty, available))

        # Minimum-clip gate. Bypassed once the schedulable residual is itself below
        # the minimum, otherwise an odd-lot tail could never be sent at all.
        if 0 < slice_qty < order.min_slice_qty and available >= order.min_slice_qty:
            slice_qty = 0

        if slice_qty > 0:
            self.working_qty += slice_qty
            status = POVStatus.EXECUTING
        else:
            status = POVStatus.VOLUME_PAUSED

        self.state = status
        notes = (
            f"POV SLICE [{order.symbol} {order.side.value} - {status.value}]: "
            f"slice={slice_qty} @ {last_price:,.4f} (away vol={away_volume}, "
            f"cum away={self.cum_away_vol}, cum target={cum_target}). "
            f"filled={self.filled_qty}/{order.total_qty}, working={self.working_qty}, "
            f"realized={self.realized_participation_rate():.2%} "
            f"(target={order.target_rate:.2%}, cap={order.max_rate:.2%})."
        )
        logger.info(notes)
        return slice_qty, self._build_report(slice_qty, status, notes, cum_target)

    # ---------------------------------------------------------------------- helpers

    def _resolve_away_volume(self, interval_volume: int) -> int:
        """
        Reduce an interval's reported volume to away volume.

        Under ``CONSOLIDATED`` the engine's own fills since the previous update are
        netted off; any excess (a fill reported before its print reached the tape) is
        carried forward rather than discarded, so each own share is netted exactly once.
        """
        if self.config.volume_basis is VolumeBasis.AWAY:
            self._own_prints_pending = 0
            return interval_volume

        netted = min(self._own_prints_pending, interval_volume)
        self._own_prints_pending -= netted
        return interval_volume - netted

    def _build_report(
        self,
        slice_qty: int,
        status: POVStatus,
        notes: str,
        cum_target: Optional[int] = None,
    ) -> POVExecutionReport:
        return POVExecutionReport(
            symbol=self.parent_order.symbol,
            parent_qty=self.parent_order.total_qty,
            filled_qty=self.filled_qty,
            remaining_qty=self.unfilled_qty,
            cum_market_volume=self.cum_away_vol,
            realized_participation_rate=round(self.realized_participation_rate(), 6),
            last_slice_qty=slice_qty,
            status=status,
            audit_notes=notes,
            working_qty=self.working_qty,
            cum_target_qty=(
                cum_target
                if cum_target is not None
                else away_target_quantity(self.parent_order.target_rate, self.cum_away_vol)
            ),
            overfill_qty=self.overfill_qty,
        )
