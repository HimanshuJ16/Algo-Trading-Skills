"""
strategy-decommissioning-and-position-unwind-procedure: orderly retirement of a live
trading strategy — entry block, working-order cancellation, participation-capped
position unwind, reconciliation, and capital return to treasury.

State machine (one-way; no transition returns to an earlier state):

    ACTIVE
      -> ORDER_ENTRY_BLOCKED     initiate_decommissioning()
      -> UNWIND_IN_PROGRESS      generate_unwind_liquidation_slices() or first fill
      -> FULLY_UNWOUND           every position flat AND no open slice AND no pending
                                 working-order cancellation
      -> DECOMMISSION_COMPLETE   return_capital_to_treasury()

What this module is
-------------------
An *inventory and state ledger* for a decommissioning. It decides how much of each
position may be released into the market in the next wave, tracks what actually filled,
and refuses to declare the strategy retired until the book is genuinely flat. It is the
bookkeeping layer that sits above an execution algo.

What this module is NOT
-----------------------
- **Not a VWAP or TWAP schedule.** Each wave is capped at ``max_adv_slice_pct`` of the
  symbol's average daily volume — a flat participation (POV-style) cap. There is no
  volume curve, no time grid and no intraday scheduling. Feed each slice to a real
  execution algo (`execution-algo-twap-vwap-slicing`,
  `participation-of-volume-pov-execution`); do not send it as one market order.
- **Not a market-impact model.** Capping participation limits impact; it does not
  measure or minimise it. The impact/timing-risk trade-off is the Almgren-Chriss
  efficient frontier (Almgren & Chriss, "Optimal execution of portfolio transactions",
  *Journal of Risk* 3(2), Winter 2000, pp. 5-39): a slower unwind pays less impact but
  holds the retired strategy's market risk for longer. Slower is not automatically safer.
- **Not an order router and not a kill switch.** It sends nothing and cancels nothing.
  It records that working orders *must* be cancelled (MiFID II RTS 6 Art. 12 kill
  functionality) and blocks completion until the caller confirms each cancellation.
- **Not a P&L engine.** Realized P&L is supplied by the broker or the accounting system,
  which own tax-lot selection, fees and financing. The engine validates and aggregates
  it; it never recomputes it.
- **Not thread-safe.** Fills typically arrive on a websocket or callback thread. Two
  concurrent ``record_slice_execution`` calls interleave read-modify-write on the same
  position and lose one of them. Serialise every mutating call onto one thread or behind
  one lock.

Correctness commitments
-----------------------
- A fill is a fact. An execution larger than the remaining position is recorded
  truthfully — the position flips sign — and raised as a reconciliation break. It is
  never clamped to zero, because clamping hides a live unhedged position.
- Duplicate fills are suppressed only when the caller supplies the broker's
  ``execution_id``. Without one, a retried callback double-counts; the engine warns.
- A symbol with an unfilled outstanding slice is skipped by the next
  ``generate_unwind_liquidation_slices()`` call, so re-generating a wave cannot
  double-release the same shares into the market.
- No numeric default here is a regulatory requirement. See ``references/standards.md``.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: Quantities below this magnitude are treated as flat. Share and contract quantities
#: are far coarser than this; it exists to absorb float subtraction residue, not to
#: forgive a genuine residual position.
QUANTITY_EPSILON = 1e-6

#: Default participation cap per liquidation wave, as a percentage of average daily
#: volume. House policy, NOT a regulatory limit — no regulator prescribes a percentage
#: of ADV for liquidating a proprietary position. Calibrate per instrument.
DEFAULT_MAX_ADV_SLICE_PCT = 10.0


class DecommissionState(str, Enum):
    ACTIVE = "ACTIVE"
    ORDER_ENTRY_BLOCKED = "ORDER_ENTRY_BLOCKED"
    UNWIND_IN_PROGRESS = "UNWIND_IN_PROGRESS"
    FULLY_UNWOUND = "FULLY_UNWOUND"
    DECOMMISSION_COMPLETE = "DECOMMISSION_COMPLETE"


class DecommissionStateError(RuntimeError):
    """Raised when an operation is attempted from a state that does not permit it."""


class EntryBlockedError(RuntimeError):
    """Raised by :meth:`StrategyDecommissioningEngine.assert_entry_allowed`.

    Strategy code must call ``assert_entry_allowed()`` on its entry path. The engine
    cannot intercept order submission it does not own — an unchecked entry path is an
    unblocked entry path, whatever the engine's state says.
    """


@dataclass
class StrategyPosition:
    """One open position belonging to the strategy being retired.

    Args:
        symbol: Instrument identifier, non-empty.
        quantity: Signed size. Positive is long, negative is short.
        market_price: Mark price used for notional reporting. Must be positive.
        avg_daily_volume: ADV in the same quantity units as ``quantity``. Must be
            positive — a zero or unknown ADV yields a zero-sized wave and an unwind
            that never terminates, so it is rejected at load time rather than silently
            producing empty slices.
        max_adv_slice_pct: Participation cap per wave, in percent of ADV. Bounded to
            ``(0, 100]``.
        lot_size: Minimum tradable increment. Non-final waves are floored to a whole
            multiple; the final wave deliberately sends the odd-lot residual, because a
            residual that is never sent is a position that is never closed.
    """

    symbol: str
    quantity: float
    market_price: float
    avg_daily_volume: float
    max_adv_slice_pct: float = DEFAULT_MAX_ADV_SLICE_PCT
    lot_size: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        for name in ("quantity", "market_price", "avg_daily_volume",
                     "max_adv_slice_pct", "lot_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(
                    f"{self.symbol}: {name} must be numeric, got {type(value).__name__}")
            if not math.isfinite(float(value)):
                raise ValueError(f"{self.symbol}: {name} must be finite, got {value!r}")
            setattr(self, name, float(value))
        if self.market_price <= 0.0:
            raise ValueError(
                f"{self.symbol}: market_price must be > 0, got {self.market_price}")
        if self.avg_daily_volume <= 0.0:
            raise ValueError(
                f"{self.symbol}: avg_daily_volume must be > 0, got {self.avg_daily_volume}. "
                "A zero/unknown ADV produces zero-quantity slices and an unwind that never "
                "completes; supply a real ADV or unwind the symbol manually.")
        if not 0.0 < self.max_adv_slice_pct <= 100.0:
            raise ValueError(
                f"{self.symbol}: max_adv_slice_pct must be in (0, 100], "
                f"got {self.max_adv_slice_pct}")
        if self.lot_size <= 0.0:
            raise ValueError(f"{self.symbol}: lot_size must be > 0, got {self.lot_size}")


@dataclass
class LiquidationSliceOrder:
    """One child order authorised for release into the market.

    ``remaining_after_slice_quantity`` is the signed position that will remain **if this
    slice fills completely**. It is a projection, not the current inventory — read
    ``engine.positions[symbol].quantity`` for that.
    """

    slice_id: str
    symbol: str
    side: str                              # 'SELL' unwinds a long, 'BUY' unwinds a short
    slice_quantity: float
    remaining_after_slice_quantity: float
    target_price: float
    participation_pct: float               # slice_quantity as a percentage of ADV
    is_final_slice: bool                   # True when this wave closes the position
    wave_index: int                        # 1-based, per symbol
    filled_quantity: float = 0.0


@dataclass
class ReconciliationBreak:
    """An inventory inconsistency that must be resolved by a human before sign-off."""

    symbol: str
    description: str
    detected_at_utc: str


@dataclass
class UnwindProcedureReport:
    """Point-in-time snapshot of the decommissioning.

    ``initial_total_notional_usd`` is marked once, at load time, from the loaded
    quantities and prices; it does not move as the unwind progresses.
    ``liquidated_notional_usd`` is the cumulative notional of executions actually
    recorded — never the notional of slices merely generated.
    """

    strategy_id: str
    state: DecommissionState
    initial_total_notional_usd: float
    remaining_notional_usd: float
    liquidated_notional_usd: float
    total_realized_pnl_usd: float
    slices_generated: List[LiquidationSliceOrder]
    new_entries_allowed: bool
    audit_notes: str
    reconciliation_breaks: List[ReconciliationBreak] = field(default_factory=list)
    pending_order_cancellations: List[str] = field(default_factory=list)
    open_slice_ids: List[str] = field(default_factory=list)
    unsliceable_symbols: List[str] = field(default_factory=list)


class StrategyDecommissioningEngine:
    """Ledger and state machine for retiring one strategy and flattening its book.

    Typical sequence::

        engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        engine.load_positions([...])
        engine.initiate_decommissioning("IR 0.31 < 0.50 floor for 3 months",
                                        working_order_ids=["O-1", "O-2"])
        for oid in ("O-1", "O-2"):
            engine.record_order_cancellation(oid)    # only after the broker confirms
        while engine.state is not DecommissionState.FULLY_UNWOUND:
            report = engine.generate_unwind_liquidation_slices()
            for child in report.slices_generated:
                ...                                  # route child to an execution algo
                engine.record_slice_execution(child.slice_id, child.symbol, qty, px, pnl,
                                              execution_id=broker_fill_id)
        engine.return_capital_to_treasury()

    The loop terminates because every wave either fills (reducing the position) or is
    released via :meth:`cancel_slice`. A symbol listed in
    ``report.unsliceable_symbols`` never shrinks on its own and must be handled
    manually — check that list rather than looping forever.
    """

    def __init__(self, strategy_id: str) -> None:
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string")
        self.strategy_id = strategy_id
        self.state = DecommissionState.ACTIVE
        self.positions: Dict[str, StrategyPosition] = {}
        self.liquidated_pnl_usd = 0.0
        self.liquidated_notional_usd = 0.0
        self.initial_total_notional_usd = 0.0
        self.decommission_reason: Optional[str] = None
        self.reconciliation_breaks: List[ReconciliationBreak] = []
        self.audit_trail: List[Tuple[str, str]] = []
        self._open_slices: Dict[str, LiquidationSliceOrder] = {}
        self._wave_counter: Dict[str, int] = {}
        self._pending_cancellations: Dict[str, bool] = {}
        self._seen_execution_ids: Set[str] = set()

    # ------------------------------------------------------------------ internals

    def _audit(self, note: str) -> None:
        self.audit_trail.append((datetime.now(timezone.utc).isoformat(), note))

    def _record_break(self, symbol: str, description: str) -> None:
        self.reconciliation_breaks.append(ReconciliationBreak(
            symbol=symbol,
            description=description,
            detected_at_utc=datetime.now(timezone.utc).isoformat(),
        ))
        self._audit(f"RECONCILIATION BREAK [{symbol}]: {description}")
        logger.error("RECONCILIATION BREAK [%s/%s]: %s",
                     self.strategy_id, symbol, description)

    def _is_flat(self) -> bool:
        return all(abs(p.quantity) <= QUANTITY_EPSILON for p in self.positions.values())

    @staticmethod
    def _wave_quantity(pos: StrategyPosition) -> Tuple[float, bool]:
        """Return ``(wave_quantity, is_final)`` for one position.

        A wave quantity of zero means no compliant wave exists: the participation cap is
        smaller than one lot. Rounding up through the cap is not an option, so the symbol
        is surfaced to the caller instead.
        """
        abs_qty = abs(pos.quantity)
        cap_qty = (pos.max_adv_slice_pct / 100.0) * pos.avg_daily_volume
        if abs_qty <= cap_qty:
            # Final wave: send the whole residual, odd lot included. Flooring here would
            # strand a fraction that never closes.
            return abs_qty, True
        return math.floor(cap_qty / pos.lot_size) * pos.lot_size, False

    def _unsliceable_symbols(self) -> List[str]:
        """Open symbols whose participation cap is below one lot."""
        return sorted(
            symbol for symbol, pos in self.positions.items()
            if abs(pos.quantity) > QUANTITY_EPSILON
            and self._wave_quantity(pos)[0] <= QUANTITY_EPSILON
        )

    @staticmethod
    def _require_finite(name: str, value: object, *, positive: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite number, got {value!r}")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{name} must be a finite number, got {value!r}")
        if positive and numeric <= 0.0:
            raise ValueError(f"{name} must be > 0, got {numeric}")
        return numeric

    # --------------------------------------------------------------------- inputs

    def load_positions(self, positions: Sequence[StrategyPosition]) -> None:
        """Load the strategy's open inventory.

        Positions may be added after decommissioning has been initiated — a
        reconciliation sweep legitimately discovers forgotten inventory — but not once
        the book has been declared flat, because that would silently reopen a completed
        unwind. Duplicate symbols are rejected rather than overwritten: an overwrite
        would discard a real position.
        """
        if self.state in (DecommissionState.FULLY_UNWOUND,
                          DecommissionState.DECOMMISSION_COMPLETE):
            raise DecommissionStateError(
                f"Cannot load positions in state {self.state.value}. The book has already "
                "been declared flat; open a new decommissioning record for newly "
                "discovered inventory.")
        positions = list(positions)
        for p in positions:
            if not isinstance(p, StrategyPosition):
                raise TypeError(f"Expected StrategyPosition, got {type(p).__name__}")
            if p.symbol in self.positions:
                raise ValueError(
                    f"Duplicate position for {p.symbol}. Net the quantities before loading; "
                    "silently overwriting would discard an open position.")
            self.positions[p.symbol] = p
            self.initial_total_notional_usd += abs(p.quantity) * p.market_price
        if self.state is not DecommissionState.ACTIVE:
            logger.warning(
                "Positions loaded into %s after decommissioning began (state=%s).",
                self.strategy_id, self.state.value)
        self._audit(f"Loaded {len(positions)} position(s).")

    # ------------------------------------------------------------- entry blocking

    @property
    def new_entries_allowed(self) -> bool:
        """True only while the strategy is still ACTIVE."""
        return self.state is DecommissionState.ACTIVE

    def assert_entry_allowed(self, symbol: str = "") -> None:
        """Gate that strategy entry logic must call before submitting a new entry.

        This is the hard block. It is only effective if the strategy actually calls it;
        the engine owns no order path and cannot intercept a submission made around it.
        """
        if not self.new_entries_allowed:
            raise EntryBlockedError(
                f"Entry blocked for {self.strategy_id}"
                f"{' / ' + symbol if symbol else ''}: strategy is being decommissioned "
                f"(state={self.state.value}, reason={self.decommission_reason!r}).")

    # ------------------------------------------------------------------ lifecycle

    def initiate_decommissioning(
        self,
        reason: str,
        working_order_ids: Sequence[str] = (),
    ) -> UnwindProcedureReport:
        """Block new entries and register the working orders that must be cancelled.

        Args:
            reason: Auditable justification. Required and non-empty — a default reason
                written into an audit trail is a fabricated record.
            working_order_ids: Broker order ids resting on the book at the moment of the
                decision. Blocking new entries does nothing about orders already
                working; MiFID II RTS 6 Art. 12 requires the firm to be able to "cancel
                immediately, as an emergency measure, any or all of its unexecuted
                orders". Each id must be confirmed through
                :meth:`record_order_cancellation` before the strategy can be declared
                fully unwound.

        Raises:
            DecommissionStateError: if decommissioning has already been initiated.
                Re-initiating would reset the audit reason on a live unwind.
        """
        if self.state is not DecommissionState.ACTIVE:
            raise DecommissionStateError(
                f"Decommissioning already initiated for {self.strategy_id} "
                f"(state={self.state.value}); it cannot be restarted.")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string (it is an audit record)")

        self.state = DecommissionState.ORDER_ENTRY_BLOCKED
        self.decommission_reason = reason.strip()
        for oid in working_order_ids:
            if not isinstance(oid, str) or not oid.strip():
                raise ValueError("working_order_ids must be non-empty strings")
            self._pending_cancellations[oid] = False

        notes = (f"DECOMMISSION INITIATED [{self.strategy_id}]: new entries BLOCKED; "
                 f"{len(self._pending_cancellations)} working order(s) pending cancellation. "
                 f"Reason: {self.decommission_reason}")
        self._audit(notes)
        logger.warning(notes)
        return self._generate_unwind_report([], notes)

    def record_order_cancellation(self, order_id: str) -> None:
        """Confirm that a working order registered at initiation has been cancelled.

        Call this only on a broker-confirmed cancellation. A cancel *request* is not a
        cancellation: an order can fill in the gap between the request and the
        acknowledgement.
        """
        if order_id not in self._pending_cancellations:
            raise ValueError(
                f"{order_id} was not registered as a working order at decommissioning "
                "initiation; confirming it would create a false audit record.")
        if self._pending_cancellations[order_id]:
            logger.info("Cancellation for %s already confirmed; ignoring duplicate.", order_id)
            return
        self._pending_cancellations[order_id] = True
        self._audit(f"Working order {order_id} cancellation confirmed.")
        self._refresh_terminal_state()

    def pending_order_cancellations(self) -> List[str]:
        """Working orders registered at initiation that are not yet confirmed cancelled."""
        return sorted(oid for oid, done in self._pending_cancellations.items() if not done)

    # -------------------------------------------------------------------- slicing

    def generate_unwind_liquidation_slices(self) -> UnwindProcedureReport:
        """Authorise the next liquidation wave for every eligible symbol.

        One wave per symbol per call, capped at ``max_adv_slice_pct`` of ADV. A symbol
        that already has an unfilled slice outstanding is skipped — regenerating a wave
        for it would authorise the same shares twice and flip the position on the second
        fill.

        A symbol whose participation cap is smaller than one lot cannot be sliced
        compliantly; it is reported in ``unsliceable_symbols`` and left untouched rather
        than being rounded up through the cap.
        """
        if self.state is DecommissionState.ACTIVE:
            raise DecommissionStateError(
                "Cannot generate liquidation slices while the strategy is ACTIVE. "
                "Call initiate_decommissioning() first - otherwise the strategy can open "
                "new positions while the unwind is draining them.")
        if self.state in (DecommissionState.FULLY_UNWOUND,
                          DecommissionState.DECOMMISSION_COMPLETE):
            raise DecommissionStateError(
                f"Cannot generate liquidation slices in state {self.state.value}; "
                "the book is already flat.")

        self.state = DecommissionState.UNWIND_IN_PROGRESS
        slices: List[LiquidationSliceOrder] = []
        unsliceable: List[str] = []
        skipped_open: List[str] = []
        symbols_with_open_slices = {s.symbol for s in self._open_slices.values()}

        for symbol, pos in sorted(self.positions.items()):
            abs_qty = abs(pos.quantity)
            if abs_qty <= QUANTITY_EPSILON:
                continue
            if symbol in symbols_with_open_slices:
                skipped_open.append(symbol)
                continue

            slice_qty, is_final = self._wave_quantity(pos)
            if slice_qty <= QUANTITY_EPSILON:
                unsliceable.append(symbol)
                logger.error(
                    "[%s/%s] participation cap %.4f is below one lot (%.4f); cannot "
                    "slice compliantly. Handle manually.", self.strategy_id, symbol,
                    (pos.max_adv_slice_pct / 100.0) * pos.avg_daily_volume, pos.lot_size)
                continue

            is_long = pos.quantity > 0
            wave = self._wave_counter.get(symbol, 0) + 1
            self._wave_counter[symbol] = wave
            order = LiquidationSliceOrder(
                slice_id=f"SLICE_{self.strategy_id}_{symbol}_{wave}",
                symbol=symbol,
                side="SELL" if is_long else "BUY",
                slice_quantity=slice_qty,
                remaining_after_slice_quantity=(abs_qty - slice_qty) * (1.0 if is_long else -1.0),
                target_price=pos.market_price,
                participation_pct=100.0 * slice_qty / pos.avg_daily_volume,
                is_final_slice=is_final,
                wave_index=wave,
            )
            slices.append(order)
            self._open_slices[order.slice_id] = order

        if slices:
            notes = (f"UNWIND IN PROGRESS [{self.strategy_id}]: authorised {len(slices)} "
                     f"liquidation wave(s).")
        elif skipped_open:
            notes = (f"UNWIND IN PROGRESS [{self.strategy_id}]: no new waves - "
                     f"{len(skipped_open)} symbol(s) still have an unfilled slice outstanding.")
        elif unsliceable:
            notes = (f"UNWIND BLOCKED [{self.strategy_id}]: {len(unsliceable)} symbol(s) have "
                     f"a participation cap below one lot and require manual liquidation.")
        else:
            notes = f"UNWIND [{self.strategy_id}]: no open positions require slicing."
            self._refresh_terminal_state()

        if skipped_open:
            logger.info("[%s] skipped (open slice outstanding): %s",
                        self.strategy_id, ", ".join(sorted(skipped_open)))
        self._audit(notes)
        logger.info(notes)
        return self._generate_unwind_report(slices, notes)

    def cancel_slice(self, slice_id: str, reason: str = "") -> None:
        """Release an authorised-but-unfilled slice so the symbol can be re-sliced.

        Call this once the broker has confirmed the child order is dead. Any quantity
        that filled before cancellation must already have been recorded through
        :meth:`record_slice_execution`.
        """
        order = self._open_slices.pop(slice_id, None)
        if order is None:
            raise ValueError(f"{slice_id} is not an open slice for {self.strategy_id}.")
        self._audit(
            f"Slice {slice_id} released with {order.filled_quantity:g} of "
            f"{order.slice_quantity:g} filled. {reason}".strip())
        logger.info("[%s] slice %s released (filled %g of %g). %s",
                    self.strategy_id, slice_id, order.filled_quantity,
                    order.slice_quantity, reason)

    # ------------------------------------------------------------------ execution

    def record_slice_execution(
        self,
        slice_id: str,
        symbol: str,
        executed_qty: float,
        executed_price: float,
        realized_pnl: float,
        *,
        execution_id: Optional[str] = None,
    ) -> None:
        """Apply a broker fill to the inventory.

        Args:
            slice_id: The authorising slice. An unknown id is accepted with a warning —
                a manual liquidation outside the engine is a legitimate source of fills,
                and rejecting it would leave the inventory overstated.
            symbol: Instrument of the fill. Must belong to this strategy.
            executed_qty: Filled quantity, unsigned and positive. This is the quantity
                that reduced the position, whichever side it was.
            executed_price: Fill price, positive.
            realized_pnl: Realized P&L for this fill, from the broker or the accounting
                system. The engine aggregates it and never recomputes it.
            execution_id: Broker fill id. **Supply it.** It is the only thing that makes
                this call idempotent; without it a retried webhook or a replayed
                callback double-decrements the position and double-counts P&L.

        Raises:
            ValueError: on a non-finite or non-positive quantity/price, a non-finite
                P&L, or a symbol that does not belong to this strategy.
            DecommissionStateError: if called before decommissioning was initiated or
                after capital has been returned.

        An execution larger than the remaining position is applied truthfully — the
        position flips sign — and logged as a reconciliation break. It is not clamped: a
        clamp would report a flat book while an unintended, unhedged opposite-side
        position is live.
        """
        if self.state is DecommissionState.ACTIVE:
            raise DecommissionStateError(
                "Cannot record an unwind execution before initiate_decommissioning(); "
                "the strategy is still ACTIVE and free to open new positions.")
        if self.state is DecommissionState.DECOMMISSION_COMPLETE:
            raise DecommissionStateError(
                "Capital has already been returned to treasury; a late fill is a "
                "reconciliation incident, not a routine execution.")

        executed_qty = self._require_finite("executed_qty", executed_qty, positive=True)
        executed_price = self._require_finite("executed_price", executed_price, positive=True)
        realized_pnl = self._require_finite("realized_pnl", realized_pnl)

        pos = self.positions.get(symbol)
        if pos is None:
            raise ValueError(
                f"{symbol} is not in {self.strategy_id}'s inventory. A fill for an unknown "
                "symbol is an attribution error - resolve which strategy owns it rather "
                "than absorbing it here.")

        if execution_id is not None:
            if execution_id in self._seen_execution_ids:
                logger.info("[%s] duplicate execution_id %s ignored.",
                            self.strategy_id, execution_id)
                return
            self._seen_execution_ids.add(execution_id)
        else:
            logger.warning(
                "[%s] fill on %s recorded without an execution_id; duplicate-fill "
                "suppression is disabled for this call.", self.strategy_id, symbol)

        was_long = pos.quantity > 0
        remaining_before = abs(pos.quantity)
        overfill = executed_qty - remaining_before

        pos.quantity = pos.quantity - executed_qty if was_long else pos.quantity + executed_qty
        if abs(pos.quantity) <= QUANTITY_EPSILON:
            pos.quantity = 0.0

        self.liquidated_pnl_usd += realized_pnl
        self.liquidated_notional_usd += executed_qty * executed_price
        self.state = DecommissionState.UNWIND_IN_PROGRESS

        order = self._open_slices.get(slice_id)
        if order is None:
            logger.warning(
                "[%s] fill recorded against unknown or already-closed slice %s on %s; "
                "treating as an out-of-band liquidation.", self.strategy_id, slice_id, symbol)
        else:
            order.filled_quantity += executed_qty
            if order.filled_quantity >= order.slice_quantity - QUANTITY_EPSILON:
                self._open_slices.pop(slice_id, None)

        if overfill > QUANTITY_EPSILON:
            self._record_break(
                symbol,
                f"Overfill on slice {slice_id}: filled {executed_qty:g} against a remaining "
                f"position of {remaining_before:g}. Position is now {pos.quantity:g} - the "
                f"unwind has opened an unintended {'short' if was_long else 'long'} of "
                f"{abs(pos.quantity):g}.")

        self._audit(
            f"Fill {execution_id or '<no-id>'} on {symbol}: {executed_qty:g} @ "
            f"{executed_price:g}, realized {realized_pnl:g}. Position now {pos.quantity:g}.")
        self._refresh_terminal_state()

    def _refresh_terminal_state(self) -> None:
        """Promote to FULLY_UNWOUND only when the book is genuinely closable."""
        if self.state in (DecommissionState.ACTIVE, DecommissionState.DECOMMISSION_COMPLETE):
            return
        if not self._is_flat() or self._open_slices or self.pending_order_cancellations():
            return
        if self.state is not DecommissionState.FULLY_UNWOUND:
            self.state = DecommissionState.FULLY_UNWOUND
            notes = (f"STRATEGY FULLY UNWOUND [{self.strategy_id}]: all positions flat, no "
                     f"open slices, no pending cancellations. Realized PnL = "
                     f"${self.liquidated_pnl_usd:,.2f}.")
            self._audit(notes)
            logger.info(notes)

    # ----------------------------------------------------------------- completion

    def return_capital_to_treasury(
        self,
        acknowledge_breaks: bool = False,
    ) -> UnwindProcedureReport:
        """Close the decommissioning and release capital back to the fund treasury.

        Args:
            acknowledge_breaks: Set only when every recorded reconciliation break has
                been investigated and resolved outside the engine. It records an
                explicit human decision; it does not clear the breaks from the report.

        Raises:
            DecommissionStateError: if the book is not flat, working-order cancellations
                are unconfirmed, slices are still outstanding, or unacknowledged
                reconciliation breaks exist.
        """
        self._refresh_terminal_state()
        if self.state is DecommissionState.DECOMMISSION_COMPLETE:
            raise DecommissionStateError(
                f"Capital for {self.strategy_id} has already been returned to treasury.")
        if self.state is not DecommissionState.FULLY_UNWOUND:
            residual = {s: p.quantity for s, p in self.positions.items()
                        if abs(p.quantity) > QUANTITY_EPSILON}
            raise DecommissionStateError(
                f"Cannot return capital for {self.strategy_id}: state={self.state.value}, "
                f"residual positions={residual or 'none'}, "
                f"open slices={sorted(self._open_slices) or 'none'}, "
                f"pending cancellations={self.pending_order_cancellations() or 'none'}.")
        if self.reconciliation_breaks and not acknowledge_breaks:
            raise DecommissionStateError(
                f"Cannot return capital for {self.strategy_id}: "
                f"{len(self.reconciliation_breaks)} unresolved reconciliation break(s). "
                "Investigate them, then pass acknowledge_breaks=True to record the decision.")

        self.state = DecommissionState.DECOMMISSION_COMPLETE
        notes = (f"DECOMMISSION COMPLETE [{self.strategy_id}]: capital returned to treasury. "
                 f"Liquidated notional ${self.liquidated_notional_usd:,.2f}, realized PnL "
                 f"${self.liquidated_pnl_usd:,.2f}."
                 + (" Reconciliation breaks acknowledged." if self.reconciliation_breaks else ""))
        self._audit(notes)
        logger.warning(notes)
        return self._generate_unwind_report([], notes)

    # ------------------------------------------------------------------ reporting

    def status_report(self) -> UnwindProcedureReport:
        """Read-only snapshot. Does not advance the state machine."""
        return self._generate_unwind_report(
            [], f"STATUS [{self.strategy_id}]: {self.state.value}")

    def _generate_unwind_report(
        self,
        slices: List[LiquidationSliceOrder],
        notes: str,
    ) -> UnwindProcedureReport:
        rem_notional = sum(abs(p.quantity) * p.market_price for p in self.positions.values())
        return UnwindProcedureReport(
            strategy_id=self.strategy_id,
            state=self.state,
            initial_total_notional_usd=round(self.initial_total_notional_usd, 2),
            remaining_notional_usd=round(rem_notional, 2),
            liquidated_notional_usd=round(self.liquidated_notional_usd, 2),
            total_realized_pnl_usd=round(self.liquidated_pnl_usd, 2),
            slices_generated=slices,
            new_entries_allowed=self.new_entries_allowed,
            audit_notes=notes,
            reconciliation_breaks=list(self.reconciliation_breaks),
            pending_order_cancellations=self.pending_order_cancellations(),
            open_slice_ids=sorted(self._open_slices),
            unsliceable_symbols=self._unsliceable_symbols(),
        )
