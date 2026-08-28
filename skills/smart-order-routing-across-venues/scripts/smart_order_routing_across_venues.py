"""Multi-venue Smart Order Routing (SOR) planner for US NMS stocks.

Builds a deterministic, auditable child-order routing plan that sweeps the best
*accessible* displayed price level across lit venues, ranks equally-priced venues
by fee-inclusive net price, and never plans an execution at a price inferior to a
better-priced venue it can see.

Scope: US NMS stocks (17 CFR 242.600(b) "NMS stock"). SEC Regulation NMS Rule 611
(Order Protection Rule) obliges *trading centers* -- not routing brokers -- to
prevent trade-throughs of protected quotations. A broker-dealer's own duty is best
execution (FINRA Rule 5310). Listed options are NOT covered by Rule 611; they fall
under the Options Order Protection and Locked/Crossed Market Plan.

This module plans routes. It does not send orders, model fills, or track order
state. It is intentionally free of I/O and shared mutable state so the plan is
reproducible from its inputs alone.
"""

import logging
import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# SEC Rule 610(c) access fee cap for protected quotations in NMS stocks priced
# >= $1.00. The 2024 Reg NMS amendments lower this to $0.0010/share; the
# compliance date has been deferred (see references/standards.md), so $0.0030
# remains the operative cap. This is a configurable default, not a hard rule.
DEFAULT_ACCESS_FEE_CAP_PER_SHARE = 0.0030

# Rule 612 minimum pricing increment for NMS stocks quoted at $1.00 or more.
# Sub-$1.00 stocks quote in $0.0001; callers must pass the correct increment.
DEFAULT_PRICE_INCREMENT = 0.01

# Sub-tick price penalty per millisecond of venue latency, used only to break
# ties between venues whose net prices are otherwise identical. A house
# heuristic for ranking, NOT a market rule or a cost estimate.
DEFAULT_LATENCY_PENALTY_PER_MS = 1e-5


@dataclass
class SmartOrderRoutingAcrossVenuesConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100
    access_fee_cap_per_share: float = DEFAULT_ACCESS_FEE_CAP_PER_SHARE
    latency_penalty_per_ms: float = DEFAULT_LATENCY_PENALTY_PER_MS


@dataclass
class VenueQuote:
    """Top-of-book quote from a single venue.

    ``taker_fee_per_share`` is the fee charged for removing liquidity.
    ``maker_rebate_per_share`` is recorded for completeness but is NOT used by
    the router: this engine plans liquidity-*taking* sweeps, where the taker fee
    applies and the rebate does not. Rebate-aware passive posting is a different
    decision -- see ``post-only-and-maker-taker-fee-optimization``.
    """
    venue_id: str                         # e.g., 'NASDAQ', 'NYSE', 'BATS', 'IEX'
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    taker_fee_per_share: float = 0.0030   # $0.0030/share taker fee
    maker_rebate_per_share: float = 0.0020# $0.0020/share maker rebate (unused by router)
    latency_ms: float = 1.5


@dataclass
class ChildOrderRoute:
    """One venue-targeted child order. ``limit_price`` is the venue's displayed price."""
    child_order_id: str
    target_venue_id: str
    side: str
    quantity: float
    limit_price: float
    effective_net_price: float           # limit_price -/+ taker fee (always fee-inclusive)
    audit_notes: str
    taker_fee_usd: float = 0.0           # quantity * taker_fee_per_share


@dataclass
class SORRoutingPlan:
    """Routing plan for one parent order.

    ``nbbo_price``  -- best price this plan can actually route against, i.e. the
        best price among venues showing non-zero displayed size on the relevant
        side. Zero-size quotes are excluded because there is nothing to execute
        against them.
    ``best_quoted_price`` -- best displayed price across *all* supplied quotes,
        including zero-size ones. A gap between this and ``nbbo_price`` means a
        better-priced quote had no size, which usually indicates stale data.
    ``net_expected_cost_usd`` -- expected cash flow, always positive: cash paid
        for a BUY, cash received (net of taker fees) for a SELL.
    ``iso_required_for_remainder`` -- True when quantity remains unrouted. Any
        attempt to fill that remainder at a price inferior to a protected
        quotation must be marked as an Intermarket Sweep Order
        (17 CFR 242.600(b)(47)) and accompanied by simultaneous
        full-displayed-size orders to every superior protected quotation.
    """
    parent_order_id: str
    symbol: str
    side: str
    total_quantity: float
    nbbo_price: float
    routes: List[ChildOrderRoute]
    unrouted_quantity: float
    net_expected_cost_usd: float
    audit_notes: str
    best_quoted_price: float = 0.0
    iso_required_for_remainder: bool = False
    locked_or_crossed: bool = False
    total_taker_fee_usd: float = 0.0


def _tick_position(price: float, price_increment: float) -> float:
    """Price expressed in ticks, with float reconstruction noise removed.

    ``150.00 / 0.01`` is ``14999.999999999998``. Rounding to 9 decimal places
    recovers the intended ``15000.0`` while leaving a genuinely off-grid price
    (``149.996 / 0.01 -> 14999.6``) distinguishable from an on-grid one.
    """
    return round(price / price_increment, 9)


def _price_to_ticks(price: float, price_increment: float) -> int:
    """Quantize a price onto the venue tick grid.

    Venue feeds reconstruct the same quoted price along different float paths
    (``10007 / 100.0`` vs ``10007 * 0.01``), which differ in the last bit. Exact
    float equality would treat those as two price levels and silently drop one
    venue's liquidity, so all price comparisons happen on integer ticks.
    """
    return int(round(_tick_position(price, price_increment)))


def _limit_ticks(limit_price: float, price_increment: float, is_buy: bool) -> int:
    """Quantize a parent limit price so rounding can never loosen the bound.

    A buy limit rounds *down* and a sell limit rounds *up*, so an off-grid limit
    (``149.996`` on a penny-tick stock) blocks the $150.00 offer instead of being
    rounded up into permission to pay more than the caller asked for.
    """
    position = _tick_position(limit_price, price_increment)
    return int(math.floor(position)) if is_buy else int(math.ceil(position))


def _require_finite(value: Any, label: str, *, positive: bool = False,
                    non_negative: bool = False) -> float:
    """Validate a numeric input, raising ``ValueError`` with a locating label.

    Strings are rejected outright rather than coerced: ``float("300")`` would pass
    validation and then raise an opaque ``TypeError`` at the first ``"300" > 0``
    comparison, far from the field that caused it.
    """
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a number, not a string, got {value!r}.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {numeric!r}.")
    if positive and numeric <= 0:
        raise ValueError(f"{label} must be > 0, got {numeric!r}.")
    if non_negative and numeric < 0:
        raise ValueError(f"{label} must be >= 0, got {numeric!r}.")
    return numeric


class SmartOrderRoutingAcrossVenuesEngine:
    """Multi-venue SOR planner for US NMS stocks.

    Consolidates the best accessible displayed price across venues, ranks
    equally-priced venues by fee-inclusive net price (with a latency tiebreaker),
    and slices the parent order across those venues. It never plans a route at a
    price inferior to a venue it can see quoting better with displayed size.
    """

    def __init__(self, config: Optional[SmartOrderRoutingAcrossVenuesConfig] = None):
        self.config = config or SmartOrderRoutingAcrossVenuesConfig(enabled=True)
        self.state = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Legacy evaluate method retained for 100% backward compatibility."""
        if not self.config.enabled:
            return []
        if market_data.get("price", 0) > self.config.threshold:
            order = {"symbol": market_data.get("symbol", "UNKNOWN"), "qty": self.config.size, "type": "LIMIT"}
            self.orders.append(order)
            return [order]
        return []

    def _validate_quotes(self, venue_quotes: List[VenueQuote]) -> List[VenueQuote]:
        """Validate quotes and return copies with every numeric field as ``float``.

        Two jobs, both necessary before any arithmetic runs:

        * Reject malformed input. A single NaN price silently propagates through
          ``min()``/``max()`` and produces a plan with a NaN limit price, so
          quotes are validated up front rather than filtered.
        * Normalize numeric types. Callers legitimately carry prices as ``Decimal``
          or as numpy scalars; ``Decimal / float`` raises ``TypeError`` deep inside
          the tick quantizer. Coercing once here keeps the routing path
          type-uniform. The caller's objects are copied, never mutated.
        """
        if not venue_quotes:
            raise ValueError("No venue quotes provided for smart order routing.")
        seen: Dict[str, int] = {}
        normalized: List[VenueQuote] = []
        for idx, vq in enumerate(venue_quotes):
            where = f"venue_quotes[{idx}]"
            if not isinstance(vq.venue_id, str) or not vq.venue_id.strip():
                raise ValueError(f"{where}.venue_id must be a non-empty string.")
            if vq.venue_id in seen:
                raise ValueError(
                    f"{where}.venue_id {vq.venue_id!r} duplicates venue_quotes[{seen[vq.venue_id]}]; "
                    "supply one consolidated top-of-book quote per venue."
                )
            seen[vq.venue_id] = idx
            bid_qty = _require_finite(vq.bid_qty, f"{where}.bid_qty ({vq.venue_id})", non_negative=True)
            ask_qty = _require_finite(vq.ask_qty, f"{where}.ask_qty ({vq.venue_id})", non_negative=True)
            # A side with no displayed size may carry a placeholder price of 0;
            # a side that is actually quoting must carry a real positive price.
            bid_price = _require_finite(vq.bid_price, f"{where}.bid_price ({vq.venue_id})",
                                        positive=bid_qty > 0, non_negative=True)
            ask_price = _require_finite(vq.ask_price, f"{where}.ask_price ({vq.venue_id})",
                                        positive=ask_qty > 0, non_negative=True)
            taker_fee = _require_finite(vq.taker_fee_per_share,
                                        f"{where}.taker_fee_per_share ({vq.venue_id})")
            latency = _require_finite(vq.latency_ms, f"{where}.latency_ms ({vq.venue_id})",
                                      non_negative=True)
            if bid_qty > 0 and ask_qty > 0 and bid_price >= ask_price:
                raise ValueError(
                    f"{where} ({vq.venue_id}) is locked or crossed within its own book: "
                    f"bid {bid_price} >= ask {ask_price}. A single venue cannot "
                    "display both sides at the same or crossed prices."
                )
            normalized.append(replace(
                vq, bid_price=bid_price, bid_qty=bid_qty,
                ask_price=ask_price, ask_qty=ask_qty,
                taker_fee_per_share=taker_fee, latency_ms=latency,
            ))
        return normalized

    def _detect_locked_or_crossed(self, venue_quotes: List[VenueQuote],
                                  price_increment: float) -> bool:
        """True when the consolidated book is locked (NBB == NBO) or crossed (NBB > NBO)."""
        bids = [vq.bid_price for vq in venue_quotes if vq.bid_qty > 0]
        asks = [vq.ask_price for vq in venue_quotes if vq.ask_qty > 0]
        if not bids or not asks:
            return False
        nbb_ticks = _price_to_ticks(max(bids), price_increment)
        nbo_ticks = _price_to_ticks(min(asks), price_increment)
        return nbb_ticks >= nbo_ticks

    def route_parent_order(
        self,
        parent_order_id: str,
        symbol: str,
        side: str,                            # 'BUY' or 'SELL'
        quantity: float,
        venue_quotes: List[VenueQuote],
        fee_aware: bool = True,
        limit_price: Optional[float] = None,
        price_increment: float = DEFAULT_PRICE_INCREMENT,
    ) -> SORRoutingPlan:
        """Build a child-order routing plan for one parent order.

        The plan targets a single price level -- the best price with displayed
        size across venues -- and splits it across every venue quoting that
        level, best fee-inclusive net price first. Quantity that the level cannot
        absorb is returned as ``unrouted_quantity`` rather than swept into worse
        prices; see ``iso_required_for_remainder``.

        Args:
            side: 'BUY' or 'SELL' (case-insensitive). Anything else raises.
            quantity: parent order size; must be finite and strictly positive.
            fee_aware: when True, venues at the same price are ranked by
                fee-inclusive net price. This flag affects **ranking only** --
                ``effective_net_price``, ``taker_fee_usd`` and
                ``net_expected_cost_usd`` always include taker fees, because the
                fee is paid whether or not the router optimized for it.
            limit_price: hard price bound. No route is planned above it for a BUY
                or below it for a SELL. ``None`` means the parent order is
                unbounded, which for a marketable sweep is a deliberate choice.
            price_increment: venue tick size used for price comparison. Default
                $0.01 (Rule 612, NMS stocks >= $1.00). Sub-$1.00 stocks quote in
                $0.0001 and must pass it explicitly.

        Raises:
            ValueError: on an empty or malformed quote list, an unrecognized
                side, a non-positive or non-finite quantity, or a non-positive
                price increment or limit price.
        """
        normalized_side = str(side).strip().upper()
        if normalized_side not in ("BUY", "SELL"):
            raise ValueError(
                f"side must be 'BUY' or 'SELL', got {side!r}. "
                "An unrecognized side previously defaulted to the sell path and would "
                "have quoted the bid for a buy order."
            )
        quantity = _require_finite(quantity, "quantity", positive=True)
        price_increment = _require_finite(price_increment, "price_increment", positive=True)
        if limit_price is not None:
            limit_price = _require_finite(limit_price, "limit_price", positive=True)
        venue_quotes = self._validate_quotes(venue_quotes)

        is_buy = normalized_side == "BUY"
        locked_or_crossed = self._detect_locked_or_crossed(venue_quotes, price_increment)
        if locked_or_crossed:
            logger.warning(
                "SOR [%s] (%s): consolidated book is locked or crossed across venues; "
                "quotes may be stale. Routing on possibly stale prices.",
                parent_order_id, symbol,
            )

        def book_price(vq: VenueQuote) -> float:
            return vq.ask_price if is_buy else vq.bid_price

        def book_qty(vq: VenueQuote) -> float:
            return vq.ask_qty if is_buy else vq.bid_qty

        # 1. Consolidate the best price. Zero-size quotes are excluded: there is
        #    nothing to execute against them, and routing "around" them to a
        #    worse-priced venue on their behalf was the old fallback's bug.
        quoted = [vq for vq in venue_quotes if book_price(vq) > 0]
        best_quoted_price = 0.0
        if quoted:
            best_quoted_price = (
                min(book_price(vq) for vq in quoted) if is_buy
                else max(book_price(vq) for vq in quoted)
            )
        accessible = [vq for vq in venue_quotes if book_qty(vq) > 0]

        if not accessible:
            notes = (
                f"SOR ROUTING PLAN [{parent_order_id}] ({symbol}): {normalized_side} {quantity} shares. "
                f"NO ROUTE - no venue shows displayed size on the {'ask' if is_buy else 'bid'} side. "
                f"Best quoted price = ${best_quoted_price}. Entire order unrouted."
            )
            logger.warning(notes)
            return SORRoutingPlan(
                parent_order_id=parent_order_id, symbol=symbol, side=normalized_side,
                total_quantity=quantity, nbbo_price=best_quoted_price, routes=[],
                unrouted_quantity=quantity, net_expected_cost_usd=0.0, audit_notes=notes,
                best_quoted_price=best_quoted_price, iso_required_for_remainder=True,
                locked_or_crossed=locked_or_crossed,
            )

        nbbo_price = (
            min(book_price(vq) for vq in accessible) if is_buy
            else max(book_price(vq) for vq in accessible)
        )
        nbbo_ticks = _price_to_ticks(nbbo_price, price_increment)

        if _price_to_ticks(best_quoted_price, price_increment) != nbbo_ticks:
            logger.warning(
                "SOR [%s] (%s): best quoted price $%s has zero displayed size; "
                "routable price is $%s. Check for a stale quote before routing.",
                parent_order_id, symbol, best_quoted_price, nbbo_price,
            )

        # Tick-quantized equality: identically-quoted venues must not be split by
        # float representation noise (100.07 vs 100.07000000000001).
        eligible_quotes = [
            vq for vq in accessible
            if _price_to_ticks(book_price(vq), price_increment) == nbbo_ticks
        ]

        # 2. Enforce the parent limit price. Never plan a route through it.
        if limit_price is not None:
            limit_ticks = _limit_ticks(limit_price, price_increment, is_buy)
            breached = nbbo_ticks > limit_ticks if is_buy else nbbo_ticks < limit_ticks
            if breached:
                notes = (
                    f"SOR ROUTING PLAN [{parent_order_id}] ({symbol}): {normalized_side} {quantity} shares. "
                    f"NO ROUTE - best routable price ${nbbo_price} is outside the parent limit "
                    f"${limit_price}. Entire order unrouted."
                )
                logger.info(notes)
                return SORRoutingPlan(
                    parent_order_id=parent_order_id, symbol=symbol, side=normalized_side,
                    total_quantity=quantity, nbbo_price=nbbo_price, routes=[],
                    unrouted_quantity=quantity, net_expected_cost_usd=0.0, audit_notes=notes,
                    best_quoted_price=best_quoted_price, iso_required_for_remainder=True,
                    locked_or_crossed=locked_or_crossed,
                )

        # 3. Rank equally-priced venues by fee-inclusive net price. venue_id is
        #    the final tiebreaker so the plan is reproducible across runs.
        latency_penalty = self.config.latency_penalty_per_ms

        def score_venue(vq: VenueQuote) -> float:
            base_price = book_price(vq)
            fee = vq.taker_fee_per_share if fee_aware else 0.0
            if is_buy:
                return base_price + fee + (vq.latency_ms * latency_penalty)
            return -(base_price - fee - (vq.latency_ms * latency_penalty))

        sorted_venues = sorted(eligible_quotes, key=lambda vq: (score_venue(vq), vq.venue_id))

        # 4. Slice across venues, taking full displayed size before moving on.
        remaining_qty = quantity
        routes: List[ChildOrderRoute] = []
        total_net_cost = 0.0
        total_fee = 0.0

        for idx, vq in enumerate(sorted_venues):
            if remaining_qty <= 0:
                break

            avail_qty = book_qty(vq)
            slice_qty = min(remaining_qty, avail_qty)
            raw_price = book_price(vq)
            fee_usd = slice_qty * vq.taker_fee_per_share
            net_price = (
                (raw_price + vq.taker_fee_per_share) if is_buy
                else (raw_price - vq.taker_fee_per_share)
            )

            if raw_price >= 1.0 and vq.taker_fee_per_share > self.config.access_fee_cap_per_share:
                logger.warning(
                    "SOR [%s]: venue %s taker fee $%.4f/share exceeds the configured Rule 610(c) "
                    "access fee cap $%.4f/share. Verify the fee schedule and whether this venue's "
                    "quote is a protected quotation.",
                    parent_order_id, vq.venue_id, vq.taker_fee_per_share,
                    self.config.access_fee_cap_per_share,
                )

            child_id = f"{parent_order_id}_CHILD_{idx+1}_{vq.venue_id}"
            notes = f"CHILD ROUTE: {slice_qty} shares to {vq.venue_id} @ ${raw_price} (Net ${net_price:.4f})."

            routes.append(ChildOrderRoute(
                child_order_id=child_id,
                target_venue_id=vq.venue_id,
                side=normalized_side,
                quantity=slice_qty,
                limit_price=raw_price,
                effective_net_price=net_price,
                audit_notes=notes,
                taker_fee_usd=fee_usd,
            ))

            total_net_cost += slice_qty * net_price
            total_fee += fee_usd
            remaining_qty -= slice_qty

        unrouted = remaining_qty
        plan_notes = (
            f"SOR ROUTING PLAN [{parent_order_id}] ({symbol}): {normalized_side} {quantity} shares. "
            f"Routable best price = ${nbbo_price}, Child Routes = {len(routes)}, "
            f"Unrouted = {unrouted}, Total Net {'Cost' if is_buy else 'Proceeds'} = ${total_net_cost:,.2f}."
        )
        if unrouted > 0:
            plan_notes += (
                " REMAINDER: any fill of the unrouted balance at a price inferior to a protected "
                "quotation must be ISO-marked (17 CFR 242.600(b)(47)) with simultaneous "
                "full-displayed-size orders to every superior protected quotation."
            )

        logger.info(plan_notes)

        return SORRoutingPlan(
            parent_order_id=parent_order_id,
            symbol=symbol,
            side=normalized_side,
            total_quantity=quantity,
            nbbo_price=nbbo_price,
            routes=routes,
            unrouted_quantity=unrouted,
            net_expected_cost_usd=round(total_net_cost, 2),
            audit_notes=plan_notes,
            best_quoted_price=best_quoted_price,
            iso_required_for_remainder=unrouted > 0,
            locked_or_crossed=locked_or_crossed,
            total_taker_fee_usd=round(total_fee, 4),
        )
