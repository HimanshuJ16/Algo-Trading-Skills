"""Lit/dark sequencing for an institutional parent order in US NMS stocks.

Decides *where and in what order* a parent order is worked: first a
non-displayed midpoint sweep across dark ATSs, then a price-priority sweep of
the remaining balance across lit exchanges. Sizing inside the dark stage
(toxicity scoring, per-venue MinQty calibration) belongs to
``dark-pool-routing-logic``; lit fee/rebate ranking belongs to
``smart-order-routing-across-venues``. This module owns the sequencing decision
and the resulting audit record.

What this module is
-------------------
A **pre-trade planner**. ``historical_fill_rate`` is applied as a deterministic
expected-fill model (``FILL_MODEL_ID``): the "executed" quantities in the report
are *projections* of what an IOC sweep would return, not fills reported by a
broker. Nothing here talks to a venue, and there is no order-state machine,
timeout handling, or idempotency key. Reconcile against real execution reports
before treating any number in the report as realised.

Regulatory grounding (US NMS stocks; all sources consulted 2026-08-25)
---------------------------------------------------------------------
- **Trade-through, 17 CFR 242.611(a)**: a trading center must have policies
  reasonably designed to prevent executions at prices inferior to protected
  quotations. A "protected bid/offer" (Rule 600(b)) is the *best* bid/offer of
  an automated exchange or association -- top of book only; depth-of-book
  prices are not protected. The lit stage therefore sweeps in strict price
  priority, and any child priced inferior to the protected NBBO is flagged
  ``requires_iso_marking``.
- **ISO exception, Rule 611(b)(5)-(6)** with the Rule 600(b) definition of an
  intermarket sweep order: an inferior-priced execution is excepted where
  additional limit orders are routed *simultaneously* against the **full
  displayed size** of every better-priced protected quotation. The price-
  priority sweep here only reaches an inferior venue after exhausting the full
  displayed size of every better-priced one, which is the condition the ISO
  exception requires; marking the orders ``ExecInst(18)='f'`` is the caller's.
- **Sub-penny, 17 CFR 242.612**: the rule bars display/rank/**accept** of
  sub-penny-priced orders and quotations; it does not bar sub-penny
  *executions*. The Reg NMS Adopting Release (70 FR 37496, 37556) states a
  sub-penny midpoint execution is permissible "so long as the execution did not
  result from an impermissible sub-penny order or quotation". A one-cent spread
  has a half-cent midpoint, so dark children are emitted as **midpoint pegs**
  (``ExecInst(18)='M'`` / ``PegPriceType(1094)=2``), never as an explicit
  sub-penny limit price. ``ChildOrderRoute.price_instruction`` records this.
- **Locked/crossed, 17 CFR 242.610(e)**: exchanges and associations must have
  rules requiring members reasonably to avoid displaying quotations that lock
  or cross a protected quotation. A crossed consolidated book is therefore a
  data-integrity signal, and its midpoint is not a usable reference price --
  ``compute_nbbo`` rejects it rather than pricing off it.
- **Pending change**: Exchange Act Release No. 34-105655 (11 Jun 2026, File
  S7-2026-20) *proposes* rescinding Rule 611 and Rule 610(e). Comments closed
  17 Aug 2026; it remains a proposal and both rules are in force as at
  2026-08-25. Re-verify before relying on this module's trade-through logic.
- **Applicability**: Rule 611 binds "trading centers" (Rule 600(b): exchanges,
  ATSs, market makers, and broker-dealers that execute internally). A router
  that only sends orders elsewhere is not itself a trading center, but FINRA
  Rule 5310(a)(1) best execution -- reasonable diligence to ascertain the best
  market -- applies to it regardless. Do not treat the flags here as a
  compliance determination; see ``us-reg-nms-order-protection-rule-compliance``.

Deliberate limitations
----------------------
- The NBBO is derived from the venue books handed in. That is a *synthetic*
  NBBO; the official NBBO is disseminated by the SIP under the CTA/UTP plans.
  Protected-quote status also depends on the quote being automated and
  immediately accessible, which is not modelled.
- Prices are ``float``. Half-cent midpoints are exact enough at equity price
  scales, but notional aggregates carry binary-float error; use ``Decimal`` if
  you need exact money.
- ``min_dark_fill_qty`` is an engineering default (500 shares), not a
  regulatory or venue-imposed constant. No US rule sets a minimum dark order
  size, and venue minimums range from none to 25,000+ share block thresholds.
- Single price level per venue, no queue position, no venue capacity/ADV model,
  no fees or rebates, no latency, no self-match prevention, no EU waiver gate.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = frozenset({"BUY", "SELL"})
VALID_VENUE_TYPES = frozenset({"DARK", "LIT"})

# Anti-pinging floor applied to every dark IOC ping. An engineering default,
# not a regulatory constant -- calibrate per name and per venue.
DEFAULT_MIN_DARK_FILL_QTY = 500

# Identifies the fill model so a consumer cannot mistake a projection for a
# broker-reported execution.
FILL_MODEL_ID = "DETERMINISTIC_EXPECTED_FILL"

# Price comparisons are made on binary floats; this absorbs representation
# error without masking a real one-tick difference.
PRICE_EPSILON = 1e-9

# Reasons the dark stage was skipped or a dark venue passed over.
SKIP_LIMIT_THROUGH_MIDPOINT = "LIMIT_PRICE_THROUGH_MIDPOINT"
SKIP_BELOW_MIN_DARK_QTY = "PING_BELOW_MIN_DARK_FILL_QTY"
SKIP_EXPECTED_FILL_BELOW_MIN_QTY = "EXPECTED_FILL_BELOW_MIN_QTY"


class LiquiditySeekingError(ValueError):
    """Raised on an invalid venue book, parent order, or unusable NBBO.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep
    working.
    """


def _require_finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise LiquiditySeekingError(f"{name} must be finite, got {value!r}.")
    return number


def _require_positive_price(value: float, name: str) -> float:
    number = _require_finite(value, name)
    if number <= 0.0:
        raise LiquiditySeekingError(f"{name} must be > 0, got {number}.")
    return number


def _require_int(value: object, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiquiditySeekingError(
            f"{name} must be an int (shares), got {type(value).__name__}."
        )
    if value < minimum:
        raise LiquiditySeekingError(f"{name} must be >= {minimum}, got {value}.")
    return value


@dataclass
class VenueBookSpec:
    """One venue's top-of-book snapshot.

    Args:
        venue_id: Stable identifier, e.g. ``'NASDAQ'`` or ``'DARK_ATS_ALPHA'``.
        venue_type: ``'LIT'`` (exchange, displayed) or ``'DARK'`` (ATS,
            non-displayed). Normalised to upper case.
        bid_price: Best bid. For a dark venue this is indicative only -- dark
            children execute at the *lit* NBBO midpoint, and dark venues are
            excluded from the NBBO calculation entirely.
        ask_price: Best offer, same caveat.
        bid_qty: Displayed size at ``bid_price`` for a lit venue; for a dark
            venue, your own estimate of resting contra liquidity. A venue
            quoting zero size does not contribute to the NBBO.
        ask_qty: As ``bid_qty``, on the offer side.
        historical_fill_rate: Realised fill probability in [0.0, 1.0], measured
            by the router as filled/sent. Used only for dark venues, and only
            as an expected-fill projection (see ``FILL_MODEL_ID``).
    """

    venue_id: str
    venue_type: str
    bid_price: float
    ask_price: float
    bid_qty: int
    ask_qty: int
    historical_fill_rate: float = 0.50

    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise LiquiditySeekingError("venue_id must be a non-empty string.")
        self.venue_id = self.venue_id.strip()

        if not isinstance(self.venue_type, str):
            raise LiquiditySeekingError(
                f"[{self.venue_id}] venue_type must be a string."
            )
        self.venue_type = self.venue_type.strip().upper()
        if self.venue_type not in VALID_VENUE_TYPES:
            raise LiquiditySeekingError(
                f"[{self.venue_id}] venue_type must be one of "
                f"{sorted(VALID_VENUE_TYPES)}, got {self.venue_type!r}."
            )

        self.bid_price = _require_positive_price(
            self.bid_price, f"[{self.venue_id}] bid_price"
        )
        self.ask_price = _require_positive_price(
            self.ask_price, f"[{self.venue_id}] ask_price"
        )
        if self.bid_price > self.ask_price + PRICE_EPSILON:
            raise LiquiditySeekingError(
                f"[{self.venue_id}] book is crossed on its own quote "
                f"(bid {self.bid_price} > ask {self.ask_price})."
            )

        self.bid_qty = _require_int(self.bid_qty, f"[{self.venue_id}] bid_qty", 0)
        self.ask_qty = _require_int(self.ask_qty, f"[{self.venue_id}] ask_qty", 0)

        self.historical_fill_rate = _require_finite(
            self.historical_fill_rate, f"[{self.venue_id}] historical_fill_rate"
        )
        if not 0.0 <= self.historical_fill_rate <= 1.0:
            raise LiquiditySeekingError(
                f"[{self.venue_id}] historical_fill_rate must be in [0.0, 1.0], got "
                f"{self.historical_fill_rate}. A rate above 1.0 would project a fill "
                "larger than the routed quantity and over-fill the parent."
            )


@dataclass
class ParentOrderSpec:
    """The parent order to be worked across venues.

    Args:
        symbol: Instrument identifier, used for logging and the audit record.
        side: ``'BUY'`` or ``'SELL'``. Normalised to upper case; anything else
            is rejected rather than defaulted, since a silently mis-parsed side
            trades the wrong way.
        target_quantity: Parent size in shares, > 0.
        limit_price: Maximum price to pay (BUY) or minimum to accept (SELL).
            Binds on **every** stage, dark midpoint included.
        min_dark_fill_qty: Anti-pinging floor in shares, applied to the routed
            ping size and to the projected fill. An engineering default.
    """

    symbol: str
    side: str
    target_quantity: int
    limit_price: float
    min_dark_fill_qty: int = DEFAULT_MIN_DARK_FILL_QTY

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise LiquiditySeekingError("symbol must be a non-empty string.")
        self.symbol = self.symbol.strip()

        if not isinstance(self.side, str):
            raise LiquiditySeekingError("side must be a string.")
        self.side = self.side.strip().upper()
        if self.side not in VALID_SIDES:
            raise LiquiditySeekingError(
                f"side must be one of {sorted(VALID_SIDES)}, got {self.side!r}."
            )

        self.target_quantity = _require_int(self.target_quantity, "target_quantity", 1)
        self.limit_price = _require_positive_price(self.limit_price, "limit_price")
        self.min_dark_fill_qty = _require_int(
            self.min_dark_fill_qty, "min_dark_fill_qty", 0
        )


@dataclass
class ChildOrderRoute:
    """One child order directive produced by the planner.

    Args:
        venue_id: Destination venue.
        venue_type: ``'DARK'`` or ``'LIT'``.
        side: Parent side, normalised.
        quantity: Quantity routed to the venue (the IOC ping size in the dark
            stage; the sweep size in the lit stage).
        price: Reference price. For a dark child this is the midpoint the peg
            is expected to resolve to, **not** a limit price to transmit.
        execution_stage: ``'STAGE1_DARK_MIDPOINT'`` or ``'STAGE2_LIT_SWEEP'``.
        filled_quantity: Projected fill under ``FILL_MODEL_ID``. In the dark
            stage this is a model output, not a broker report.
        price_improvement_usd: Signed projected improvement of the fill versus
            the far touch of the protected NBBO. Negative means the route is
            expected to execute *worse* than the touch.
        min_qty_instruction: ``MinQty(110)`` to attach. Zero for lit children.
            Never exceeds ``quantity``.
        price_instruction: ``'MIDPOINT_PEG'`` (send ``ExecInst(18)='M'`` /
            ``PegPriceType(1094)=2``; do **not** transmit ``price`` as a limit,
            it may be sub-penny) or ``'LIMIT'``.
        requires_iso_marking: True when ``price`` is inferior to the protected
            NBBO, so the child must carry ``ExecInst(18)='f'`` and the
            simultaneous-routing conditions of Rule 611(b)(5)-(6) must hold.
    """

    venue_id: str
    venue_type: str
    side: str
    quantity: int
    price: float
    execution_stage: str
    filled_quantity: int
    price_improvement_usd: float
    min_qty_instruction: int = 0
    price_instruction: str = "LIMIT"
    requires_iso_marking: bool = False


@dataclass
class LiquiditySeekingReport:
    """Audit record for one lit/dark sequencing decision.

    Quantities on this report are **projections** under ``FILL_MODEL_ID``, not
    broker-reported executions. ``total_executed_qty + unfilled_qty`` always
    equals ``total_requested_qty``.
    """

    symbol: str
    total_requested_qty: int
    total_executed_qty: int
    dark_executed_qty: int
    lit_executed_qty: int
    unfilled_qty: int
    nbbo_midpoint_price: float
    average_fill_price: float
    total_price_improvement_usd: float  # Signed, vs the far touch of the NBBO
    child_routes: List[ChildOrderRoute]
    status: str  # COMPLETE | PARTIALLY_FILLED | INSUFFICIENT_LIQUIDITY
    audit_notes: str
    nbbo_bid: float = 0.0
    nbbo_ask: float = 0.0
    fill_model: str = FILL_MODEL_ID
    requires_iso_marking: bool = False
    dark_skip_reasons: List[str] = field(default_factory=list)


class LiquiditySeekingEngine:
    """Two-stage liquidity-seeking planner: dark midpoint sweep, then lit sweep.

    Stage 1 pings dark ATSs at the lit NBBO midpoint with IOC + MinQty; stage 2
    sweeps the residual across lit exchanges in strict price priority. Both
    stages respect the parent limit price, and neither stage sends an order the
    parent limit does not permit. See the module docstring for the regulatory
    grounding and the limitations of the fill model.
    """

    def compute_nbbo(self, venues: List[VenueBookSpec]) -> Tuple[float, float, float]:
        """Derive the synthetic NBB, NBO, and midpoint from the lit books.

        Only lit venues **quoting size** contribute: a zero-size quote is not a
        quotation and must not set the touch or skew the midpoint.

        Raises:
            LiquiditySeekingError: if no lit venue quotes size on either side,
                or if the resulting book is crossed (bid > ask), whose midpoint
                is not a usable reference price -- see 17 CFR 242.610(e).
        """
        if not venues:
            raise LiquiditySeekingError("No venues provided to compute NBBO.")

        seen_ids = set()
        for venue in venues:
            if venue.venue_id in seen_ids:
                raise LiquiditySeekingError(
                    f"Duplicate venue_id {venue.venue_id!r}; each venue book must "
                    "appear once or its liquidity is double-counted."
                )
            seen_ids.add(venue.venue_id)

        bids = [v.bid_price for v in venues if v.venue_type == "LIT" and v.bid_qty > 0]
        asks = [v.ask_price for v in venues if v.venue_type == "LIT" and v.ask_qty > 0]
        if not bids:
            raise LiquiditySeekingError(
                "No lit venue is quoting bid size; cannot derive a National Best Bid."
            )
        if not asks:
            raise LiquiditySeekingError(
                "No lit venue is quoting offer size; cannot derive a National Best "
                "Offer."
            )

        best_bid = max(bids)
        best_ask = min(asks)

        if best_bid > best_ask + PRICE_EPSILON:
            raise LiquiditySeekingError(
                f"Crossed NBBO (bid {best_bid} > ask {best_ask}). The midpoint of a "
                "crossed book is not a valid reference price; treat this as a market "
                "data integrity failure (cf. 17 CFR 242.610(e)) and re-snapshot."
            )
        if abs(best_ask - best_bid) <= PRICE_EPSILON:
            logger.warning(
                "Locked NBBO at %.4f: the midpoint equals both touches, so the dark "
                "stage offers no price improvement.", best_bid
            )

        midpoint = round((best_bid + best_ask) / 2.0, 6)
        return best_bid, best_ask, midpoint

    def _limit_permits(self, side: str, price: float, limit_price: float) -> bool:
        """True when executing ``side`` at ``price`` respects the parent limit."""
        if side == "BUY":
            return price <= limit_price + PRICE_EPSILON
        return price >= limit_price - PRICE_EPSILON

    def execute_liquidity_seeking(
        self,
        order: ParentOrderSpec,
        venues: List[VenueBookSpec],
    ) -> LiquiditySeekingReport:
        """Plan the two-stage sweep and return the audit report.

        Stage 1 -- dark midpoint: dark venues are tried highest historical fill
        rate first. A venue is pinged only when the routed quantity clears
        ``min_dark_fill_qty``; the projected fill is taken as zero unless it
        also clears that floor, matching IOC + ``MinQty(110)`` semantics where
        an execution below MinQty does not happen. The whole stage is skipped
        when the parent limit does not permit the midpoint.

        Stage 2 -- lit sweep: venues are swept in strict price priority, so the
        full displayed size of every better-priced protected quotation is taken
        before any inferior price. Venues whose price breaches the parent limit
        are skipped, never repriced.

        Returns:
            A ``LiquiditySeekingReport`` whose quantities are projections under
            ``FILL_MODEL_ID``, with ``total_executed_qty + unfilled_qty ==
            target_quantity`` held as an invariant.

        Raises:
            LiquiditySeekingError: on invalid venue books or an unusable NBBO.
                A parent limit that permits no execution is *not* an error --
                it returns an ``INSUFFICIENT_LIQUIDITY`` report.
        """
        best_bid, best_ask, nbbo_midpoint = self.compute_nbbo(venues)
        side = order.side

        remaining_qty = order.target_quantity
        child_routes: List[ChildOrderRoute] = []
        dark_skip_reasons: List[str] = []

        dark_exec_qty = 0
        lit_exec_qty = 0
        total_price_improvement = 0.0
        total_fill_notional = 0.0
        requires_iso = False

        # --- STAGE 1: dark ATS midpoint sweep -------------------------------
        # The parent limit binds on the midpoint too. Historically this raised
        # for a BUY and was unchecked for a SELL, which let a SELL fill below
        # the client's limit; the gate is now symmetric and skips the stage
        # instead of rejecting an order the lit stage may still be able to work.
        far_touch = best_ask if side == "BUY" else best_bid
        if not self._limit_permits(side, nbbo_midpoint, order.limit_price):
            dark_skip_reasons.append(SKIP_LIMIT_THROUGH_MIDPOINT)
            logger.info(
                "[%s] Dark stage skipped: %s limit %.4f does not permit the NBBO "
                "midpoint %.4f.", order.symbol, side, order.limit_price, nbbo_midpoint,
            )
        else:
            dark_venues = sorted(
                (v for v in venues if v.venue_type == "DARK"),
                key=lambda v: v.historical_fill_rate,
                reverse=True,
            )
            for d_venue in dark_venues:
                if remaining_qty <= 0:
                    break

                avail_liquidity = d_venue.ask_qty if side == "BUY" else d_venue.bid_qty
                route_qty = min(remaining_qty, avail_liquidity)

                # Anti-pinging gate on the *routed* quantity. Gating on venue
                # liquidity alone still lets a small residual leak the parent's
                # intent into a deep pool.
                if route_qty < order.min_dark_fill_qty:
                    dark_skip_reasons.append(
                        f"{d_venue.venue_id}:{SKIP_BELOW_MIN_DARK_QTY}"
                    )
                    continue

                # IOC + MinQty either executes at least MinQty or does not
                # execute, so a projected fill below the floor is no fill.
                projected_fill = int(route_qty * d_venue.historical_fill_rate)
                projected_fill = min(projected_fill, remaining_qty)
                if projected_fill < max(order.min_dark_fill_qty, 1):
                    dark_skip_reasons.append(
                        f"{d_venue.venue_id}:{SKIP_EXPECTED_FILL_BELOW_MIN_QTY}"
                    )
                    continue

                exec_price = nbbo_midpoint
                # Signed, not absolute: on a locked book the midpoint equals the
                # touch and the improvement is legitimately zero.
                per_share = (
                    far_touch - exec_price if side == "BUY" else exec_price - far_touch
                )
                price_improvement = per_share * projected_fill

                dark_exec_qty += projected_fill
                remaining_qty -= projected_fill
                total_fill_notional += projected_fill * exec_price
                total_price_improvement += price_improvement

                child_routes.append(
                    ChildOrderRoute(
                        venue_id=d_venue.venue_id,
                        venue_type="DARK",
                        side=side,
                        quantity=route_qty,
                        price=exec_price,
                        execution_stage="STAGE1_DARK_MIDPOINT",
                        filled_quantity=projected_fill,
                        price_improvement_usd=round(price_improvement, 2),
                        min_qty_instruction=min(order.min_dark_fill_qty, route_qty),
                        # A one-cent spread midpoints to a half cent. Rule 612
                        # bars *accepting* a sub-penny-priced order, so peg it.
                        price_instruction="MIDPOINT_PEG",
                        requires_iso_marking=False,
                    )
                )

        # --- STAGE 2: lit exchange sweep in price priority -------------------
        # Sorting by price is what keeps the sweep off a trade-through: an
        # inferior venue is only reached once the full displayed size of every
        # better-priced protected quotation has been taken, which is exactly the
        # condition Rule 611(b)(5)-(6) requires for the ISO exception.
        lit_venues = [v for v in venues if v.venue_type == "LIT"]
        if side == "BUY":
            lit_venues.sort(key=lambda v: v.ask_price)
        else:
            lit_venues.sort(key=lambda v: -v.bid_price)

        for l_venue in lit_venues:
            if remaining_qty <= 0:
                break

            avail_lit_qty = l_venue.ask_qty if side == "BUY" else l_venue.bid_qty
            exec_price = l_venue.ask_price if side == "BUY" else l_venue.bid_price
            if avail_lit_qty <= 0:
                continue
            if not self._limit_permits(side, exec_price, order.limit_price):
                continue

            fill_qty = min(remaining_qty, avail_lit_qty)

            inferior_to_nbbo = (
                exec_price > best_ask + PRICE_EPSILON
                if side == "BUY"
                else exec_price < best_bid - PRICE_EPSILON
            )
            if inferior_to_nbbo:
                requires_iso = True
                logger.warning(
                    "[%s] Lit route to %s at %.4f is inferior to the protected NBBO "
                    "(%.4f x %.4f); the child must be marked ISO (ExecInst(18)='f') "
                    "and routed simultaneously with the better-priced quotations.",
                    order.symbol, l_venue.venue_id, exec_price, best_bid, best_ask,
                )

            per_share = (
                far_touch - exec_price if side == "BUY" else exec_price - far_touch
            )
            price_improvement = per_share * fill_qty

            lit_exec_qty += fill_qty
            remaining_qty -= fill_qty
            total_fill_notional += fill_qty * exec_price
            total_price_improvement += price_improvement

            child_routes.append(
                ChildOrderRoute(
                    venue_id=l_venue.venue_id,
                    venue_type="LIT",
                    side=side,
                    quantity=fill_qty,
                    price=exec_price,
                    execution_stage="STAGE2_LIT_SWEEP",
                    filled_quantity=fill_qty,
                    price_improvement_usd=round(price_improvement, 2),
                    min_qty_instruction=0,
                    price_instruction="LIMIT",
                    requires_iso_marking=inferior_to_nbbo,
                )
            )

        total_exec_qty = dark_exec_qty + lit_exec_qty
        avg_price = (
            round(total_fill_notional / float(total_exec_qty), 4)
            if total_exec_qty > 0
            else 0.0
        )

        if total_exec_qty + remaining_qty != order.target_quantity:  # pragma: no cover
            raise LiquiditySeekingError(
                f"Quantity conservation broken: projected {total_exec_qty} + residual "
                f"{remaining_qty} != parent {order.target_quantity}."
            )

        if remaining_qty == 0:
            status = "LIQUIDITY_SEEKING_COMPLETE"
            notes = (
                f"LIQUIDITY SEEKING COMPLETE [{order.symbol}]: Projected "
                f"{total_exec_qty:,}/{order.target_quantity:,} shares "
                f"(Dark = {dark_exec_qty:,}, Lit = {lit_exec_qty:,}) @ Avg Price "
                f"${avg_price:,.4f}. Price Improvement = "
                f"${total_price_improvement:,.2f} vs the far touch."
            )
            logger.info(notes)
        elif total_exec_qty > 0:
            status = "PARTIALLY_FILLED"
            notes = (
                f"LIQUIDITY SEEKING PARTIAL [{order.symbol}]: Projected "
                f"{total_exec_qty:,}/{order.target_quantity:,} shares. "
                f"Unfilled = {remaining_qty:,} shares."
            )
            logger.warning(notes)
        else:
            status = "INSUFFICIENT_LIQUIDITY"
            notes = (
                f"LIQUIDITY SEEKING REJECT [{order.symbol}]: Zero projected fills "
                f"across lit and dark venues (limit ${order.limit_price:,.4f} vs NBBO "
                f"${best_bid:,.4f} x ${best_ask:,.4f})."
            )
            logger.error(notes)

        if requires_iso:
            notes = (
                f"{notes} ISO MARKING REQUIRED: at least one lit child is priced "
                "inferior to the protected NBBO."
            )

        return LiquiditySeekingReport(
            symbol=order.symbol,
            total_requested_qty=order.target_quantity,
            total_executed_qty=total_exec_qty,
            dark_executed_qty=dark_exec_qty,
            lit_executed_qty=lit_exec_qty,
            unfilled_qty=remaining_qty,
            nbbo_midpoint_price=nbbo_midpoint,
            average_fill_price=avg_price,
            total_price_improvement_usd=round(total_price_improvement, 2),
            child_routes=child_routes,
            status=status,
            audit_notes=notes,
            nbbo_bid=best_bid,
            nbbo_ask=best_ask,
            fill_model=FILL_MODEL_ID,
            requires_iso_marking=requires_iso,
            dark_skip_reasons=dark_skip_reasons,
        )
