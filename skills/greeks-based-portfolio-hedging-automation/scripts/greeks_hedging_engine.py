"""
greeks-based-portfolio-hedging-automation: portfolio net Greeks aggregator, risk
tolerance breach evaluator, and automated hedge order generator.

Scaling conventions (these are the whole point of the module - get them wrong and
every downstream order is wrong by the multiplier):

    position dollar delta  = quantity * multiplier * delta * underlying_price
    position dollar vega   = quantity * multiplier * vega
    beta-weighted delta    = beta * position dollar delta

``delta`` and ``vega`` are *per unit of the deliverable* (per share for a standard
equity option), never per contract. ``multiplier`` converts contract count to
deliverable units and is a **required** field: it is 100 for a standard US equity
option ("Generally, 100 shares of one of the exchange-traded products", Cboe Equity
Options Specifications), but it is NOT universally 100. OCC adjusts the deliverable
after splits, mergers and special distributions and publishes the new terms in an
Information Memo (OCC Infomemo #26853, "Contract Adjustments"), so an adjusted
contract may deliver 10, 150 or a basket. Index futures carry their own multiplier
($50 per index point for CME E-mini S&P 500), and NSE revises index-derivative lot
sizes periodically to stay above SEBI's minimum contract value. Read the multiplier
from the contract master; never hard-code it.

``vega`` is quoted per 1 percentage point (1 vol point) of implied volatility, the
standard listed-options convention ("Vega indicates an absolute change in option
value for a 1% change in volatility" - OIC, *Vega*). Net vega USD is therefore the
dollar P&L of a 1-vol-point parallel shift, with the sign carried by ``quantity``
(short positions are negative). There is no extra factor of 100 anywhere.

Cross-underlying aggregation is beta-weighted. Summing raw dollar delta across
different underlyings and hedging the total with one index proxy silently assumes
every name has a beta of 1.0 to that proxy. Cboe's documented procedure converts a
position's dollar sensitivity into index-equivalent terms via its beta before
sizing the hedge ("How to Right-size Hedges Via Beta Weighting with XSP Options").
``OptionPosition.beta`` defaults to 1.0, which reproduces the naive behaviour, but
it must be measured against the *delta hedge instrument's* underlying.

Order of operations: the vega leg is sized first because an options vega hedge
injects delta into the book; the delta leg is then sized on the post-vega-hedge
exposure. Reversing the two leaves a delta hole the size of the vega overlay.

Limitations (documented, deliberate):

- **Delta and vega only.** Gamma, theta and rho are not aggregated, so a
  delta-neutral book here can still be gamma-short. A delta hedge set once is stale
  as soon as spot moves; see ``real-time-greeks-recalculation-on-market-moves``.
- **Static, first-order, single-snapshot.** Greeks are taken as given inputs. The
  engine performs no pricing and has no view on whether the supplied Greeks are
  stale, mispriced or drawn from an inconsistent vol surface.
- **Betas are inputs, not estimates.** No beta is computed, and beta itself is
  unstable in a crash, which is exactly when the hedge matters most.
- **Recommendation only.** Hedge orders are returned, not sent. Routing them still
  requires the same pre-trade risk controls, idempotency and kill-switch coverage as
  any strategy order (``order-placement-idempotency``,
  ``execution-algorithm-kill-switch-integration``).
- **No borrow, margin, tax-lot or position-limit awareness.** A SELL hedge in a cash
  equity may require a locate; this engine does not check one.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Standard US equity/ETP option deliverable (Cboe Equity Options Specifications).
#: Exposed for readability in caller code - deliberately NOT used as a default,
#: because adjusted contracts and non-US products differ.
STANDARD_US_EQUITY_OPTION_MULTIPLIER = 100.0

#: Delta per unit of the deliverable is bounded by +/-1 for options and linear
#: instruments alike. A value outside this band almost always means the feed quoted
#: delta in percent (60 instead of 0.60) - a 100x hedge error if accepted.
MAX_ABS_DELTA_PER_UNIT = 1.0


def _require_finite(value: float, label: str, context: str) -> float:
    """Reject NaN/Inf before it can propagate silently into an order quantity."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{context}: {label} must be finite, got {value!r}")
    return numeric


@dataclass
class OptionPosition:
    """
    One position in the hedged book.

    ``delta``/``vega`` are per unit of the deliverable; ``multiplier`` converts
    contracts to deliverable units and is required (see module docstring). ``beta``
    is the position underlying's beta to the delta hedge instrument's underlying;
    leave it at 1.0 only when they are the same asset.
    """
    symbol: str
    underlying_symbol: str
    quantity: float          # Positive for long, negative for short
    delta: float             # Per deliverable unit, -1.0 .. 1.0
    vega: float              # Per deliverable unit, per 1 vol point
    underlying_price: float
    multiplier: float        # Deliverable units per contract (100 for standard US equity options)
    beta: float = 1.0        # Beta vs the delta hedge instrument's underlying

    def validate(self) -> None:
        """Raise ``ValueError`` on any input that would produce a wrong hedge."""
        ctx = f"position {self.symbol!r}"
        _require_finite(self.quantity, "quantity", ctx)
        _require_finite(self.beta, "beta", ctx)
        _require_finite(self.vega, "vega", ctx)

        delta = _require_finite(self.delta, "delta", ctx)
        if abs(delta) > MAX_ABS_DELTA_PER_UNIT:
            raise ValueError(
                f"{ctx}: delta must be per deliverable unit in [-1, 1], got {delta!r}. "
                "A value like 60 means the feed quoted delta in percent."
            )

        price = _require_finite(self.underlying_price, "underlying_price", ctx)
        if price <= 0.0:
            raise ValueError(f"{ctx}: underlying_price must be > 0, got {price!r}")

        multiplier = _require_finite(self.multiplier, "multiplier", ctx)
        if multiplier <= 0.0:
            raise ValueError(
                f"{ctx}: multiplier must be > 0 (deliverable units per contract, e.g. "
                f"{STANDARD_US_EQUITY_OPTION_MULTIPLIER:.0f} for a standard US equity "
                f"option), got {multiplier!r}"
            )

    @property
    def delta_usd(self) -> float:
        """Raw dollar delta: quantity * multiplier * delta * spot."""
        return self.quantity * self.multiplier * self.delta * self.underlying_price

    @property
    def beta_weighted_delta_usd(self) -> float:
        """Dollar delta expressed in hedge-instrument-equivalent terms."""
        return self.beta * self.delta_usd

    @property
    def vega_usd(self) -> float:
        """Dollar P&L of a 1-vol-point move: quantity * multiplier * vega."""
        return self.quantity * self.multiplier * self.vega


@dataclass
class HedgeInstrument:
    """
    An instrument the engine is allowed to trade to neutralise the book.

    ``price`` is the reference price of the asset the delta applies to - the share
    price for cash equity, the index/futures level for an index hedge, the spot of
    the underlying when hedging with options. ``multiplier`` is the contract's own
    deliverable size and is required for the same reason it is required on a
    position: defaulting it turns a forgotten ``multiplier=50`` on an E-mini into a
    50x oversized hedge with no error. Use 1.0 explicitly for cash equity.
    ``delta_per_unit`` is 1.0 for the underlying itself or a future on it, and the
    option delta when hedging with options. ``vega_per_unit`` is per deliverable
    unit per vol point and is 0.0 for linear instruments.
    """
    symbol: str
    price: float
    multiplier: float               # Deliverable units per contract (1.0 for cash equity)
    delta_per_unit: float = 1.0
    vega_per_unit: float = 0.0

    def validate(self) -> None:
        """Raise ``ValueError`` on terms that would produce a wrong hedge quantity."""
        ctx = f"hedge instrument {self.symbol!r}"
        price = _require_finite(self.price, "price", ctx)
        if price <= 0.0:
            raise ValueError(f"{ctx}: price must be > 0, got {price!r}")

        multiplier = _require_finite(self.multiplier, "multiplier", ctx)
        if multiplier <= 0.0:
            raise ValueError(f"{ctx}: multiplier must be > 0, got {multiplier!r}")

        _require_finite(self.delta_per_unit, "delta_per_unit", ctx)
        _require_finite(self.vega_per_unit, "vega_per_unit", ctx)

    @property
    def delta_usd_per_unit(self) -> float:
        """Dollar delta carried by one contract/share of the hedge instrument."""
        return self.delta_per_unit * self.multiplier * self.price

    @property
    def vega_usd_per_unit(self) -> float:
        """Dollar vega carried by one contract/share of the hedge instrument."""
        return self.vega_per_unit * self.multiplier


@dataclass
class NetGreeksSummary:
    total_positions: int
    net_delta_usd: float                    # Raw sum, NOT beta-weighted
    beta_weighted_delta_usd: float          # Hedge-relevant exposure
    net_vega_usd: float                     # Dollars per 1 vol point
    is_delta_breached: bool                 # vs max_allowed_delta_usd
    is_vega_breached: bool                  # vs max_allowed_vega_usd
    delta_usd_by_underlying: Dict[str, float] = field(default_factory=dict)


@dataclass
class HedgeOrder:
    target_symbol: str
    action: str                     # BUY or SELL
    quantity: float                 # Whole contracts/shares of the hedge instrument
    order_type: str                 # MARKET or LIMIT
    rationale: str
    hedge_leg: str = "DELTA"        # DELTA or VEGA
    delta_usd_offset: float = 0.0   # Dollar delta this order adds to the book
    vega_usd_offset: float = 0.0    # Dollar vega this order adds to the book


@dataclass
class HedgingAuditReport:
    net_greeks: NetGreeksSummary
    recommended_hedge_orders: List[HedgeOrder]
    is_hedging_required: bool               # True when a limit is breached
    message: str
    residual_delta_usd: float = 0.0         # Beta-weighted delta left after the orders
    residual_vega_usd: float = 0.0          # Net vega left after the orders
    is_residual_within_limits: bool = True
    warnings: List[str] = field(default_factory=list)


class GreeksPortfolioHedgingEngine:
    """
    Aggregates portfolio net Delta & Vega exposures, checks risk tolerance limits,
    and calculates offsetting hedge orders.

    Threshold semantics - the two delta numbers do different jobs:

    * ``max_allowed_delta_usd`` is the **trigger**. Nothing is hedged until
      ``|beta-weighted delta| > max_allowed_delta_usd``.
    * ``min_rebalance_delta_usd`` is a **floor on order size**. An order whose own
      dollar delta is below it is suppressed rather than sent, so a breach that only
      just clears the trigger does not pay the spread for a token hedge.

    Hedging every small drift instead of waiting for the trigger is the classic fee
    drag failure mode; using the size floor as the trigger reintroduces it.
    """

    def __init__(
        self,
        max_allowed_delta_usd: float = 50000.0,
        max_allowed_vega_usd: float = 10000.0,
        min_rebalance_delta_usd: float = 10000.0,
        hedge_order_type: str = "MARKET",
    ):
        max_allowed_delta_usd = _require_finite(
            max_allowed_delta_usd, "max_allowed_delta_usd", "engine config")
        max_allowed_vega_usd = _require_finite(
            max_allowed_vega_usd, "max_allowed_vega_usd", "engine config")
        min_rebalance_delta_usd = _require_finite(
            min_rebalance_delta_usd, "min_rebalance_delta_usd", "engine config")

        if max_allowed_delta_usd <= 0.0:
            raise ValueError(f"max_allowed_delta_usd must be > 0, got {max_allowed_delta_usd!r}")
        if max_allowed_vega_usd <= 0.0:
            raise ValueError(f"max_allowed_vega_usd must be > 0, got {max_allowed_vega_usd!r}")
        if min_rebalance_delta_usd < 0.0:
            raise ValueError(f"min_rebalance_delta_usd must be >= 0, got {min_rebalance_delta_usd!r}")
        if hedge_order_type not in ("MARKET", "LIMIT"):
            raise ValueError(f"hedge_order_type must be MARKET or LIMIT, got {hedge_order_type!r}")

        if min_rebalance_delta_usd > max_allowed_delta_usd:
            logger.warning(
                "min_rebalance_delta_usd (%.2f) exceeds max_allowed_delta_usd (%.2f): breaches "
                "between the two will be reported but not hedged.",
                min_rebalance_delta_usd, max_allowed_delta_usd,
            )

        self.max_allowed_delta_usd = max_allowed_delta_usd
        self.max_allowed_vega_usd = max_allowed_vega_usd
        self.min_rebalance_delta_usd = min_rebalance_delta_usd
        self.hedge_order_type = hedge_order_type

    def compute_net_greeks(self, positions: Iterable[OptionPosition]) -> NetGreeksSummary:
        """
        Aggregate the book. Raises ``ValueError`` on any invalid position rather than
        netting a corrupt value into the total.

        The input is materialised first: the book is traversed several times, and a
        one-shot iterator would be exhausted by validation and then aggregate to
        zero - a silently flat book, which is the worst possible failure here.
        """
        positions = list(positions)
        for position in positions:
            position.validate()

        # fsum, not sum: the trigger is a threshold comparison on this total, and a
        # large book that nets near zero must not depend on position ordering.
        net_delta_usd = round(math.fsum(p.delta_usd for p in positions), 2)
        beta_weighted_delta_usd = round(math.fsum(p.beta_weighted_delta_usd for p in positions), 2)
        net_vega_usd = round(math.fsum(p.vega_usd for p in positions), 2)

        by_underlying: Dict[str, float] = {}
        for position in positions:
            by_underlying[position.underlying_symbol] = round(
                by_underlying.get(position.underlying_symbol, 0.0) + position.delta_usd, 2
            )

        return NetGreeksSummary(
            total_positions=len(positions),
            net_delta_usd=net_delta_usd,
            beta_weighted_delta_usd=beta_weighted_delta_usd,
            net_vega_usd=net_vega_usd,
            is_delta_breached=abs(beta_weighted_delta_usd) > self.max_allowed_delta_usd,
            is_vega_breached=abs(net_vega_usd) > self.max_allowed_vega_usd,
            delta_usd_by_underlying=by_underlying,
        )

    def evaluate_and_hedge(
        self,
        positions: List[OptionPosition],
        delta_hedge_instrument: HedgeInstrument,
        vega_hedge_instrument: Optional[HedgeInstrument] = None,
    ) -> HedgingAuditReport:
        """
        Evaluate limits and size the hedge legs.

        The vega leg is sized first: an options vega hedge carries delta, and that
        delta is folded into the exposure the delta leg then neutralises. Both legs
        are truncated toward zero so a hedge can never overshoot past neutral into an
        opposite-signed exposure; whatever is left over is reported in
        ``residual_delta_usd`` / ``residual_vega_usd`` instead of being rounded away.
        """
        delta_hedge_instrument.validate()
        if vega_hedge_instrument is not None:
            vega_hedge_instrument.validate()

        summary = self.compute_net_greeks(positions)
        hedge_orders: List[HedgeOrder] = []
        warnings: List[str] = []

        residual_vega_usd = summary.net_vega_usd
        exposure_to_hedge = summary.beta_weighted_delta_usd

        if summary.is_vega_breached:
            vega_order, residual_vega_usd, delta_injected, vega_warning = self._size_vega_leg(
                summary.net_vega_usd, vega_hedge_instrument
            )
            if vega_order is not None:
                hedge_orders.append(vega_order)
                exposure_to_hedge = round(exposure_to_hedge + delta_injected, 2)
            if vega_warning:
                warnings.append(vega_warning)

        residual_delta_usd = exposure_to_hedge
        if abs(exposure_to_hedge) > self.max_allowed_delta_usd:
            delta_order, residual_delta_usd, delta_warning = self._size_delta_leg(
                exposure_to_hedge, delta_hedge_instrument
            )
            if delta_order is not None:
                hedge_orders.append(delta_order)
            if delta_warning:
                warnings.append(delta_warning)

        is_hedging_required = summary.is_delta_breached or summary.is_vega_breached
        is_residual_within_limits = (
            abs(residual_delta_usd) <= self.max_allowed_delta_usd
            and abs(residual_vega_usd) <= self.max_allowed_vega_usd
        )

        if is_hedging_required:
            msg = (
                f"HEDGE REQUIRED: beta-weighted delta=${summary.beta_weighted_delta_usd:,.2f} "
                f"(limit ${self.max_allowed_delta_usd:,.2f}), net vega=${summary.net_vega_usd:,.2f} "
                f"(limit ${self.max_allowed_vega_usd:,.2f}). Generated {len(hedge_orders)} order(s); "
                f"residual delta=${residual_delta_usd:,.2f}, residual vega=${residual_vega_usd:,.2f}."
            )
            logger.warning(msg)
        else:
            msg = (
                f"WITHIN RISK LIMITS: beta-weighted delta=${summary.beta_weighted_delta_usd:,.2f}, "
                f"net vega=${summary.net_vega_usd:,.2f}. No hedge orders generated."
            )
            logger.info(msg)

        for warning in warnings:
            logger.warning("Hedging gap: %s", warning)

        return HedgingAuditReport(
            net_greeks=summary,
            recommended_hedge_orders=hedge_orders,
            is_hedging_required=is_hedging_required,
            message=msg,
            residual_delta_usd=residual_delta_usd,
            residual_vega_usd=residual_vega_usd,
            is_residual_within_limits=is_residual_within_limits,
            warnings=warnings,
        )

    def _size_vega_leg(
        self,
        net_vega_usd: float,
        instrument: Optional[HedgeInstrument],
    ) -> Tuple[Optional[HedgeOrder], float, float, Optional[str]]:
        """Size the vega overlay. Returns (order, residual_vega, delta_injected, warning)."""
        if instrument is None or instrument.vega_usd_per_unit == 0.0:
            return None, net_vega_usd, 0.0, (
                f"VEGA_BREACH_UNHEDGED: net vega ${net_vega_usd:,.2f} breaches the "
                f"${self.max_allowed_vega_usd:,.2f} limit but no vega-carrying hedge instrument "
                "was supplied. Vega cannot be neutralised with a linear instrument; supply an "
                "options overlay or escalate to a human risk manager."
            )

        raw_units = -net_vega_usd / instrument.vega_usd_per_unit
        units = float(math.trunc(raw_units))
        if units == 0.0:
            return None, net_vega_usd, 0.0, (
                f"VEGA_HEDGE_ROUNDS_TO_ZERO: one unit of {instrument.symbol} carries "
                f"${instrument.vega_usd_per_unit:,.2f} of vega, more than the ${net_vega_usd:,.2f} "
                "breach; no whole-contract hedge can reduce it without overshooting."
            )

        vega_offset = units * instrument.vega_usd_per_unit
        delta_injected = units * instrument.delta_usd_per_unit
        order = HedgeOrder(
            target_symbol=instrument.symbol,
            action="BUY" if units > 0 else "SELL",
            quantity=abs(units),
            order_type=self.hedge_order_type,
            rationale=(
                f"Vega Hedge: offset ${net_vega_usd:,.2f} net vega; "
                f"injects ${delta_injected:,.2f} of delta, netted into the delta leg."
            ),
            hedge_leg="VEGA",
            delta_usd_offset=round(delta_injected, 2),
            vega_usd_offset=round(vega_offset, 2),
        )
        return order, round(net_vega_usd + vega_offset, 2), delta_injected, None

    def _size_delta_leg(
        self,
        exposure_usd: float,
        instrument: HedgeInstrument,
    ) -> Tuple[Optional[HedgeOrder], float, Optional[str]]:
        """Size the delta leg. Returns (order, residual_delta, warning)."""
        delta_usd_per_unit = instrument.delta_usd_per_unit
        if delta_usd_per_unit == 0.0:
            return None, exposure_usd, (
                f"DELTA_HEDGE_INSTRUMENT_HAS_NO_DELTA: {instrument.symbol} carries zero dollar "
                f"delta per unit; ${exposure_usd:,.2f} of exposure cannot be hedged with it."
            )

        raw_units = -exposure_usd / delta_usd_per_unit
        units = float(math.trunc(raw_units))
        if units == 0.0:
            return None, exposure_usd, (
                f"DELTA_BREACH_UNHEDGEABLE: one unit of {instrument.symbol} carries "
                f"${abs(delta_usd_per_unit):,.2f} of delta, more than the ${exposure_usd:,.2f} "
                "exposure; hedging would overshoot past neutral. Use a smaller-denomination "
                "instrument (e.g. Micro futures or the cash underlying) or accept the breach "
                "explicitly."
            )

        delta_offset = units * delta_usd_per_unit
        order_notional = abs(delta_offset)
        if order_notional < self.min_rebalance_delta_usd:
            return None, exposure_usd, (
                f"HEDGE_SUPPRESSED_BELOW_MIN_SIZE: hedge of ${order_notional:,.2f} is below the "
                f"${self.min_rebalance_delta_usd:,.2f} minimum rebalance size; suppressed to avoid "
                f"spread cost. ${exposure_usd:,.2f} of exposure remains unhedged."
            )

        order = HedgeOrder(
            target_symbol=instrument.symbol,
            action="BUY" if units > 0 else "SELL",
            quantity=abs(units),
            order_type=self.hedge_order_type,
            rationale=(
                f"Delta Hedge: offset ${exposure_usd:,.2f} beta-weighted delta exposure "
                f"(limit ${self.max_allowed_delta_usd:,.2f})."
            ),
            hedge_leg="DELTA",
            delta_usd_offset=round(delta_offset, 2),
        )
        return order, round(exposure_usd + delta_offset, 2), None
