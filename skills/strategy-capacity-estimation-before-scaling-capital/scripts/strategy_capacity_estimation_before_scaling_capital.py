"""
strategy-capacity-estimation-before-scaling-capital: AUM capacity estimator that
scales a strategy's traded notional across a grid of AUM levels, prices the
resulting bid-ask and market-impact drag, and reports the largest AUM at which
the strategy still clears a minimum net Sharpe gate and an ADV participation cap.

WHAT THIS IS
------------
A deterministic, single-strategy *capacity accountant*. Given a frictionless
return/volatility profile and a liquidity profile (turnover, ADV, daily
volatility, half-spread), it walks an AUM grid and answers one question: at what
capital level does this strategy stop being worth running?

WHAT THIS IS NOT
----------------
- **Not an optimizer.** Nothing here searches over portfolio weights, execution
  schedules, or turnover levels. Every parameter is an input.
- **Not an alpha model.** ``annual_gross_return_pct`` is taken entirely on faith
  and is assumed *invariant to AUM*. In reality gross alpha decays with size for
  reasons this engine cannot see: signal capacity exhaustion, dilution into worse
  names, and crowding by other participants trading the same signal. The Sharpe
  decay reported here is therefore an **upper bound** on the true curve, and the
  capacity number is correspondingly optimistic.
- **Not a per-name liquidity model.** The engine compares aggregate portfolio
  turnover against a single aggregate ``avg_daily_volume_usd``. A real portfolio
  spreads turnover across names with wildly different ADVs, and capacity binds on
  the *least liquid* names long before it binds on the aggregate. Use
  ``liquidity-adjusted-position-sizing`` for the per-name view.
- **Not an execution scheduler.** There is no slicing, no participation schedule,
  and no intraday timing. See ``execution-algo-twap-vwap-slicing``.

IMPACT MODEL AND ITS ATTRIBUTION
--------------------------------
The engine applies the empirical **square-root law of market impact**:

    I(Q) = Y * sigma_daily * sqrt(Q / V)

where ``Q`` is the notional traded in a day, ``V`` is average daily volume in the
same currency units, ``sigma_daily`` is daily return volatility, and ``Y`` is a
dimensionless prefactor of order unity (``impact_gamma`` here).

This law is **not** the Almgren-Chriss model, and earlier versions of this skill
mis-attributed it. Almgren and Chriss (2000), "Optimal Execution of Portfolio
Transactions", solve an optimal-liquidation problem under *linear* temporary and
permanent impact functions of the trading rate; they do not propose a square-root
impact law. The square-root law is an independent empirical regularity credited
to Torre/BARRA (1997) and Grinold and Kahn (1999), and confirmed repeatedly since
-- Almgren et al. (2005), Toth et al. (2011), Kyle and Obizhaeva (2016) among
others. Note that Almgren et al. (2005) and Kyle and Obizhaeva (2016) fit an
exponent nearer 0.6 than 0.5; the exponent here is hard-coded at 0.5.

Two consequences worth stating plainly:

1. **The default Y = 0.5 is the optimistic end of the empirical range.**
   Measured values for stocks and futures fall in roughly ``0.5`` to ``1.0``.
   Impact drag is linear in ``Y``, but *capacity* is not: when the Sharpe gate
   binds, net return is ``R - spread - c*Y*sqrt(AUM)``, so the AUM at which the
   gate breaks scales as ``Y^-2``. Doubling ``Y`` from the default to the top of
   the empirical range therefore cuts estimated capacity roughly fourfold -- on
   the reference parameters, from $133M to $33M. (When the participation cap binds
   instead, capacity is independent of ``Y`` entirely.) ``impact_gamma`` must be
   calibrated against your own realized slippage; there is no transferable
   universal constant, and the value used is echoed on the report for audit.
2. **Charging ``I(Q)`` on the whole notional over-states cost.** ``I(Q)`` is the
   *terminal* price displacement of a metaorder; the average price paid across
   the metaorder is strictly below it. Empirically impact then relaxes to roughly
   two-thirds of peak after completion (the fair-pricing condition; Farmer,
   Gerig, Lillo and Waelbroeck 2013). This engine deliberately charges the full
   terminal displacement, which is conservative -- and partially offsets the
   optimism of point 1. Do not treat the two errors as cancelling; they are not
   the same size and neither is calibrated.

SHARPE CONVENTION
-----------------
A Sharpe ratio is an *excess* return per unit of risk (Sharpe 1994): the mean
differential return over a benchmark, divided by that differential's standard
deviation. ``risk_free_rate_pct`` therefore participates in every Sharpe computed
here. It defaults to ``0.0``, which reproduces this engine's historical
``return / volatility`` output exactly and is only correct if you are already
feeding it *excess* returns. Feeding total returns with ``risk_free_rate_pct=0.0``
overstates every Sharpe by ``rf / sigma`` -- at a 4% rate and 15% volatility that
is +0.27, easily enough to carry a strategy over a 1.0 gate it does not clear.

The denominator is the **gross** strategy volatility. Costs are modelled as a
deterministic drag, so the reported net Sharpe ignores the fact that realized
impact is itself stochastic and adds variance. This biases net Sharpe *upward*.

GRID SEMANTICS
--------------
Capacity is searched on a discrete grid ``aum_step_usd, 2*aum_step_usd, ...`` up
to ``max_search_aum_usd``. Three things follow, all of which are reported rather
than hidden:

- ``max_capacity_aum_usd`` is the largest *grid point* that clears both gates.
  True capacity lies in ``[max_capacity_aum_usd, max_capacity_aum_usd +
  capacity_resolution_usd)``. Reducing ``aum_step_usd`` narrows this.
- ``max_capacity_aum_usd == 0.0`` means "below one grid step", not "exactly zero".
- If nothing in the searched range breaches, the answer is
  ``SEARCH_RANGE_EXHAUSTED`` -- the search hit its ceiling, which is emphatically
  not the same as unlimited capacity. Earlier versions reported ``UNLIMITED``
  here, and a caller that scaled to that number was scaling to a loop bound.

Capacity is defined as the largest AUM such that *every* grid point up to and
including it is feasible, i.e. the search stops at the first breach. Taking the
last feasible point anywhere on the grid would jump across a breached region.

OTHER LIMITATIONS
-----------------
- **Costs are symmetric.** No borrow fees, no taxes, no asymmetric spreads, no
  fixed per-order costs, no financing charges on leverage.
- **252 trading days is hard-coded** (``TRADING_DAYS_PER_YEAR``). Crypto venues
  trade roughly 365; adjust the turnover input or the constant accordingly.
- **Curve fields are rounded for display.** Gate decisions use the exact values,
  exposed as ``net_sharpe_ratio_exact`` and ``adv_participation_pct_exact``. A
  point can display ``1.0`` and still be flagged as breaching.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Basis points per unit (1.0 = 100% = 10,000 bps).
BPS_PER_UNIT = 10_000.0

#: Trading days per year used to annualise daily turnover costs. Equity/futures
#: convention. Crypto and FX venues do not follow it.
TRADING_DAYS_PER_YEAR = 252.0

#: Square-root-law prefactor Y. Empirical fits for stocks and futures land in
#: roughly 0.5-1.0, so this default sits at the *optimistic* end and will flatter
#: capacity. It MUST be calibrated to your own realized slippage; see the module
#: docstring. The value actually used is echoed on the report for audit.
DEFAULT_IMPACT_GAMMA = 0.5

#: Hard cap on grid points. A caller passing a tiny step against a wide search
#: range would otherwise materialise an unbounded curve and exhaust memory.
MAX_GRID_POINTS = 200_000

#: Limiting-factor values actually emitted by ``estimate_capacity``. Kept as a
#: module constant so callers can assert against the real enumeration rather than
#: against a docstring.
LIMITING_FACTORS = (
    "ADV_PARTICIPATION_LIMIT",        # participation cap bound first
    "MIN_SHARPE_BREACH",              # net Sharpe fell through the gate first
    "BELOW_MIN_SHARPE_AT_ALL_SIZES",  # never cleared the gate, even at one step
    "SEARCH_RANGE_EXHAUSTED",         # no breach inside the searched range
)


@dataclass
class StrategyCapacityEstimationBeforeScalingCapitalConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True


class StrategyCapacityEstimationBeforeScalingCapital:
    """Legacy class retained for 100% backward compatibility."""

    def __init__(self, config: StrategyCapacityEstimationBeforeScalingCapitalConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


@dataclass
class StrategyParameters:
    """
    Frictionless performance profile plus the liquidity profile needed to price
    scaling drag.

    ``daily_turnover_pct`` is **one-way** notional traded per day as a fraction of
    AUM: 0.10 means $10M of notional against a $100M book. ``half_spread_bps`` is
    charged once on that notional, which is the standard pairing for one-way
    turnover -- you cross half the spread on each side you trade. If your turnover
    figure is two-way, halve it before passing it in, or you will double every
    cost in this model.

    ``avg_daily_volume_usd`` must be denominated in the same currency as AUM.
    """
    strategy_id: str
    annual_gross_return_pct: float           # Frictionless gross return e.g. 0.25 (25%)
    annual_volatility_pct: float             # Annual volatility e.g. 0.15 (15%)
    daily_turnover_pct: float                # ONE-WAY daily turnover e.g. 0.10 (10% of AUM traded daily)
    avg_daily_volume_usd: float              # Tradable universe ADV in USD e.g. $50,000,000
    avg_daily_volatility_pct: float          # Daily return volatility e.g. 0.015 (1.5%)
    half_spread_bps: float = 2.0             # Half bid-ask spread in bps, charged on one-way turnover
    max_participation_rate_pct: float = 5.0  # Max % of ADV traded per day (practitioner convention, not a rule)
    min_acceptable_sharpe: float = 1.0       # Minimum acceptable NET Sharpe ratio
    # --- Additive; the default preserves historical positional construction. ---
    risk_free_rate_pct: float = 0.0          # Annual risk-free rate e.g. 0.04. See module docstring.


@dataclass
class AUMCapacityPoint:
    """
    One grid point. The ``*_exact`` fields carry the unrounded values the gates are
    actually evaluated against; the rounded fields exist for display and are not
    decision inputs.
    """
    aum_usd: float
    gross_return_pct: float
    spread_cost_usd: float
    market_impact_cost_usd: float
    net_return_pct: float
    net_sharpe_ratio: float
    adv_participation_pct: float
    is_capacity_exceeded: bool
    # --- Fields below are additive; they default so positional construction still works. ---
    net_pnl_usd: float = 0.0
    net_sharpe_ratio_exact: float = 0.0
    adv_participation_pct_exact: float = 0.0
    is_participation_breached: bool = False
    is_sharpe_breached: bool = False


@dataclass
class StrategyCapacityReport:
    """
    ``max_capacity_aum_usd`` is the largest grid point clearing both gates; true
    capacity lies within ``capacity_resolution_usd`` above it, and ``0.0`` means
    "below one grid step".

    ``optimal_sharpe_capacity_aum_usd`` is the **feasible** net-dollar-PnL maximum:
    the AUM maximising net PnL among points that pass both gates. Earlier versions
    maximised over the whole grid including breached points, which routinely
    returned an AUM several times the safe limit (and, when the PnL peak lay
    outside the grid, simply returned the search ceiling). The unconstrained
    figure is still available as ``unconstrained_max_pnl_aum_usd``; it is a
    diagnostic, never an allocation target.
    """
    strategy_id: str
    frictionless_sharpe_ratio: float
    max_capacity_aum_usd: float             # Max AUM clearing both gates
    optimal_sharpe_capacity_aum_usd: float  # FEASIBLE AUM maximising net dollar PnL
    capacity_curve: List[AUMCapacityPoint]
    limiting_factor: str                    # One of LIMITING_FACTORS
    audit_notes: str
    # --- Fields below are additive; they default so positional construction still works. ---
    unconstrained_max_pnl_aum_usd: float = 0.0  # Diagnostic only; ignores both gates
    capacity_resolution_usd: float = 0.0    # Grid step; capacity is known only to this precision
    search_range_exhausted: bool = False    # True => no breach found; answer censored by the grid ceiling
    max_capacity_net_sharpe: float = 0.0    # Net Sharpe at max_capacity_aum_usd (0.0 if no feasible point)
    impact_gamma: float = DEFAULT_IMPACT_GAMMA  # Prefactor actually used, for audit
    risk_free_rate_pct: float = 0.0         # Rate actually used, for audit


def _validate_finite(value: float, label: str) -> float:
    """Rejects non-numeric and non-finite inputs before they poison the arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{label} is non-finite ({value}). A NaN propagates silently through the "
            "Sharpe computation, and because every comparison against NaN is False the "
            "engine would report a confident capacity number for an unpriced strategy."
        )
    return float(value)


def _validate_params(params: StrategyParameters) -> None:
    """
    Validates the strategy profile. Every rule here corresponds to a way the
    unvalidated engine produced a plausible-looking but wrong capacity number.
    """
    if not isinstance(params.strategy_id, str) or not params.strategy_id.strip():
        raise ValueError(
            f"strategy_id must be a non-empty string, got {params.strategy_id!r}."
        )

    for label, value in (
        ("annual_gross_return_pct", params.annual_gross_return_pct),
        ("annual_volatility_pct", params.annual_volatility_pct),
        ("daily_turnover_pct", params.daily_turnover_pct),
        ("avg_daily_volume_usd", params.avg_daily_volume_usd),
        ("avg_daily_volatility_pct", params.avg_daily_volatility_pct),
        ("half_spread_bps", params.half_spread_bps),
        ("max_participation_rate_pct", params.max_participation_rate_pct),
        ("min_acceptable_sharpe", params.min_acceptable_sharpe),
        ("risk_free_rate_pct", params.risk_free_rate_pct),
    ):
        _validate_finite(value, f"StrategyParameters.{label}")

    if params.annual_volatility_pct <= 0.0:
        raise ValueError(
            f"StrategyParameters.annual_volatility_pct must be > 0, got "
            f"{params.annual_volatility_pct}. It is the Sharpe denominator; zero "
            "volatility is a division by zero, not an infinitely good strategy."
        )
    if params.avg_daily_volume_usd <= 0.0:
        raise ValueError(
            f"StrategyParameters.avg_daily_volume_usd must be > 0, got "
            f"{params.avg_daily_volume_usd}. An instrument with no volume has no "
            "capacity; it cannot be assigned one by dividing by zero."
        )
    if params.avg_daily_volatility_pct < 0.0:
        raise ValueError(
            f"StrategyParameters.avg_daily_volatility_pct must be >= 0, got "
            f"{params.avg_daily_volatility_pct}. Negative volatility would credit the "
            "strategy for trading size."
        )
    if params.daily_turnover_pct < 0.0:
        raise ValueError(
            f"StrategyParameters.daily_turnover_pct must be >= 0, got "
            f"{params.daily_turnover_pct}. Negative turnover produces negative impact "
            "and spread costs -- a rebate for trading -- and the engine then reports "
            "capacity limited by nothing at all."
        )
    if params.half_spread_bps < 0.0:
        raise ValueError(
            f"StrategyParameters.half_spread_bps must be >= 0, got "
            f"{params.half_spread_bps}. A negative spread pays you to cross it."
        )
    if params.max_participation_rate_pct <= 0.0:
        raise ValueError(
            f"StrategyParameters.max_participation_rate_pct must be > 0, got "
            f"{params.max_participation_rate_pct}. This is a percentage of ADV "
            "(5.0 means 5%), not a fraction."
        )


def _validate_grid(aum_step_usd: float, max_search_aum_usd: float) -> int:
    """Validates the AUM search grid and returns the number of points it contains."""
    _validate_finite(aum_step_usd, "aum_step_usd")
    _validate_finite(max_search_aum_usd, "max_search_aum_usd")

    if aum_step_usd <= 0.0:
        raise ValueError(
            f"aum_step_usd must be > 0, got {aum_step_usd}. A zero or negative step "
            "never advances the search and the engine would loop forever."
        )
    if max_search_aum_usd < aum_step_usd:
        raise ValueError(
            f"max_search_aum_usd ({max_search_aum_usd}) is below aum_step_usd "
            f"({aum_step_usd}), so the grid is empty. An empty grid previously "
            "returned a zero-capacity report indistinguishable from a genuine result."
        )

    n_points = int(math.floor(max_search_aum_usd / aum_step_usd))
    if n_points > MAX_GRID_POINTS:
        raise ValueError(
            f"Grid would contain {n_points} points (max {MAX_GRID_POINTS}). Increase "
            "aum_step_usd or lower max_search_aum_usd; capacity does not need "
            "sub-basis-point AUM resolution."
        )
    return n_points


class StrategyCapacityEstimatorEngine:
    """
    Single-strategy AUM capacity estimator: square-root-law market impact,
    half-spread friction, ADV participation cap, and net Sharpe decay.

    ``impact_gamma`` is the square-root-law prefactor Y and must be calibrated to
    realized slippage. See the module docstring for why the default flatters
    capacity and why this model is *not* Almgren-Chriss.
    """

    def __init__(self, impact_gamma: float = DEFAULT_IMPACT_GAMMA):
        _validate_finite(impact_gamma, "impact_gamma")
        if impact_gamma < 0.0:
            raise ValueError(
                f"impact_gamma must be >= 0, got {impact_gamma}. A negative prefactor "
                "turns market impact into a rebate that grows with size."
            )
        if impact_gamma == 0.0:
            logger.warning(
                "impact_gamma=0 disables market impact entirely. The resulting capacity "
                "is bounded only by spread and the participation cap and is not a "
                "capacity estimate in any meaningful sense."
            )
        self.impact_gamma = impact_gamma

    def _estimate_daily_market_impact_pct(
        self,
        daily_trade_size_usd: float,
        adv_usd: float,
        daily_vol_pct: float
    ) -> float:
        """
        Square-root law of market impact (Torre/BARRA 1997; Grinold and Kahn 1999;
        Toth et al. 2011) -- *not* Almgren-Chriss, which is a linear-impact model:

            I(Q) = Y * sigma_daily * sqrt(Q / V)

        Returns the terminal price displacement as a fraction (0.01 = 1%).
        """
        if adv_usd <= 0 or daily_trade_size_usd <= 0:
            return 0.0
        participation = daily_trade_size_usd / adv_usd
        return self.impact_gamma * daily_vol_pct * math.sqrt(participation)

    def estimate_capacity(
        self,
        params: StrategyParameters,
        aum_step_usd: float = 1_000_000.0,
        max_search_aum_usd: float = 200_000_000.0
    ) -> StrategyCapacityReport:
        """
        Walks the AUM grid, prices scaling drag at each point, and returns the
        capacity report.

        Raises ``ValueError``/``TypeError`` on any input that would make the result
        meaningless, rather than returning a plausible-looking number. See the module
        docstring for grid semantics and for what the returned figures do and do not
        assert.
        """
        _validate_params(params)
        n_points = _validate_grid(aum_step_usd, max_search_aum_usd)

        frictionless_sharpe = round(
            (params.annual_gross_return_pct - params.risk_free_rate_pct)
            / params.annual_volatility_pct,
            2,
        )
        capacity_curve: List[AUMCapacityPoint] = []

        max_capacity_aum = 0.0
        max_capacity_net_sharpe = 0.0
        optimal_pnl_aum = 0.0
        best_feasible_pnl_usd = -math.inf
        unconstrained_pnl_aum = 0.0
        best_unconstrained_pnl_usd = -math.inf
        limiting_factor: Optional[str] = None
        first_point_breached_on_sharpe = False
        still_feasible = True

        for i in range(n_points):
            # Index-derived rather than accumulated: repeated += on a step that is
            # not exactly representable drifts and can drop the final grid point.
            aum = (i + 1) * aum_step_usd

            # 1. Daily one-way trading notional and its share of ADV.
            daily_turnover_usd = aum * params.daily_turnover_pct
            adv_participation = (daily_turnover_usd / params.avg_daily_volume_usd) * 100.0

            # 2. Annual half-spread cost, charged once on one-way turnover.
            annual_spread_cost_usd = (
                daily_turnover_usd * TRADING_DAYS_PER_YEAR
                * (params.half_spread_bps / BPS_PER_UNIT)
            )

            # 3. Annual square-root-law market impact cost.
            daily_impact_pct = self._estimate_daily_market_impact_pct(
                daily_turnover_usd, params.avg_daily_volume_usd,
                params.avg_daily_volatility_pct
            )
            annual_impact_cost_usd = (
                daily_turnover_usd * TRADING_DAYS_PER_YEAR * daily_impact_pct
            )

            # 4. Net return and net Sharpe. Excess return over the risk-free rate,
            #    divided by GROSS strategy volatility (see module docstring).
            total_annual_cost_usd = annual_spread_cost_usd + annual_impact_cost_usd
            gross_pnl_usd = aum * params.annual_gross_return_pct
            net_pnl_usd = gross_pnl_usd - total_annual_cost_usd

            net_return_pct = net_pnl_usd / aum
            net_sharpe = (
                (net_return_pct - params.risk_free_rate_pct) / params.annual_volatility_pct
            )

            is_sharpe_breached = net_sharpe < params.min_acceptable_sharpe
            is_participation_breached = adv_participation > params.max_participation_rate_pct
            is_exceeded = is_sharpe_breached or is_participation_breached

            capacity_curve.append(AUMCapacityPoint(
                aum_usd=aum,
                gross_return_pct=params.annual_gross_return_pct,
                spread_cost_usd=round(annual_spread_cost_usd, 2),
                market_impact_cost_usd=round(annual_impact_cost_usd, 2),
                net_return_pct=round(net_return_pct, 4),
                net_sharpe_ratio=round(net_sharpe, 2),
                adv_participation_pct=round(adv_participation, 2),
                is_capacity_exceeded=is_exceeded,
                net_pnl_usd=round(net_pnl_usd, 2),
                net_sharpe_ratio_exact=net_sharpe,
                adv_participation_pct_exact=adv_participation,
                is_participation_breached=is_participation_breached,
                is_sharpe_breached=is_sharpe_breached,
            ))

            # Diagnostic: unconstrained PnL peak, ignoring both gates. Reported so
            # the gap against the feasible optimum is visible, never as a target.
            if net_pnl_usd > best_unconstrained_pnl_usd:
                best_unconstrained_pnl_usd = net_pnl_usd
                unconstrained_pnl_aum = aum

            if is_exceeded:
                if still_feasible:
                    # First breach ends the feasible region. Capacity is the largest
                    # AUM with an unbroken feasible run beneath it, so a later
                    # non-breaching point must not resurrect it.
                    still_feasible = False
                    if is_participation_breached:
                        limiting_factor = "ADV_PARTICIPATION_LIMIT"
                    else:
                        limiting_factor = "MIN_SHARPE_BREACH"
                        first_point_breached_on_sharpe = (i == 0)
            elif still_feasible:
                max_capacity_aum = aum
                max_capacity_net_sharpe = net_sharpe
                if net_pnl_usd > best_feasible_pnl_usd:
                    best_feasible_pnl_usd = net_pnl_usd
                    optimal_pnl_aum = aum

        if limiting_factor is None:
            # Nothing breached anywhere in the grid. The search was censored by its
            # own ceiling; this is NOT evidence of unlimited capacity.
            limiting_factor = "SEARCH_RANGE_EXHAUSTED"
            search_range_exhausted = True
        else:
            search_range_exhausted = False
            if first_point_breached_on_sharpe:
                # The gate was never cleared, not even at one step. The strategy is
                # not capacity-constrained; it simply does not clear the bar.
                limiting_factor = "BELOW_MIN_SHARPE_AT_ALL_SIZES"

        notes = (
            f"STRATEGY CAPACITY REPORT [{params.strategy_id}]: Frictionless Sharpe = "
            f"{frictionless_sharpe} (rf={params.risk_free_rate_pct:.4f}), Max Capacity AUM = "
            f"${max_capacity_aum:,.0f} (+/- ${aum_step_usd:,.0f} grid resolution), "
            f"Feasible Optimal PnL AUM = ${optimal_pnl_aum:,.0f}, "
            f"Limiting Factor = {limiting_factor}, impact_gamma = {self.impact_gamma}."
        )
        if search_range_exhausted:
            notes += (
                f" WARNING: no gate breached below ${max_search_aum_usd:,.0f}; the reported "
                "capacity is the search ceiling, not a measured limit. Widen "
                "max_search_aum_usd before treating it as an allocation target."
            )
        if unconstrained_pnl_aum > max_capacity_aum:
            notes += (
                f" NOTE: unconstrained net-PnL peak sits at ${unconstrained_pnl_aum:,.0f}, "
                f"above the feasible capacity of ${max_capacity_aum:,.0f}. Dollar PnL keeps "
                "rising past the point where the strategy breaches its own gates."
            )

        logger.info(notes)

        return StrategyCapacityReport(
            strategy_id=params.strategy_id,
            frictionless_sharpe_ratio=frictionless_sharpe,
            max_capacity_aum_usd=max_capacity_aum,
            optimal_sharpe_capacity_aum_usd=optimal_pnl_aum,
            capacity_curve=capacity_curve,
            limiting_factor=limiting_factor,
            audit_notes=notes,
            unconstrained_max_pnl_aum_usd=unconstrained_pnl_aum,
            capacity_resolution_usd=aum_step_usd,
            search_range_exhausted=search_range_exhausted,
            max_capacity_net_sharpe=max_capacity_net_sharpe,
            impact_gamma=self.impact_gamma,
            risk_free_rate_pct=params.risk_free_rate_pct,
        )
