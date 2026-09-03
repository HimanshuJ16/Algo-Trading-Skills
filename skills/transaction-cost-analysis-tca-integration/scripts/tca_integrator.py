"""
transaction-cost-analysis-tca-integration: implementation shortfall decomposition,
square-root market impact estimation, and backtest slippage-model calibration.

WHAT THIS IS
------------
A deterministic, per-trade *execution cost accountant*. For each trade it produces
two independent numbers and their difference:

1. an **estimated** (ex-ante) shortfall, built from a cost model --
   delay + half-spread + modelled square-root impact + commission;
2. a **realized** (ex-post) shortfall, measured from the actual fill price
   against the decision price (Perold 1988);
3. ``model_error_bps`` = realized - estimated, which is the only quantity that
   can actually calibrate a backtest slippage model.

Both are needed. A backtest that only knows (1) never learns it is wrong; a TCA
report that only knows (2) cannot tell you *which* cost component to fix.

IMPLEMENTATION SHORTFALL
------------------------
Perold (1988) defines IS over the whole *order*, not just the filled part, as the
sum of delay cost, explicit costs, implicit (execution) costs, and the
**opportunity cost of shares that were never executed**. Weighting by fill ratio
``f = filled_size / order_size``:

    IS_bps = f * execution_cost_bps + (1 - f) * opportunity_cost_bps
             + f * commission_bps

Opportunity cost requires a terminal benchmark price ``p_end`` -- the price at
which the unfilled remainder would have to be chased. This engine will not invent
one: when shares go unfilled and ``p_end`` is not supplied, ``opportunity_cost_bps``
and ``total_implementation_shortfall_bps`` are ``None``, never ``0.0``. Reporting
zero would silently understate the cost of exactly the orders that failed to fill.

MARKET IMPACT MODEL
-------------------
The modelled impact term is the square-root law

    market_impact_bps = gamma * sqrt(order_size / ADV)

The canonical form of this law in the literature is

    I = Y * sigma * sqrt(Q / V)

with Q the metaorder size, V the average daily volume, sigma the *daily
volatility*, and Y a dimensionless prefactor of order one (Toth et al. 2011;
Bouchaud et al.). This engine's ``gamma`` is a basis-point constant that folds
``Y * sigma`` into a single number. That is deliberate -- it keeps the signature
small -- but it has a consequence that must not be forgotten:

    **gamma is instrument-specific and volatility-regime-specific.**

A gamma fitted on a 20%-annualised-vol large cap will badly under-price the same
participation rate in a 120%-vol microcap, and will over-price it once volatility
mean-reverts. ``DEFAULT_MARKET_IMPACT_GAMMA_BPS`` is a placeholder to make the
module runnable, not a transferable constant. Calibrate it with
``suggest_market_impact_gamma`` against your own fills, per instrument bucket, and
refit as volatility regimes change.

The exponent itself is contested. Almgren, Thum, Hauptmann and Li (2005) reject
the 1/2 exponent for temporary impact in favour of 3/5 on Citigroup desk data;
fitted exponents in the literature run roughly 0.4-0.7 (see also Kyle and
Obizhaeva 2016). The square-root form is used here because it is the standard
baseline, not because it is settled.

The law also has a bounded validity range. Impact crosses over from linear to
square-root as order size grows, and empirical fits are typically quoted for
participation rates above ~1e-5, on metaorders small relative to ADV.
Participation outside ``[SQRT_LAW_MIN_PARTICIPATION, SQRT_LAW_MAX_PARTICIPATION]``
is still computed -- silently clamping it, as an earlier version of this module
did, made a 100x-ADV order price identically to a 1x-ADV order -- but it is
flagged via ``participation_out_of_model_range`` and logged.

LIMITATIONS
-----------
- **Half-spread is charged unconditionally in the estimate.** The modelled cost
  assumes every fill takes liquidity. A passive fill that earns the spread is
  over-charged by the *estimate*; the *realized* number, which comes from
  ``p_fill``, prices it correctly. Trust ``realized_shortfall_bps`` for maker flow.
- **No execution schedule.** Each trade is a single fill at ``p_fill``. Nothing
  here slices an order, models a participation trajectory, or decays impact.
- **Costs are symmetric.** Borrow fees on shorts and transaction taxes are not
  modelled; fold them into ``fixed_commission_bps`` if they apply.
- **Portfolio drag needs a capital base.** ``evaluate_portfolio_tca`` requires
  ``capital_base`` because a return drag is a fraction of capital, not a function
  of how many trades were printed.

REFERENCES
----------
- Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality."
  Journal of Portfolio Management 14(3), 4-9.
- Almgren, R., Thum, C., Hauptmann, E., Li, H. (2005). "Direct Estimation of
  Equity Market Impact." Risk 18(7).
  https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf
- Toth, B., Lemperiere, Y., Deremble, C., de Lataillade, J., Kockelkoren, J.,
  Bouchaud, J.-P. (2011). "Anomalous Price Impact and the Critical Nature of
  Liquidity in Financial Markets." Physical Review X 1, 021006.
  https://link.aps.org/doi/10.1103/PhysRevX.1.021006
- Kyle, A. and Obizhaeva, A. (2016). "Market Microstructure Invariance: Empirical
  Hypotheses." Econometrica 84(4), 1345-1404.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Basis points per unit (1.0 = 100% = 10,000 bps).
BPS_PER_UNIT = 10_000.0

#: Placeholder impact coefficient in bps, NOT a transferable constant. It equals
#: ``Y * sigma`` for one instrument in one volatility regime. Calibrate with
#: ``suggest_market_impact_gamma`` before trusting any impact number.
DEFAULT_MARKET_IMPACT_GAMMA_BPS = 15.0

#: Participation rate (order_size / ADV) below which the square-root law is
#: outside its usual empirical fitting range; impact there crosses over toward
#: linear. Flagged, not clamped.
SQRT_LAW_MIN_PARTICIPATION = 1e-5

#: Participation rate above which the square-root law is extrapolating. Published
#: fits are calibrated on metaorders small relative to ADV; 10% is the
#: conventional practitioner cut-off, matching `execution-realistic-simulation`.
#: Flagged, not clamped.
SQRT_LAW_MAX_PARTICIPATION = 0.10

#: Relative tolerance when comparing filled_size against order_size. A fill
#: quantity summed from child fills routinely lands a few ulps either side of the
#: parent size; without this a complete fill is rejected as an over-fill, or
#: leaves a 1e-13-unit "unfilled remainder" that demands a p_end to price.
FILL_SIZE_REL_TOL = 1e-9

_VALID_ACTIONS = ("BUY", "SELL")


def _validate_finite(value: float, label: str) -> float:
    """Return ``value`` as a float, rejecting non-numeric, NaN and infinite input.

    A NaN price otherwise propagates all the way to ``net_tca_return_pct``, where
    it compares ``False`` against every viability threshold and silently passes a
    strategy whose costs were never actually computed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    return value


def _validate_positive(value: float, label: str) -> float:
    """Return ``value`` as a finite float, rejecting anything <= 0."""
    value = _validate_finite(value, label)
    if value <= 0.0:
        raise ValueError(f"{label} must be strictly positive, got {value!r}.")
    return value


@dataclass
class TCATradeBreakdown:
    """Per-trade cost decomposition.

    All ``*_bps`` fields are signed costs in basis points of ``p_decision``, with
    **positive meaning adverse** (money lost) for both buys and sells.
    """

    symbol: str
    action: str  # BUY or SELL
    order_size: float
    filled_size: float
    unfilled_size: float
    adv: float
    p_decision: float
    p_arrival: float
    p_fill: float
    spread: float

    participation_rate: float
    participation_out_of_model_range: bool

    # --- modelled (ex-ante) components ---
    delay_cost_bps: float
    spread_cross_bps: float
    market_impact_bps: float
    commission_bps: float
    estimated_shortfall_bps: float

    # --- realized (ex-post) measurement ---
    realized_execution_cost_bps: float
    realized_shortfall_bps: float

    # --- Perold completeness: opportunity cost on the unfilled remainder ---
    opportunity_cost_bps: Optional[float]
    total_implementation_shortfall_bps: Optional[float]

    # --- calibration signal ---
    model_error_bps: float

    # --- currency amounts, based on the decision-price paper portfolio ---
    executed_notional: float
    realized_cost_currency: float
    opportunity_cost_currency: Optional[float]

    @property
    def total_shortfall_bps(self) -> float:
        """Deprecated alias for :attr:`estimated_shortfall_bps`.

        Retained so older callers keep working. It is the *modelled* cost, not
        the measured implementation shortfall; new code should read
        ``estimated_shortfall_bps`` or ``realized_shortfall_bps`` explicitly.
        """
        return self.estimated_shortfall_bps

    @property
    def total_cost_currency(self) -> float:
        """Realized cost plus opportunity cost where the latter is priced.

        Unpriced opportunity cost contributes ``0.0`` here, so this figure is a
        **lower bound** whenever ``opportunity_cost_currency`` is ``None``. The
        portfolio summary counts those trades in ``unpriced_opportunity_trades``.
        """
        return self.realized_cost_currency + (self.opportunity_cost_currency or 0.0)


@dataclass
class TCAPortfolioSummary:
    """Portfolio-level aggregation of :class:`TCATradeBreakdown` records."""

    total_trades_analyzed: int
    #: Equal-weighted mean of *realized* shortfall. Every trade counts once,
    #: regardless of size -- useful for spotting a systematically bad venue,
    #: misleading as a portfolio cost measure.
    avg_implementation_shortfall_bps: float
    #: Notional-weighted mean of realized shortfall. This is the figure that ties
    #: to ``total_cost_currency`` and the one to compare against a cost budget.
    notional_weighted_shortfall_bps: float
    avg_estimated_shortfall_bps: float
    #: Notional-weighted mean of (realized - estimated). Positive means the cost
    #: model under-predicts what execution actually costs.
    notional_weighted_model_error_bps: float
    total_market_impact_cost_usd: float
    total_commission_cost_usd: float
    total_cost_currency: float
    total_executed_notional: float
    capital_base: float
    friction_drag_pct: float
    gross_return_pct: float
    net_tca_return_pct: float
    #: Trades with unfilled shares but no ``p_end``; their opportunity cost is
    #: excluded, so ``friction_drag_pct`` understates the true drag.
    unpriced_opportunity_trades: int
    is_strategy_viable: bool


class TCABacktestIntegrator:
    """Decomposes implementation shortfall and calibrates backtest slippage.

    Args:
        market_impact_gamma: Square-root impact coefficient in bps. See the module
            docstring -- this is instrument- and volatility-regime-specific and
            must be calibrated, not inherited.
        fixed_commission_bps: All-in explicit cost in bps (commission, exchange
            and regulatory fees). Set it to the part your broker does *not*
            already report in the fill, or you will double-count.
        max_acceptable_shortfall_bps: Viability threshold, compared against the
            notional-weighted realized shortfall.
    """

    def __init__(
        self,
        market_impact_gamma: float = DEFAULT_MARKET_IMPACT_GAMMA_BPS,
        fixed_commission_bps: float = 2.5,
        max_acceptable_shortfall_bps: float = 50.0,
    ) -> None:
        self.gamma = _validate_finite(market_impact_gamma, "market_impact_gamma")
        if self.gamma < 0.0:
            raise ValueError(
                f"market_impact_gamma must be non-negative, got {self.gamma!r}.")
        self.fixed_commission_bps = _validate_finite(
            fixed_commission_bps, "fixed_commission_bps")
        if self.fixed_commission_bps < 0.0:
            raise ValueError(
                f"fixed_commission_bps must be non-negative, "
                f"got {self.fixed_commission_bps!r}.")
        self.max_acceptable_shortfall_bps = _validate_finite(
            max_acceptable_shortfall_bps, "max_acceptable_shortfall_bps")

    def analyze_trade(
        self,
        symbol: str,
        action: str,
        order_size: float,
        adv: float,
        p_decision: float,
        p_arrival: float,
        p_fill: float,
        spread: float,
        filled_size: Optional[float] = None,
        p_end: Optional[float] = None,
    ) -> TCATradeBreakdown:
        """Decompose one order into modelled and realized execution costs.

        Args:
            symbol: Instrument identifier.
            action: ``"BUY"`` or ``"SELL"`` (case-insensitive). Anything else is
                rejected -- a typo must not silently flip the sign of every
                price-based cost.
            order_size: Shares/contracts the strategy *intended* to trade.
            adv: Average daily volume in the same units as ``order_size``.
            p_decision: Price at signal decision time (the IS benchmark).
            p_arrival: Price when the order reached the venue.
            p_fill: Volume-weighted average fill price of the executed part.
            spread: Quoted bid-ask spread in price units at arrival.
            filled_size: Units actually executed. Defaults to ``order_size``
                (complete fill). Must satisfy ``0 <= filled_size <= order_size``.
            p_end: Terminal benchmark price for the unfilled remainder. Required
                to price opportunity cost; without it that component is ``None``.

        Returns:
            A :class:`TCATradeBreakdown`.

        Raises:
            TypeError: A numeric argument was not numeric.
            ValueError: Unknown ``action``, empty ``symbol``, non-finite input,
                non-positive price/size/ADV, or ``filled_size`` out of range.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")
        if not isinstance(action, str) or action.strip().upper() not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {_VALID_ACTIONS}, got {action!r}. "
                "Treating an unrecognised action as a SELL silently inverts the "
                "sign of delay and realized cost.")

        action_norm = action.strip().upper()
        order_size = _validate_positive(order_size, "order_size")
        # ADV is validated rather than floored. The previous max(1.0, adv) guard
        # turned adv=0 into a 1-unit ADV, pinning participation at 100% and the
        # impact estimate at gamma -- a fabricated number derived from missing
        # data, indistinguishable from a genuine full-ADV order.
        adv = _validate_positive(adv, "adv")
        p_decision = _validate_positive(p_decision, "p_decision")
        p_arrival = _validate_positive(p_arrival, "p_arrival")
        p_fill = _validate_positive(p_fill, "p_fill")
        spread = _validate_finite(spread, "spread")
        if spread < 0.0:
            raise ValueError(f"spread must be non-negative, got {spread!r}.")

        if filled_size is None:
            filled_size = order_size
        filled_size = _validate_finite(filled_size, "filled_size")
        # A fill quantity accumulated from child fills can exceed order_size by
        # representation noise (sum of 3 x 333.333... overshoots 1000.0). Snap
        # that back rather than rejecting a complete fill as an over-fill; a
        # genuine over-fill is orders of magnitude larger than rel_tol.
        if filled_size > order_size and math.isclose(
                filled_size, order_size, rel_tol=FILL_SIZE_REL_TOL, abs_tol=0.0):
            filled_size = order_size
        if filled_size < 0.0 or filled_size > order_size:
            raise ValueError(
                f"filled_size must satisfy 0 <= filled_size <= order_size "
                f"({order_size!r}), got {filled_size!r}.")
        unfilled_size = order_size - filled_size
        # Guard the same noise on the other side: an unfilled remainder of 1e-13
        # units is a rounding artefact, not an unexecuted order.
        if math.isclose(filled_size, order_size, rel_tol=FILL_SIZE_REL_TOL, abs_tol=0.0):
            unfilled_size = 0.0

        direction = 1.0 if action_norm == "BUY" else -1.0

        # --- 1. Delay cost: signal decision -> order arrival at the venue. ---
        delay_cost_bps = direction * ((p_arrival - p_decision) / p_decision) * BPS_PER_UNIT

        # --- 2. Half-spread cross (assumes a liquidity-taking fill). ---
        spread_cross_bps = (0.5 * spread / p_decision) * BPS_PER_UNIT

        # --- 3. Square-root market impact: gamma * sqrt(size / ADV). ---
        participation_rate = order_size / adv
        out_of_range = not (
            SQRT_LAW_MIN_PARTICIPATION
            <= participation_rate
            <= SQRT_LAW_MAX_PARTICIPATION)
        if out_of_range:
            logger.warning(
                "%s %s: participation rate %.6g is outside the square-root law's "
                "usual fitted range [%g, %g]; the impact estimate is an "
                "extrapolation.",
                symbol.strip().upper(), action_norm, participation_rate,
                SQRT_LAW_MIN_PARTICIPATION, SQRT_LAW_MAX_PARTICIPATION)
        market_impact_bps = self.gamma * math.sqrt(participation_rate)

        # --- 4. Modelled total (ex-ante estimate). ---
        estimated_shortfall_bps = (
            delay_cost_bps
            + spread_cross_bps
            + market_impact_bps
            + self.fixed_commission_bps)

        # --- 5. Realized shortfall (ex-post, Perold): measured from the fill. ---
        # This is what p_fill is for. It subsumes delay, spread and impact as they
        # actually occurred, so it is compared against -- never added to -- the
        # modelled components above.
        if filled_size > 0.0:
            realized_execution_cost_bps = (
                direction * ((p_fill - p_decision) / p_decision) * BPS_PER_UNIT)
            realized_shortfall_bps = (
                realized_execution_cost_bps + self.fixed_commission_bps)
        else:
            # Nothing executed: no execution cost and no commission to pay. The
            # cost of a complete miss is opportunity cost, priced below.
            realized_execution_cost_bps = 0.0
            realized_shortfall_bps = 0.0

        # --- 6. Opportunity cost on the unfilled remainder. ---
        opportunity_cost_bps: Optional[float] = None
        opportunity_cost_currency: Optional[float] = None
        total_is_bps: Optional[float] = None
        if unfilled_size <= 0.0:
            opportunity_cost_bps = 0.0
            opportunity_cost_currency = 0.0
        elif p_end is not None:
            p_end = _validate_positive(p_end, "p_end")
            opportunity_cost_bps = (
                direction * ((p_end - p_decision) / p_decision) * BPS_PER_UNIT)
            opportunity_cost_currency = (
                (opportunity_cost_bps / BPS_PER_UNIT) * (unfilled_size * p_decision))
        else:
            logger.warning(
                "%s %s: %.6g of %.6g units unfilled and no p_end supplied; "
                "opportunity cost is reported as None, not zero. Total "
                "implementation shortfall is therefore not computable.",
                symbol.strip().upper(), action_norm, unfilled_size, order_size)

        if opportunity_cost_bps is not None:
            fill_ratio = filled_size / order_size
            total_is_bps = (
                fill_ratio * realized_execution_cost_bps
                + (1.0 - fill_ratio) * opportunity_cost_bps
                + fill_ratio * self.fixed_commission_bps)

        # --- 7. Calibration signal. Positive => model under-predicts cost. ---
        model_error_bps = realized_shortfall_bps - estimated_shortfall_bps

        executed_notional = filled_size * p_decision
        realized_cost_currency = (
            (realized_shortfall_bps / BPS_PER_UNIT) * executed_notional)

        return TCATradeBreakdown(
            symbol=symbol.strip().upper(),
            action=action_norm,
            order_size=order_size,
            filled_size=filled_size,
            unfilled_size=unfilled_size,
            adv=adv,
            p_decision=p_decision,
            p_arrival=p_arrival,
            p_fill=p_fill,
            spread=spread,
            participation_rate=participation_rate,
            participation_out_of_model_range=out_of_range,
            delay_cost_bps=delay_cost_bps,
            spread_cross_bps=spread_cross_bps,
            market_impact_bps=market_impact_bps,
            commission_bps=self.fixed_commission_bps,
            estimated_shortfall_bps=estimated_shortfall_bps,
            realized_execution_cost_bps=realized_execution_cost_bps,
            realized_shortfall_bps=realized_shortfall_bps,
            opportunity_cost_bps=opportunity_cost_bps,
            total_implementation_shortfall_bps=total_is_bps,
            model_error_bps=model_error_bps,
            executed_notional=executed_notional,
            realized_cost_currency=realized_cost_currency,
            opportunity_cost_currency=opportunity_cost_currency,
        )

    def evaluate_portfolio_tca(
        self,
        trades: List[TCATradeBreakdown],
        gross_return_pct: float,
        capital_base: float,
    ) -> TCAPortfolioSummary:
        """Aggregate trade breakdowns into a net-of-TCA return.

        ``capital_base`` is mandatory. A return drag is currency cost divided by
        the capital that earned the gross return; it is not a function of how many
        trades were printed. an earlier formula summed per-trade *bps* and read
        the result as a percentage, so 1,000 one-share trades costing about four
        cents in total subtracted 35 percentage points from the strategy return
        and flagged it non-viable, while a single order for half a day's volume
        barely registered.

        Args:
            trades: Breakdowns from :meth:`analyze_trade`.
            gross_return_pct: Backtest return before costs, in percent.
            capital_base: Capital that produced ``gross_return_pct``, in the same
                currency as the prices passed to :meth:`analyze_trade`.

        Returns:
            A :class:`TCAPortfolioSummary`.

        Raises:
            TypeError: ``gross_return_pct`` or ``capital_base`` was not numeric.
            ValueError: Non-finite ``gross_return_pct``, or non-positive
                ``capital_base``.
        """
        gross_return_pct = _validate_finite(gross_return_pct, "gross_return_pct")
        capital_base = _validate_positive(capital_base, "capital_base")

        if not trades:
            logger.info("TCA portfolio analysis: no trades supplied; drag is zero.")
            return TCAPortfolioSummary(
                total_trades_analyzed=0,
                avg_implementation_shortfall_bps=0.0,
                notional_weighted_shortfall_bps=0.0,
                avg_estimated_shortfall_bps=0.0,
                notional_weighted_model_error_bps=0.0,
                total_market_impact_cost_usd=0.0,
                total_commission_cost_usd=0.0,
                total_cost_currency=0.0,
                total_executed_notional=0.0,
                capital_base=capital_base,
                friction_drag_pct=0.0,
                gross_return_pct=round(gross_return_pct, 2),
                net_tca_return_pct=round(gross_return_pct, 2),
                unpriced_opportunity_trades=0,
                is_strategy_viable=True,
            )

        n = len(trades)
        total_notional = sum(t.executed_notional for t in trades)

        avg_realized = sum(t.realized_shortfall_bps for t in trades) / n
        avg_estimated = sum(t.estimated_shortfall_bps for t in trades) / n

        if total_notional > 0.0:
            weighted_realized = sum(
                t.realized_shortfall_bps * t.executed_notional
                for t in trades) / total_notional
            weighted_error = sum(
                t.model_error_bps * t.executed_notional
                for t in trades) / total_notional
        else:
            # Every order was a complete miss, so there is no notional to weight
            # by. Equal weighting is the only defined answer.
            weighted_realized = avg_realized
            weighted_error = sum(t.model_error_bps for t in trades) / n

        total_impact_usd = sum(
            (t.market_impact_bps / BPS_PER_UNIT) * t.executed_notional for t in trades)
        total_comm_usd = sum(
            (t.commission_bps / BPS_PER_UNIT) * t.executed_notional for t in trades)

        total_cost_currency = sum(t.total_cost_currency for t in trades)
        unpriced = sum(1 for t in trades if t.opportunity_cost_currency is None)

        friction_drag_pct = (total_cost_currency / capital_base) * 100.0
        net_return_pct = gross_return_pct - friction_drag_pct

        is_viable = (
            weighted_realized <= self.max_acceptable_shortfall_bps
            and net_return_pct > 0.0)

        logger.info(
            "TCA portfolio analysis (%d trades): notional-weighted shortfall=%.2f bps, "
            "model error=%+.2f bps, cost=%.2f on capital %.2f -> drag %.4f%%; "
            "gross %.2f%% -> net %.2f%%.",
            n, weighted_realized, weighted_error, total_cost_currency, capital_base,
            friction_drag_pct, gross_return_pct, net_return_pct)
        if unpriced:
            logger.warning(
                "%d of %d trades have unpriced opportunity cost; "
                "net_tca_return_pct is an optimistic bound.", unpriced, n)

        return TCAPortfolioSummary(
            total_trades_analyzed=n,
            avg_implementation_shortfall_bps=round(avg_realized, 2),
            notional_weighted_shortfall_bps=round(weighted_realized, 2),
            avg_estimated_shortfall_bps=round(avg_estimated, 2),
            notional_weighted_model_error_bps=round(weighted_error, 2),
            total_market_impact_cost_usd=round(total_impact_usd, 2),
            total_commission_cost_usd=round(total_comm_usd, 2),
            total_cost_currency=round(total_cost_currency, 2),
            total_executed_notional=round(total_notional, 2),
            capital_base=capital_base,
            friction_drag_pct=round(friction_drag_pct, 4),
            gross_return_pct=round(gross_return_pct, 2),
            net_tca_return_pct=round(net_return_pct, 2),
            unpriced_opportunity_trades=unpriced,
            is_strategy_viable=is_viable,
        )

    def suggest_market_impact_gamma(
        self,
        trades: List[TCATradeBreakdown],
    ) -> Optional[float]:
        """Least-squares refit of ``gamma`` against realized fills.

        This is the calibration step the workflow calls for. For each trade the
        residual not explained by delay and half-spread is

            r_i = realized_execution_cost_bps_i - delay_i - spread_cross_i

        and gamma is chosen to minimise ``sum_i (r_i - gamma * sqrt(phi_i))^2``,
        which has the closed form

            gamma_hat = sum_i (r_i * sqrt(phi_i)) / sum_i phi_i

        Only filled orders carry information about impact, so unfilled ones are
        excluded. A negative fit is returned as ``0.0``: impact cannot be a
        credit, and a negative estimate means the residual is dominated by
        something other than impact -- passive fills earning the spread, or
        favourable drift -- and that the fit is not usable as-is.

        Args:
            trades: Breakdowns from :meth:`analyze_trade`.

        Returns:
            The refitted coefficient in bps, or ``None`` when no filled trade has
            non-zero participation.
        """
        num = 0.0
        den = 0.0
        for t in trades:
            if t.filled_size <= 0.0 or t.participation_rate <= 0.0:
                continue
            residual = (
                t.realized_execution_cost_bps - t.delay_cost_bps - t.spread_cross_bps)
            num += residual * math.sqrt(t.participation_rate)
            den += t.participation_rate

        if den <= 0.0:
            logger.warning(
                "Cannot calibrate gamma: no filled trade with non-zero participation.")
            return None

        gamma_hat = num / den
        if gamma_hat < 0.0:
            logger.warning(
                "Calibrated gamma is negative (%.4f bps); clamping to 0.0. The "
                "unexplained residual is not impact -- check for passive fills or "
                "favourable drift before using this figure.", gamma_hat)
            return 0.0
        return gamma_hat
