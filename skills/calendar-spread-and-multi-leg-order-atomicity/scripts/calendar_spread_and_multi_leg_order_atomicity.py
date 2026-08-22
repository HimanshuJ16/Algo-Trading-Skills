"""
calendar-spread-and-multi-leg-order-atomicity: Algorithmic atomicity for multi-leg
strategies on venues without native combo instruments.

Routes the illiquid anchor leg passively, fires a size-matched IOC hedge on every
anchor fill, and evaluates legging risk only when the hedge order reaches a
terminal state (an IOC may report several partial fills before it is cancelled).
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Quantity comparisons are float-based; treat differences below this as zero so
# accumulated rounding on fractional (e.g. crypto) fills cannot fake a broken spread.
QTY_TOLERANCE = 1e-9


class OrderSide(Enum):
    BUY = 1
    SELL = 2


class SpreadState(Enum):
    PENDING_ANCHOR = 1
    ANCHOR_PARTIAL = 2
    ANCHOR_FILLED = 3
    HEDGING = 4
    COMPLETED = 5
    BROKEN = 6


class HedgeOrderOutcome(Enum):
    """Terminal state reported by the broker for a routed hedge (IOC) order."""
    FILLED = 1
    CANCELLED = 2  # IOC/FOK remainder cancelled, or expired
    REJECTED = 3


TERMINAL_STATES = (SpreadState.COMPLETED, SpreadState.BROKEN)


@dataclass
class Leg:
    symbol: str
    side: OrderSide
    ratio: int  # E.g., 1 for 1:1 spread
    limit_price: float

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError("Leg ratio must be a positive integer, got %r" % (self.ratio,))
        if self.limit_price <= 0:
            raise ValueError("Leg limit_price must be positive, got %r" % (self.limit_price,))


@dataclass
class SpreadConfig:
    anchor_leg: Leg
    hedge_leg: Leg
    max_hedge_slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        if self.max_hedge_slippage_bps < 0:
            raise ValueError("max_hedge_slippage_bps cannot be negative")


class SpreadExecutionEngine:
    """
    Algorithmic execution engine for multi-leg strategies.
    Ensures atomicity by managing legging risk on non-native exchanges.

    Broker integration contract - the adapter MUST call:
      * ``on_anchor_fill``      for every anchor execution report;
      * ``on_hedge_fill``       for every hedge execution report (an IOC may
                                produce more than one before it is cancelled);
      * ``on_hedge_order_done`` exactly once per routed hedge order, when that
                                order reaches a terminal state.

    Legging risk is evaluated ONLY in ``on_hedge_order_done``. A hedge that
    receives zero fills never emits a fill report, so a fill-driven check would
    stay silent on the single worst outcome: a fully unhedged anchor position.
    """

    def __init__(
        self,
        config: SpreadConfig,
        broker_submit_callback: Callable[..., None],
        broker_cancel_callback: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.broker_submit = broker_submit_callback
        self.broker_cancel = broker_cancel_callback
        self.state = SpreadState.PENDING_ANCHOR

        self.anchor_filled_qty = 0.0
        self.hedge_filled_qty = 0.0
        self.target_spread_qty = 0.0
        self.started = False

        # Notional accumulators for realised net-spread verification.
        self.anchor_notional = 0.0
        self.hedge_notional = 0.0

    # ------------------------------------------------------------------ sizing

    def _expected_anchor_qty(self) -> float:
        return self.target_spread_qty * self.config.anchor_leg.ratio

    def _expected_hedge_qty(self) -> float:
        """Hedge quantity required to cover the anchor filled *so far*."""
        return (
            self.anchor_filled_qty / self.config.anchor_leg.ratio
        ) * self.config.hedge_leg.ratio

    def unhedged_qty(self) -> float:
        """Anchor exposure not yet offset by the hedge leg, in hedge-leg units."""
        return max(0.0, self._expected_hedge_qty() - self.hedge_filled_qty)

    def realized_net_spread(self) -> Optional[float]:
        """
        Volume-weighted net spread price actually achieved so far, expressed as
        (anchor VWAP - hedge VWAP) per anchor unit. Returns None before both legs
        have traded. Compare against the target net spread to verify execution
        quality; the engine does not gate on this value.
        """
        if self.anchor_filled_qty <= 0 or self.hedge_filled_qty <= 0:
            return None
        anchor_vwap = self.anchor_notional / self.anchor_filled_qty
        hedge_vwap = self.hedge_notional / self.hedge_filled_qty
        return anchor_vwap - hedge_vwap

    # --------------------------------------------------------------- execution

    def start_execution(self, total_spread_qty: float) -> None:
        """Initiates the spread by routing the illiquid anchor leg."""
        if total_spread_qty <= 0:
            raise ValueError(
                "total_spread_qty must be positive, got %r" % (total_spread_qty,)
            )
        if self.started:
            # Re-entry would duplicate a live anchor order on the venue.
            raise RuntimeError("start_execution already called for this engine instance")

        self.started = True
        self.target_spread_qty = total_spread_qty
        anchor_qty = self._expected_anchor_qty()

        logger.info("Routing Anchor Leg: %s %s", anchor_qty, self.config.anchor_leg.symbol)
        self.state = SpreadState.PENDING_ANCHOR
        self.broker_submit(
            symbol=self.config.anchor_leg.symbol,
            side=self.config.anchor_leg.side,
            qty=anchor_qty,
            price=self.config.anchor_leg.limit_price,
            order_type="LIMIT",
        )

    def on_anchor_fill(self, fill_qty: float, fill_price: float) -> None:
        """Callback from broker when the anchor leg receives a fill."""
        if self.state in TERMINAL_STATES:
            # A broken spread must not keep acquiring anchor exposure; the resting
            # anchor order is cancelled on break, but in-flight fills may still land.
            logger.warning(
                "Received anchor fill of %s for spread in terminal state %s; "
                "position requires manual reconciliation.",
                fill_qty,
                self.state.name,
            )
            return
        if fill_qty <= 0:
            raise ValueError("fill_qty must be positive, got %r" % (fill_qty,))

        self.anchor_filled_qty += fill_qty
        self.anchor_notional += fill_qty * fill_price

        expected_anchor = self._expected_anchor_qty()
        if self.anchor_filled_qty > expected_anchor + QTY_TOLERANCE:
            logger.error(
                "Anchor overfill: filled %s vs ordered %s on %s.",
                self.anchor_filled_qty,
                expected_anchor,
                self.config.anchor_leg.symbol,
            )
        anchor_complete = self.anchor_filled_qty >= expected_anchor - QTY_TOLERANCE
        self.state = SpreadState.ANCHOR_FILLED if anchor_complete else SpreadState.ANCHOR_PARTIAL

        # Size the hedge to this specific fill so the ratio is preserved per tranche.
        required_hedge_qty = (
            fill_qty / self.config.anchor_leg.ratio
        ) * self.config.hedge_leg.ratio
        worst_price = self._worst_acceptable_hedge_price()

        logger.info(
            "Anchor filled %s. Routing Hedge Leg: %s %s @ %s IOC",
            fill_qty,
            required_hedge_qty,
            self.config.hedge_leg.symbol,
            worst_price,
        )
        if anchor_complete:
            self.state = SpreadState.HEDGING

        self.broker_submit(
            symbol=self.config.hedge_leg.symbol,
            side=self.config.hedge_leg.side,
            qty=required_hedge_qty,
            price=worst_price,
            order_type="IOC",  # Immediate or Cancel to prevent hanging legs
        )

    def _worst_acceptable_hedge_price(self) -> float:
        """Limit price at the far edge of the configured slippage tolerance."""
        slip = self.config.max_hedge_slippage_bps / 10000.0
        base = self.config.hedge_leg.limit_price
        if self.config.hedge_leg.side == OrderSide.BUY:
            return base * (1 + slip)  # willing to pay up
        return base * (1 - slip)  # willing to sell down

    def on_hedge_fill(self, fill_qty: float, fill_price: Optional[float] = None) -> None:
        """
        Callback from broker for each hedge-leg execution report.

        Records the fill only. This method deliberately does NOT declare a broken
        spread: an IOC order may report several partial fills before its remainder
        is cancelled, so a shortfall observed here may still be filled moments
        later. Legging risk is assessed in ``on_hedge_order_done``.
        """
        if self.state in TERMINAL_STATES:
            logger.warning(
                "Received hedge fill of %s for spread in terminal state %s.",
                fill_qty,
                self.state.name,
            )
            return
        if fill_qty <= 0:
            raise ValueError("fill_qty must be positive, got %r" % (fill_qty,))

        self.hedge_filled_qty += fill_qty
        if fill_price is not None:
            self.hedge_notional += fill_qty * fill_price

    def on_hedge_order_done(
        self,
        outcome: HedgeOrderOutcome = HedgeOrderOutcome.CANCELLED,
    ) -> None:
        """
        Callback from broker when a routed hedge order reaches a terminal state
        (fully filled, IOC remainder cancelled/expired, or rejected).

        This is the only place legging risk is decided, because it is the only
        event that also fires when the hedge received zero fills.
        """
        if self.state in TERMINAL_STATES:
            return
        if not self.started:
            raise RuntimeError("on_hedge_order_done called before start_execution")

        shortfall = self.unhedged_qty()
        if shortfall > QTY_TOLERANCE:
            self.state = SpreadState.BROKEN
            logger.critical(
                "BROKEN SPREAD! Hedge order terminated as %s with %s units of %s "
                "still unhedged against %s of %s.",
                outcome.name,
                shortfall,
                self.config.hedge_leg.symbol,
                self.anchor_filled_qty,
                self.config.anchor_leg.symbol,
            )
            self._cancel_anchor_leg()
            return

        if self.state == SpreadState.HEDGING:
            self.state = SpreadState.COMPLETED
            logger.info(
                "Spread Execution Completed successfully. Realized net spread: %s",
                self.realized_net_spread(),
            )
        # Otherwise the anchor is still working: this tranche is hedged, so stay in
        # ANCHOR_PARTIAL and await further anchor fills.

    def _cancel_anchor_leg(self) -> None:
        """Stop accumulating naked anchor exposure once the spread has broken."""
        if self.broker_cancel is None:
            logger.critical(
                "No broker_cancel_callback configured: resting anchor order on %s "
                "must be pulled MANUALLY and immediately.",
                self.config.anchor_leg.symbol,
            )
            return
        try:
            self.broker_cancel(self.config.anchor_leg.symbol)
        except Exception:
            # Never let a failed cancel swallow the BROKEN alert above.
            logger.critical(
                "Cancel of resting anchor order on %s FAILED; manual intervention required.",
                self.config.anchor_leg.symbol,
                exc_info=True,
            )
