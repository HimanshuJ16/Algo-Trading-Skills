"""
execution-algo-twap-vwap-slicing — stateful TWAP/VWAP parent-order slicing engine.

Responsibilities
----------------
* Build a child-order schedule (TWAP time-uniform, or VWAP weighted by a volume curve)
  that is **lot-aligned**, **non-negative**, and **exactly quantity-conserving**.
* Track the child-order lifecycle (fill / partial-expiry / rejection / cancellation)
  and re-allocate the released residual according to an explicit catch-up policy.
* Produce a side-aware post-trade report: achieved VWAP, side-adjusted slippage in
  basis points, and — when a decision price is supplied — the opportunity cost of the
  unfilled remainder and the combined implementation shortfall (Perold, 1988).

Scope / non-goals
-----------------
This module owns *scheduling and accounting only*. It does not place, cancel, or
authenticate orders, and it holds no locks: it is **not** thread-safe. Route every
child order through the caller's idempotency and rate-limit layers
(`order-placement-idempotency`, `multi-broker-rate-limit-handling`), and keep the
pre-trade risk checks that gate order entry outside this class so that a scheduling
bug cannot disable them.

Quantity invariant
------------------
At all times::

    sum(slice.target_qty for slice in slices) + unassigned_qty == total_qty

`unassigned_qty` is quantity released from a closed child order but not re-allocated
(because the policy declined to catch up, or because no schedulable interval remains).
It is the quantity the parent order is on course to leave unfilled.
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Absolute tolerance for float comparisons on quantities.
_QTY_TOL = 1e-9


class SlicerType(str, Enum):
    """Benchmark the schedule is built to track."""

    TWAP = "TWAP"
    VWAP = "VWAP"


class OrderSide(str, Enum):
    """Parent-order direction. Required to sign the reported slippage correctly."""

    BUY = "BUY"
    SELL = "SELL"


class SliceStatus(str, Enum):
    """
    Child-order lifecycle state.

    Subclasses ``str`` so existing comparisons against bare string literals
    (``slice.status == "PENDING"``) keep working.
    """

    PENDING = "PENDING"      # open; still re-targetable by the catch-up policy
    PARTIAL = "PARTIAL"      # closed with a partial fill; residual released
    FILLED = "FILLED"        # closed, target met
    REJECTED = "REJECTED"    # broker rejected the child order; residual released
    CANCELLED = "CANCELLED"  # abandoned by the give-up policy, or cancelled by caller


#: States in which a slice may still be re-sized by the catch-up policy.
_OPEN_STATES = frozenset({SliceStatus.PENDING})


class CatchUpPolicy(str, Enum):
    """
    What to do with quantity released by a partially-filled, rejected, or cancelled
    child order. A deliberate execution trade-off, not an implementation detail —
    see SKILL.md workflow step 6.
    """

    #: Re-allocate the residual across the remaining open slices, pro-rata to their
    #: existing targets (preserving the TWAP/VWAP shape). Completes the parent order
    #: at the cost of larger clips later, i.e. more market impact.
    AGGRESSIVE_CATCHUP = "AGGRESSIVE_CATCHUP"

    #: Leave the remaining schedule untouched. The residual stays unassigned and the
    #: parent order under-completes. Caps impact; accepts execution risk.
    PASSIVE_CONTINUE = "PASSIVE_CONTINUE"

    #: Re-allocate as AGGRESSIVE_CATCHUP, but only into slices scheduled strictly
    #: before ``deadline``. Slices at or after the deadline are cancelled and their
    #: quantity abandoned.
    GIVE_UP_AT_DEADLINE = "GIVE_UP_AT_DEADLINE"


@dataclass
class ChildOrderSlice:
    """One scheduled child order."""

    slice_id: int
    target_qty: float
    target_time: float
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    status: SliceStatus = SliceStatus.PENDING
    reject_reason: Optional[str] = None

    @property
    def residual_qty(self) -> float:
        """Target quantity not yet filled on this child order."""
        return max(0.0, self.target_qty - self.filled_qty)


@dataclass
class ExecutionReport:
    """
    Post-trade execution quality report.

    Sign convention — stated explicitly because TCA vendors differ: ``slippage_bps``,
    ``opportunity_cost_bps`` and ``implementation_shortfall_bps`` are **costs**.
    Positive means worse than the benchmark *for the parent order's side*. A buy filled
    above the benchmark and a sell filled below it both report a positive number.
    """

    algo_type: SlicerType
    total_requested: float
    total_filled: float
    completion_pct: float
    vwap_achieved_price: float
    benchmark_price: float
    slippage_bps: float
    child_slices_count: int
    # --- added in 2.0.0 ---
    side: OrderSide = OrderSide.BUY
    unfilled_qty: float = 0.0
    notional_filled: float = 0.0
    overfill_qty: float = 0.0
    status_counts: Dict[str, int] = field(default_factory=dict)
    quantity_invariant_ok: bool = True
    #: None unless a decision/final price was supplied to `get_execution_report`.
    opportunity_cost_bps: Optional[float] = None
    implementation_shortfall_bps: Optional[float] = None


# ---------------------------------------------------------------------------
# Schedule construction
# ---------------------------------------------------------------------------
def _validate_positive_finite(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    return numeric


def allocate_lots(
    total_qty: float,
    weights: Sequence[float],
    lot_size: float = 1.0,
    jitter_pct: float = 0.0,
    rng: Optional[random.Random] = None,
) -> List[float]:
    """
    Split ``total_qty`` across ``weights`` in whole multiples of ``lot_size``.

    Uses largest-remainder (Hamilton) apportionment on integer lot counts, which
    guarantees three properties a naive ``round()``-then-patch-the-last-slice does not:

    * ``sum(result) == total_qty`` exactly — no accumulated drift dumped on one slice;
    * every element is non-negative — patching the residual onto the last slice can
      drive it negative, which downstream reads as an order on the opposite side;
    * fractional instruments work — a 0.5 BTC parent does not round to an all-zero
      schedule that silently executes nothing.

    Raises:
        ValueError: on non-finite/non-positive inputs, a negative or all-zero weight
            vector, an out-of-range ``jitter_pct``, or a ``total_qty`` that is not a
            whole multiple of ``lot_size``.
    """
    total_qty = _validate_positive_finite(total_qty, "total_qty")
    lot_size = _validate_positive_finite(lot_size, "lot_size")

    if len(weights) == 0:
        raise ValueError("weights must contain at least one element")
    clean_weights: List[float] = []
    for index, weight in enumerate(weights):
        numeric = float(weight)
        if not math.isfinite(numeric):
            raise ValueError(f"weights[{index}] is not finite: {weight!r}")
        if numeric < 0.0:
            raise ValueError(
                f"weights[{index}] is negative ({numeric}); a volume curve cannot "
                "instruct the algorithm to trade in reverse"
            )
        clean_weights.append(numeric)
    weight_sum = math.fsum(clean_weights)
    if weight_sum <= 0.0:
        raise ValueError("weights sum to zero; cannot distribute quantity")

    if not 0.0 <= jitter_pct < 1.0:
        raise ValueError(
            "jitter_pct must be in [0.0, 1.0) so a jittered weight cannot go "
            f"negative, got {jitter_pct!r}"
        )

    total_lots_float = total_qty / lot_size
    total_lots = int(round(total_lots_float))
    if abs(total_lots_float - total_lots) > 1e-6 or total_lots <= 0:
        raise ValueError(
            f"total_qty ({total_qty}) is not a positive whole multiple of lot_size "
            f"({lot_size}); round the parent order to a tradable size before slicing "
            "rather than letting the schedule silently drop a partial lot"
        )

    if jitter_pct > 0.0:
        generator = rng if rng is not None else random
        jittered = [
            w * (1.0 + generator.uniform(-jitter_pct, jitter_pct)) for w in clean_weights
        ]
        jittered_sum = math.fsum(jittered)
        # Only adopt the jittered vector if it is still usable; `weight_sum > 0`
        # already rules out the degenerate case, but never divide by ~0.
        if jittered_sum > 0.0:
            clean_weights = jittered
            weight_sum = jittered_sum

    exact = [total_lots * (w / weight_sum) for w in clean_weights]
    floors = [int(math.floor(value)) for value in exact]
    shortfall = total_lots - sum(floors)
    # Hand the leftover lots to the largest fractional remainders. Ties break by
    # index, so the allocation is deterministic for a given weight vector.
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:shortfall]:
        floors[i] += 1

    return [count * lot_size for count in floors]


def twap_schedule(
    total_qty: float,
    num_intervals: int,
    jitter_pct: float = 0.15,
    lot_size: float = 1.0,
    rng: Optional[random.Random] = None,
) -> List[float]:
    """Time-uniform child sizes: every interval carries the same weight."""
    if not isinstance(num_intervals, int) or num_intervals <= 0:
        raise ValueError(f"num_intervals must be a positive int, got {num_intervals!r}")
    return allocate_lots(total_qty, [1.0] * num_intervals, lot_size, jitter_pct, rng)


def vwap_schedule(
    total_qty: float,
    historical_volume_curve: Sequence[float],
    jitter_pct: float = 0.15,
    lot_size: float = 1.0,
    rng: Optional[random.Random] = None,
) -> List[float]:
    """
    Volume-weighted child sizes.

    ``historical_volume_curve`` holds each interval's share of expected window volume.
    It need not sum to 1.0 — it is normalised — but it must be non-negative, since a
    negative weight would emit a child order on the opposite side.
    """
    return allocate_lots(
        total_qty, list(historical_volume_curve), lot_size, jitter_pct, rng
    )


# ---------------------------------------------------------------------------
# Stateful slicer
# ---------------------------------------------------------------------------
class ExecutionSlicer:
    """
    Stateful parent-order slicer.

    Not thread-safe: drive it from a single event loop, or guard it with the caller's
    own lock. Every mutating method preserves the quantity invariant documented at
    module level; `quantity_invariant_ok()` re-checks it.
    """

    def __init__(
        self,
        total_qty: float,
        algo_type: SlicerType = SlicerType.TWAP,
        num_intervals: int = 10,
        interval_seconds: float = 60.0,
        historical_volume_curve: Optional[Sequence[float]] = None,
        jitter_pct: float = 0.15,
        catch_up_policy: CatchUpPolicy = CatchUpPolicy.PASSIVE_CONTINUE,
        start_time: Optional[float] = None,
        side: OrderSide = OrderSide.BUY,
        lot_size: float = 1.0,
        seed: Optional[int] = None,
        deadline: Optional[float] = None,
        max_child_multiple: Optional[float] = None,
    ):
        """
        Args:
            total_qty: Parent order quantity. Must be a positive whole multiple of
                ``lot_size``.
            num_intervals: Number of child orders. For VWAP it must equal the length of
                ``historical_volume_curve`` — the curve does not silently override it.
            interval_seconds: Nominal spacing between child orders.
            jitter_pct: Fractional jitter applied to both child size and child timing.
                Bounded to ``[0, 0.5)`` so jittered timestamps cannot reorder.
            catch_up_policy: See :class:`CatchUpPolicy`.
            start_time: Epoch seconds; defaults to ``time.time()``. ``0.0`` is honoured.
            side: Parent order direction; determines the sign of reported slippage.
            lot_size: Minimum tradable increment. Child sizes are whole multiples of it.
            seed: Seeds a slicer-local RNG. Supply it for reproducible backtests. The
                module never reads or mutates the process-wide ``random`` state.
            deadline: Epoch seconds, required semantics for ``GIVE_UP_AT_DEADLINE``;
                defaults to the end of the schedule window.
            max_child_multiple: Cap on how far catch-up may grow one child order,
                as a multiple of that slice's *originally scheduled* size. ``None``
                leaves catch-up uncapped, which lets a single late clip absorb the
                whole residual — the market-impact event this skill exists to avoid.
                Set it whenever ``catch_up_policy`` is not ``PASSIVE_CONTINUE``.

        Raises:
            ValueError: on any invalid parameter. Nothing is silently coerced — a
                mis-parameterised execution algorithm should fail at construction, not
                halfway through a live parent order.
        """
        if not isinstance(num_intervals, int) or num_intervals <= 0:
            raise ValueError(
                f"num_intervals must be a positive int, got {num_intervals!r}"
            )
        self.interval_seconds = _validate_positive_finite(
            interval_seconds, "interval_seconds"
        )
        if not 0.0 <= jitter_pct < 0.5:
            raise ValueError(
                "jitter_pct must be in [0.0, 0.5) — at 0.5 and above the timing jitter "
                "of consecutive intervals overlaps and child orders can be scheduled "
                f"out of order; got {jitter_pct!r}"
            )
        self.algo_type = SlicerType(algo_type)
        self.side = OrderSide(side)
        self.catch_up_policy = CatchUpPolicy(catch_up_policy)
        self.lot_size = _validate_positive_finite(lot_size, "lot_size")
        self.total_qty = _validate_positive_finite(total_qty, "total_qty")
        self.jitter_pct = float(jitter_pct)
        self.num_intervals = num_intervals
        self._rng = random.Random(seed)
        if max_child_multiple is not None:
            max_child_multiple = float(max_child_multiple)
            if not math.isfinite(max_child_multiple) or max_child_multiple < 1.0:
                raise ValueError(
                    "max_child_multiple must be >= 1.0 (a cap below the originally "
                    f"scheduled size would shrink the schedule), got {max_child_multiple!r}"
                )
        self.max_child_multiple = max_child_multiple
        if (
            max_child_multiple is None
            and self.catch_up_policy is not CatchUpPolicy.PASSIVE_CONTINUE
        ):
            logger.warning(
                "catch_up_policy=%s with max_child_multiple=None: catch-up is uncapped, "
                "so one late child order can absorb the entire residual.",
                self.catch_up_policy.value,
            )

        if self.algo_type is SlicerType.VWAP:
            if historical_volume_curve is None:
                raise ValueError(
                    "VWAP slicing requires historical_volume_curve; falling back to a "
                    "flat curve would produce a TWAP schedule labelled VWAP"
                )
            if len(historical_volume_curve) != num_intervals:
                raise ValueError(
                    f"historical_volume_curve has {len(historical_volume_curve)} "
                    f"entries but num_intervals is {num_intervals}; the curve length "
                    "must match the schedule length"
                )
            self.historical_volume_curve: List[float] = [
                float(w) for w in historical_volume_curve
            ]
        else:
            if historical_volume_curve is not None:
                logger.warning(
                    "historical_volume_curve supplied with algo_type=TWAP; it is "
                    "ignored. Pass algo_type=VWAP to weight by the volume curve."
                )
            self.historical_volume_curve = [1.0] * num_intervals

        # `start_time or time.time()` would treat a legitimate 0.0 epoch as absent.
        self.start_time = time.time() if start_time is None else float(start_time)
        if not math.isfinite(self.start_time):
            raise ValueError(f"start_time must be finite, got {start_time!r}")

        window_end = self.start_time + num_intervals * self.interval_seconds
        self.deadline = window_end if deadline is None else float(deadline)
        if not math.isfinite(self.deadline):
            raise ValueError(f"deadline must be finite, got {deadline!r}")

        #: Released quantity not currently allocated to any open slice.
        self.unassigned_qty: float = 0.0
        #: Quantity reported filled in excess of the parent order. Never discarded —
        #: a fill that happened at the broker is a real position.
        self.overfill_qty: float = 0.0
        self.slices: List[ChildOrderSlice] = []
        self._build_schedule()

    # -- construction -------------------------------------------------------
    def _build_schedule(self) -> None:
        sizes = allocate_lots(
            self.total_qty,
            self.historical_volume_curve,
            lot_size=self.lot_size,
            jitter_pct=self.jitter_pct,
            rng=self._rng,
        )
        actionable = sum(1 for qty in sizes if qty > 0.0)
        if actionable < self.num_intervals:
            logger.warning(
                "Schedule requests %d intervals but only %d carry a non-zero size: "
                "total_qty %.10g is only %.10g lots. Slicing finer than the lot size "
                "cannot help — reduce num_intervals.",
                self.num_intervals,
                actionable,
                self.total_qty,
                self.total_qty / self.lot_size,
            )

        self.slices = []
        for i, qty in enumerate(sizes):
            if i == 0:
                # One-sided jitter on the first slice. A symmetric draw would be
                # clamped at start_time roughly half the time, parking the opening
                # child order on an exactly predictable timestamp.
                offset = self._rng.uniform(0.0, self.jitter_pct) * self.interval_seconds
            else:
                offset = (
                    i + self._rng.uniform(-self.jitter_pct, self.jitter_pct)
                ) * self.interval_seconds
            self.slices.append(
                ChildOrderSlice(
                    slice_id=i,
                    target_qty=float(qty),
                    target_time=self.start_time + max(0.0, offset),
                )
            )
        #: Sizes as first scheduled — the baseline `max_child_multiple` caps against.
        self._original_targets: List[float] = [s.target_qty for s in self.slices]

    # -- lookups ------------------------------------------------------------
    def _require_slice(self, slice_id: int) -> ChildOrderSlice:
        if not isinstance(slice_id, int) or slice_id < 0 or slice_id >= len(self.slices):
            raise KeyError(
                f"unknown slice_id {slice_id!r} (schedule holds {len(self.slices)} "
                "slices). Silently dropping the event would lose a real broker fill "
                "from the parent order's accounting."
            )
        return self.slices[slice_id]

    def open_slices(self) -> List[ChildOrderSlice]:
        """Slices the catch-up policy may still re-size."""
        return [s for s in self.slices if s.status in _OPEN_STATES]

    def actionable_slices(self) -> List[ChildOrderSlice]:
        """Open slices carrying a non-zero size — the ones worth sending."""
        return [s for s in self.open_slices() if s.target_qty > _QTY_TOL]

    def total_filled(self) -> float:
        """Quantity executed across every child order."""
        return math.fsum(s.filled_qty for s in self.slices)

    def remaining_qty(self) -> float:
        """Parent quantity still to be executed."""
        return max(0.0, self.total_qty - self.total_filled())

    def quantity_invariant_ok(self) -> bool:
        """True when scheduled + unassigned quantity still equals the parent order."""
        scheduled = math.fsum(s.target_qty for s in self.slices)
        return abs(scheduled + self.unassigned_qty - self.total_qty) <= 1e-6

    # -- lifecycle events ---------------------------------------------------
    def on_child_fill(self, slice_id: int, filled_qty: float, fill_price: float) -> None:
        """
        Record an execution against a child order.

        Pure fill accounting: it accumulates quantity and the quantity-weighted average
        price, and closes the slice as FILLED once its target is met. It deliberately
        does **not** release residual quantity — a partial fill may be followed by more
        executions on the same working child order. Call `on_child_expired` or
        `on_child_reject` when the child order is actually finished; that is what
        triggers the catch-up policy.

        Raises:
            KeyError: unknown ``slice_id``.
            ValueError: non-finite or non-positive ``filled_qty`` / ``fill_price``.
        """
        child = self._require_slice(slice_id)
        filled_qty = _validate_positive_finite(filled_qty, "filled_qty")
        fill_price = _validate_positive_finite(fill_price, "fill_price")

        if child.status in (SliceStatus.REJECTED, SliceStatus.CANCELLED):
            logger.error(
                "Fill of %.10g @ %.10g reported for slice %d which is already %s. "
                "Recording it anyway — the position is real — but reconcile with the "
                "broker before sending further child orders.",
                filled_qty,
                fill_price,
                slice_id,
                child.status.value,
            )

        new_qty = child.filled_qty + filled_qty
        child.filled_avg_price = (
            child.filled_qty * child.filled_avg_price + filled_qty * fill_price
        ) / new_qty
        child.filled_qty = new_qty

        if child.filled_qty + _QTY_TOL >= child.target_qty:
            child.status = SliceStatus.FILLED

        parent_overfill = self.total_filled() - self.total_qty
        if parent_overfill > _QTY_TOL:
            self.overfill_qty = parent_overfill
            logger.error(
                "Parent order over-filled by %.10g (%.10g filled vs %.10g requested). "
                "Stop sending child orders and reconcile the position.",
                parent_overfill,
                self.total_filled(),
                self.total_qty,
            )

    def on_child_expired(self, slice_id: int) -> None:
        """
        Close a child order that stopped working with quantity outstanding — its
        time-in-force elapsed, or the caller cancelled the residual.

        Truncates the slice's target to what actually filled, then releases the residual
        to the catch-up policy. That truncation is the step a naive implementation
        omitted: leaving the original target in place *while also* redistributing the
        residual made the schedule sum to more than the parent order, so a caller
        driving orders from ``target_qty`` would over-execute.
        """
        self._close_slice(slice_id, "on_child_expired")

    def on_child_reject(self, slice_id: int, reason: str = "") -> None:
        """
        Record a broker rejection of a child order and release its full residual.

        A rejection is not a retry signal. Classify the reason — risk-limit breach, bad
        tick size, insufficient buying power, venue halt — before letting the policy
        push that quantity into later intervals; re-sending a size the venue just
        refused will be refused again, only larger.
        """
        child = self._require_slice(slice_id)
        if child.status not in _OPEN_STATES:
            logger.warning(
                "on_child_reject for slice %d ignored: already %s.",
                slice_id,
                child.status.value,
            )
            return
        residual = child.residual_qty
        logger.warning(
            "Child order %d rejected (%s); releasing %.10g to policy %s.",
            slice_id,
            reason or "no reason given",
            residual,
            self.catch_up_policy.value,
        )
        child.target_qty = child.filled_qty
        child.status = SliceStatus.REJECTED
        child.reject_reason = reason or None
        self._release(residual)

    def on_child_cancel(self, slice_id: int) -> None:
        """Cancel a child order and release its unfilled quantity to the policy."""
        self._close_slice(slice_id, "on_child_cancel", force_cancelled=True)

    def _close_slice(
        self, slice_id: int, caller: str, force_cancelled: bool = False
    ) -> None:
        child = self._require_slice(slice_id)
        if child.status not in _OPEN_STATES:
            logger.warning(
                "%s for slice %d ignored: already %s.",
                caller,
                slice_id,
                child.status.value,
            )
            return
        residual = child.residual_qty
        child.target_qty = child.filled_qty
        if force_cancelled or child.filled_qty <= _QTY_TOL:
            child.status = SliceStatus.CANCELLED
        else:
            child.status = SliceStatus.PARTIAL
        self._release(residual)

    # -- catch-up policy ----------------------------------------------------
    def _release(self, qty: float) -> None:
        """Return released quantity to the pool and apply the catch-up policy."""
        if qty > _QTY_TOL:
            self.unassigned_qty += qty
        self._apply_catch_up_policy()

    def _apply_catch_up_policy(self) -> None:
        if self.catch_up_policy is CatchUpPolicy.PASSIVE_CONTINUE:
            # Deliberate no-op: hold the original schedule, accept under-completion.
            return

        if self.catch_up_policy is CatchUpPolicy.GIVE_UP_AT_DEADLINE:
            for child in self.open_slices():
                if child.target_time >= self.deadline:
                    abandoned = child.target_qty
                    child.target_qty = 0.0
                    child.status = SliceStatus.CANCELLED
                    if abandoned > _QTY_TOL:
                        self.unassigned_qty += abandoned
                        logger.info(
                            "Slice %d abandoned at deadline; %.10g returned unassigned.",
                            child.slice_id,
                            abandoned,
                        )
            candidates = [
                s for s in self.actionable_slices() if s.target_time < self.deadline
            ]
        else:
            candidates = self.actionable_slices()

        if not candidates or self.unassigned_qty <= _QTY_TOL:
            return

        # Pro-rata to existing targets, so re-allocation preserves the VWAP volume
        # curve. Flattening every open slice to an equal size — as an earlier
        # implementation did — silently converts a VWAP schedule into a TWAP one.
        current = [s.target_qty for s in candidates]
        pool = math.fsum(current) + self.unassigned_qty
        # `pool` need not be a whole multiple of lot_size once a broker reports a
        # sub-lot fill; floor to the nearest lot and leave the stub unassigned.
        lots = math.floor(pool / self.lot_size + 1e-9)
        if lots <= 0:
            return
        new_sizes = allocate_lots(lots * self.lot_size, current, lot_size=self.lot_size)

        for child, size in zip(candidates, new_sizes):
            child.target_qty = self._cap_child(child, size)
        self.unassigned_qty = max(
            0.0, self.total_qty - math.fsum(s.target_qty for s in self.slices)
        )

    def _cap_child(self, child: ChildOrderSlice, size: float) -> float:
        """
        Clamp a catch-up allocation to ``max_child_multiple`` x the slice's original
        size, rounded down to a whole lot. Quantity above the cap is not re-spread
        across the other slices — it stays unassigned, and shows up as unfilled in the
        report rather than as an oversized clip in the market.
        """
        if self.max_child_multiple is None:
            return size
        cap_lots = math.floor(
            self.max_child_multiple * self._original_targets[child.slice_id]
            / self.lot_size
            + 1e-9
        )
        cap = cap_lots * self.lot_size
        if size > cap:
            logger.info(
                "Catch-up for slice %d capped at %.10g (wanted %.10g); %.10g stays "
                "unassigned rather than becoming an oversized child order.",
                child.slice_id,
                cap,
                size,
                size - cap,
            )
            return cap
        return size

    def reweight_pending(self, observed_volume_curve: Sequence[float]) -> None:
        """
        Re-weight the still-open slices against an updated volume curve.

        SKILL.md workflow step 3 requires recomputing the VWAP schedule when live volume
        diverges from the historical curve; this is that step. ``observed_volume_curve``
        is a full-length curve (one weight per interval) — only the entries matching
        open slices are used, so the caller can pass the same shape it already holds.

        Quantity already filled or abandoned is untouched: only currently-scheduled open
        quantity plus any unassigned residual is redistributed.

        ``max_child_multiple`` is deliberately **not** applied here. Re-weighting onto a
        back-loaded curve is meant to grow late slices, so capping against the original
        schedule would fight the caller's explicit instruction. Validate the curve you
        pass — an extreme weight produces an extreme clip.

        Raises:
            ValueError: curve length does not match ``num_intervals``, or the weights on
                the open slices are negative or all zero.
        """
        if len(observed_volume_curve) != self.num_intervals:
            raise ValueError(
                f"observed_volume_curve has {len(observed_volume_curve)} entries but "
                f"the schedule has {self.num_intervals} intervals"
            )
        candidates = self.open_slices()
        if not candidates:
            logger.warning("reweight_pending called with no open slices; nothing to do.")
            return

        weights = [float(observed_volume_curve[s.slice_id]) for s in candidates]
        pool = math.fsum(s.target_qty for s in candidates) + self.unassigned_qty
        lots = math.floor(pool / self.lot_size + 1e-9)
        if lots <= 0:
            logger.warning("reweight_pending: no whole lots left to redistribute.")
            return
        new_sizes = allocate_lots(lots * self.lot_size, weights, lot_size=self.lot_size)
        for child, size in zip(candidates, new_sizes):
            child.target_qty = size
        self.unassigned_qty = max(
            0.0, self.total_qty - math.fsum(s.target_qty for s in self.slices)
        )

    # -- reporting ----------------------------------------------------------
    def get_execution_report(
        self, benchmark_price: float, final_price: Optional[float] = None
    ) -> ExecutionReport:
        """
        Build the post-trade report.

        Args:
            benchmark_price: The benchmark the algorithm was tracking — interval TWAP,
                interval VWAP, or arrival price. Must be positive.
            final_price: Optional decision / end-of-window price. Supplying it enables
                the opportunity-cost term on the unfilled remainder and the combined
                implementation shortfall (Perold 1988, JPM 14(3), pp. 4-9). Without it
                both are ``None``: an unfilled remainder has a real cost, and reporting
                it as zero would flatter every give-up execution.

        All three cost figures are side-adjusted — positive means worse than benchmark.
        """
        benchmark_price = _validate_positive_finite(benchmark_price, "benchmark_price")
        sign = 1.0 if self.side is OrderSide.BUY else -1.0

        filled = self.total_filled()
        notional = math.fsum(s.filled_qty * s.filled_avg_price for s in self.slices)
        achieved = notional / filled if filled > _QTY_TOL else 0.0

        slippage_bps = (
            sign * (achieved - benchmark_price) / benchmark_price * 10_000.0
            if achieved > 0.0
            else 0.0
        )

        unfilled = max(0.0, self.total_qty - filled)
        opportunity_cost_bps: Optional[float] = None
        shortfall_bps: Optional[float] = None
        if final_price is not None:
            final_price = _validate_positive_finite(final_price, "final_price")
            opportunity_cost_bps = (
                sign * (final_price - benchmark_price) / benchmark_price * 10_000.0
            )
            filled_share = min(1.0, filled / self.total_qty)
            shortfall_bps = (
                filled_share * slippage_bps
                + (1.0 - filled_share) * opportunity_cost_bps
            )

        counts: Dict[str, int] = {}
        for child in self.slices:
            counts[child.status.value] = counts.get(child.status.value, 0) + 1

        return ExecutionReport(
            algo_type=self.algo_type,
            total_requested=self.total_qty,
            total_filled=filled,
            completion_pct=round(filled / self.total_qty * 100.0, 2),
            vwap_achieved_price=round(achieved, 6),
            benchmark_price=round(benchmark_price, 6),
            slippage_bps=round(slippage_bps, 2),
            child_slices_count=len(self.slices),
            side=self.side,
            unfilled_qty=unfilled,
            notional_filled=notional,
            overfill_qty=self.overfill_qty,
            status_counts=counts,
            quantity_invariant_ok=self.quantity_invariant_ok(),
            opportunity_cost_bps=(
                None if opportunity_cost_bps is None else round(opportunity_cost_bps, 2)
            ),
            implementation_shortfall_bps=(
                None if shortfall_bps is None else round(shortfall_bps, 2)
            ),
        )
