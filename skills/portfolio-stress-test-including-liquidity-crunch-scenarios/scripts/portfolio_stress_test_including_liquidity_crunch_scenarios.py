"""
portfolio-stress-test-including-liquidity-crunch-scenarios: couples a scenario price
shock with a liquidity-crunch haircut to produce a liquidation-aware stressed loss,
a per-position Days-to-Liquidate (DTL) horizon, and a bottleneck audit.

A conventional VaR or scenario P&L marks the book at shocked prices and stops there.
That answers "what is the book worth after the shock?" but not "what does it cost to
get out of it, and how long am I stuck?" Those are different numbers, and in a crunch
the second one dominates. This module reports all three separately::

    price_shock_loss  = -sum( Q_i * P_i * shock_i )              # signed; +ve = loss
    spread_cost       =  sum( 0.5 * s_i * |Q_i| * P_i )          # exogenous
    market_impact     =  sum( Y * sigma_i * sqrt(phi_i) * |Q_i| * P_i )   # endogenous
    total_stressed_loss = price_shock_loss + spread_cost + market_impact

with the liquidity state under stress given by::

    stressed_adv_i   = adv_shares_i * (1 - liquidity_drop_pct)
    daily_capacity_i = participation_rate * stressed_adv_i
    DTL_i            = |Q_i| / daily_capacity_i
    phi_i            = |Q_i| / stressed_adv_i      # metaorder volume fraction

ESMA calls these the two principal approaches to simulating asset liquidity --
"Liquidation cost and time to liquidity" (ESMA34-39-897 para. 43) -- and requires that
liquidation cost account for "asset type, liquidation horizon and the size of the
trade/order" (para. 44). This module reports both, per position and in aggregate.

Provenance of the cost model
----------------------------
**Spread (exogenous) cost.** Bangia, Diebold, Schuermann and Stroughair, *Modeling
Liquidity Risk with Implications for Traditional Market Risk Measurement and
Management* (Wharton Financial Institutions Center 99-06, 1999) add a cost of liquidity
to VaR equal to ``0.5 * P * (mu_s + z * sigma_s)`` -- **half** the proportional bid-ask
spread, because a liquidation crosses from the mid to the bid once. It is charged
**once per share**, not once per share per session: slicing a position over DTL days
does not make each share pay the spread DTL times. a naive implementation charged
the *full* spread on the *full* position value for each of up to ten days, overstating
this component by up to 20x. ``spread_expansion_factor`` carries the stress: ESMA34-39-897
para. 45 notes stressed conditions are "typically characterised by higher volatility,
lower liquidity (e.g. higher bid-ask spread) and longer time to liquidate".

**Market impact (endogenous) cost.** Toth, Lemperiere, Deremble, de Lataillade,
Kockelkoren and Bouchaud, *Anomalous price impact and the critical nature of liquidity
in financial markets*, Phys. Rev. X **1**, 021006 (2011), Eq. (1)::

    Delta(Q) = Y * sigma * sqrt(Q / V)

where sigma is the asset's daily volatility, V its daily traded volume, and "the
numerical constant Y is of order unity" (hence ``DEFAULT_IMPACT_COEFFICIENT_Y = 1.0``).
In that paper Delta is measured as the average execution shortfall of the metaorder, so
using it directly as a cost fraction is faithful to the quantity that was fitted. The
term is priced only for positions that supply ``daily_volatility``; the rest are listed
in ``positions_missing_volatility`` and contribute zero, so a missing input is visible
in the report rather than silently priced at zero.

Why the liquidity crunch is modelled as a *capacity* haircut, not a volume forecast
----------------------------------------------------------------------------------
``liquidity_drop_pct`` haircuts ADV, but the empirical record of a crash is not that
volume falls. In March 2020 it rose: trading venues "were able to handle record trading
volumes" and the FSB describes "the surge in trading activity" (FSB, *Holistic Review
of the March Market Turmoil*, 17 November 2020). What collapsed was **depth**. Market
depth in US equities and Treasuries "declined to levels seen during the worst period of
the 2008 financial crisis", and per the IMF's Global Markets Monitor 10-year UST depth
fell **93%** from its February average and 30-year depth **76%**, both the lowest on
record (ibid., p. 8 and n. 2) -- "accompanied by a large increase in transaction costs
in many inter-dealer markets".

So the haircut here stands for the collapse in the volume a seller can absorb *at a
tolerable price*, not for a forecast of tape volume. Read it as a depth/capacity
haircut. Sizing it from an observed volume decline understates the crunch, because in
the episodes this skill exists to model the tape got busier while the book got thinner.

Limitations (documented, deliberate)
------------------------------------
- **The scenario is supplied, not generated.** Shocks, the liquidity drop and the
  spread expansion are the caller's judgement. ESMA34-39-897 para. 45 is explicit that
  managers "should not only refer to historical observations of stressed markets".
- **Square-root impact is used outside its fitted range.** Toth et al. fit Q/V "ranging
  from a few 10^-4 to a few %". A stressed portfolio routinely implies phi of 1.0 or
  more, orders of magnitude beyond that. Positions above ``IMPACT_CALIBRATION_MAX_PHI``
  are reported in ``positions_outside_impact_calibration``; their impact figure should
  be read as an order-of-magnitude flag that the position is untradeable in the assumed
  horizon, not as a cost estimate.
- **No correlation, funding or forced-seller feedback.** Shocks are applied
  independently per symbol from the scenario vector. Crowding, margin spirals and the
  fact that everyone in the same trade exits through the same door are out of scope --
  see ``tail-correlation-between-strategies-under-stress`` and
  ``correlation-aware-exposure-limits``.
- **Netting is assumed to be economically real.** ``price_shock_loss`` nets longs
  against shorts. That is only meaningful if the shock vector is internally consistent
  across the legs; a hedge that nets on paper can still gap in a crunch.
- **One unit of ``current_price`` is one unit of ``adv_shares``.** For futures and
  options pass price per contract and ADV in contracts.
- **Not a capital requirement.** No regulator-set methodology is implemented here.
"""
import logging
import math
from collections.abc import Sequence as _SequenceABC
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Share of a session's stressed volume the liquidation is assumed to consume.
#: A policy default, not a regulatory limit -- see references/standards.md.
DEFAULT_PARTICIPATION_RATE = 0.10

#: Sessions to flat above which a position is flagged as a liquidity bottleneck.
#: A policy default. SEC Rule 22e-4's illiquid bucket is seven calendar days; this
#: library's default is deliberately tighter. Calibrate it to the horizon the book must
#: actually survive (margin call, redemption terms), not to this number.
DEFAULT_MAX_ALLOWED_DTL_DAYS = 5.0

#: "The numerical constant Y is of order unity" -- Toth et al. (2011), Eq. (1).
DEFAULT_IMPACT_COEFFICIENT_Y = 1.0

#: Upper end of the volume fraction Q/V over which Toth et al. fit the square-root law
#: ("a few 10^-4 to a few %"). Above this the impact figure is an extrapolation and is
#: reported as such. The boundary is this library's reading of "a few %", not a value
#: stated in the paper.
IMPACT_CALIBRATION_MAX_PHI = 0.10

#: Scenario key applying one shock to every symbol not listed explicitly.
DEFAULT_SHOCK_KEY = "DEFAULT"

#: Report status values.
STATUS_PASSED = "STRESS_TEST_PASSED"
STATUS_ILLIQUID_WARNING = "LIQUIDITY_CRUNCH_ILLIQUID_WARNING"

_BPS_PER_UNIT = 10_000.0


def _require_finite(value: float, name: str, context: str) -> float:
    """
    Rejects NaN, +/-Inf, bools and numeric strings before they reach the arithmetic.

    Every comparison against NaN is False, so an unguarded NaN passes ``price <= 0``,
    passes ``dtl > max_allowed`` and propagates into a report whose status field reads
    ``STRESS_TEST_PASSED``. A stress test that answers "passed" on corrupt reference
    data is worse than one that is absent, because the caller has been told the book
    survives the scenario. A numeric *string* almost always means an unparsed CSV or
    JSON field upstream, so it raises rather than being silently coerced.
    """
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a number, not a string ({context}), got {value!r}.")
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not a bool ({context}).")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric ({context}), got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite ({context}), got {numeric!r}.")
    return numeric


@dataclass
class Config:
    """Engine policy. Every threshold here is a library default, not a regulatory limit."""

    name: str = "Stress_Tester"
    max_allowed_dtl_days: float = DEFAULT_MAX_ALLOWED_DTL_DAYS
    daily_participation_rate: float = DEFAULT_PARTICIPATION_RATE
    impact_coefficient_y: float = DEFAULT_IMPACT_COEFFICIENT_Y

    def __post_init__(self) -> None:
        ctx = "stress test configuration"
        self.max_allowed_dtl_days = _require_finite(
            self.max_allowed_dtl_days, "max_allowed_dtl_days", ctx)
        if self.max_allowed_dtl_days <= 0.0:
            raise ValueError(
                f"max_allowed_dtl_days must be > 0 ({ctx}), got {self.max_allowed_dtl_days}.")

        self.daily_participation_rate = _require_finite(
            self.daily_participation_rate, "daily_participation_rate", ctx)
        if not 0.0 < self.daily_participation_rate <= 1.0:
            raise ValueError(
                f"daily_participation_rate must be in (0, 1] ({ctx}), "
                f"got {self.daily_participation_rate}.")

        self.impact_coefficient_y = _require_finite(
            self.impact_coefficient_y, "impact_coefficient_y", ctx)
        if self.impact_coefficient_y < 0.0:
            raise ValueError(
                f"impact_coefficient_y must be >= 0 ({ctx}), got {self.impact_coefficient_y}.")


@dataclass
class PortfolioPosition:
    """
    One holding.

    ``current_price`` and ``adv_shares`` must be in the same unit -- price per contract
    with ADV in contracts for derivatives, or the position is stressed at a fraction of
    its real size.
    """

    symbol: str
    quantity: float                           # + for long, - for short
    current_price: float
    adv_shares: float                         # Normal-conditions average daily volume
    spread_bps: float = 5.0                   # Normal-conditions bid-ask spread, bps
    daily_volatility: Optional[float] = None  # Stressed daily vol as a fraction (0.05 = 5%)

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {self.symbol!r}.")
        ctx = f"position {self.symbol}"
        self.quantity = _require_finite(self.quantity, "quantity", ctx)

        self.current_price = _require_finite(self.current_price, "current_price", ctx)
        if self.current_price <= 0.0:
            raise ValueError(f"current_price must be > 0 ({ctx}), got {self.current_price}.")

        # A zero or negative ADV was previously floored to 1.0 share/day, turning an
        # untradeable instrument into a finite DTL derived from a fabricated volume.
        self.adv_shares = _require_finite(self.adv_shares, "adv_shares", ctx)
        if self.adv_shares <= 0.0:
            raise ValueError(
                f"adv_shares must be > 0 ({ctx}), got {self.adv_shares}. An instrument with "
                f"no volume has no finite days-to-liquidate; handle it as unliquidatable.")

        self.spread_bps = _require_finite(self.spread_bps, "spread_bps", ctx)
        if self.spread_bps < 0.0:
            raise ValueError(f"spread_bps must be >= 0 ({ctx}), got {self.spread_bps}.")

        if self.daily_volatility is not None:
            self.daily_volatility = _require_finite(
                self.daily_volatility, "daily_volatility", ctx)
            if self.daily_volatility < 0.0:
                raise ValueError(
                    f"daily_volatility must be >= 0 ({ctx}), got {self.daily_volatility}.")


@dataclass
class StressScenario:
    """
    A scenario.

    ``price_shock_pct`` maps symbol -> return (``-0.20`` is a 20% fall); the key
    ``DEFAULT`` supplies a shock for any symbol not listed explicitly.
    ``liquidity_drop_pct`` is a haircut to executable **capacity**, not a forecast of
    tape volume -- see the module docstring.
    """

    scenario_name: str
    price_shock_pct: Dict[str, float]
    liquidity_drop_pct: float = 0.50
    spread_expansion_factor: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_name, str) or not self.scenario_name.strip():
            raise ValueError(
                f"scenario_name must be a non-empty string, got {self.scenario_name!r}.")
        ctx = f"scenario {self.scenario_name}"

        if not isinstance(self.price_shock_pct, dict) or not self.price_shock_pct:
            raise ValueError(f"price_shock_pct must be a non-empty mapping ({ctx}).")
        # Validate into a *copy*: coercing in place would mutate the caller's dict, and a
        # scenario is routinely reused across several runs and several books.
        validated: Dict[str, float] = {}
        for symbol, shock in self.price_shock_pct.items():
            value = _require_finite(shock, f"price_shock_pct[{symbol!r}]", ctx)
            if value < -1.0:
                raise ValueError(
                    f"price_shock_pct[{symbol!r}] must be >= -1.0 ({ctx}), got {value}. "
                    f"A shock below -100% implies a negative price.")
            validated[symbol] = value
        self.price_shock_pct = validated

        self.liquidity_drop_pct = _require_finite(
            self.liquidity_drop_pct, "liquidity_drop_pct", ctx)
        if not 0.0 <= self.liquidity_drop_pct < 1.0:
            raise ValueError(
                f"liquidity_drop_pct must be in [0, 1) ({ctx}), got {self.liquidity_drop_pct}. "
                f"A 100% capacity loss is a trading halt, not a days-to-liquidate question.")

        self.spread_expansion_factor = _require_finite(
            self.spread_expansion_factor, "spread_expansion_factor", ctx)
        if self.spread_expansion_factor < 0.0:
            raise ValueError(
                f"spread_expansion_factor must be >= 0 ({ctx}), "
                f"got {self.spread_expansion_factor}.")

    def shock_for(self, symbol: str) -> float:
        """
        Returns the shock for ``symbol``, falling back to the ``DEFAULT`` entry.

        Raises when neither is present. a naive engine fell back to a hard-coded
        -20%, so a symbol missing from the scenario was silently stress-tested against a
        shock nobody chose and which appeared nowhere in the report.
        """
        if symbol in self.price_shock_pct:
            return self.price_shock_pct[symbol]
        if DEFAULT_SHOCK_KEY in self.price_shock_pct:
            return self.price_shock_pct[DEFAULT_SHOCK_KEY]
        raise ValueError(
            f"scenario {self.scenario_name!r} defines no shock for {symbol!r} and no "
            f"{DEFAULT_SHOCK_KEY!r} entry. Add one explicitly rather than defaulting.")


@dataclass
class PositionStressResult:
    """Per-position breakdown, so an aggregate figure can be traced to its drivers."""

    symbol: str
    quantity: float
    gross_exposure_usd: float            # |Q| * P, pre-shock
    price_shock_pct: float
    price_shock_loss_usd: float          # +ve = loss, -ve = gain
    stressed_adv_shares: float
    daily_capacity_shares: float
    dtl_days: float
    volume_fraction: float               # phi = |Q| / stressed ADV
    stressed_spread_bps: float
    spread_cost_usd: float
    market_impact_cost_usd: float
    is_illiquid: bool
    impact_extrapolated: bool            # phi beyond the square-root law's fitted range
    impact_priced: bool                  # False when daily_volatility was not supplied


@dataclass
class StressTestReport:
    """Aggregate scenario result. Loss figures are positive when they are losses."""

    scenario_name: str
    total_portfolio_value_usd: float       # Gross exposure, sum |Q|*P
    net_exposure_usd: float                # Signed, sum Q*P
    price_shock_loss_usd: float            # +ve = loss, -ve = gain; longs and shorts net
    spread_cost_usd: float
    market_impact_cost_usd: float
    liquidity_slippage_haircut_usd: float  # spread_cost + market_impact
    total_stressed_loss_usd: float
    total_stressed_loss_pct_of_gross: float
    max_dtl_days: float
    illiquid_symbols: List[str]
    status: str
    audit_notes: str
    positions: List[PositionStressResult] = field(default_factory=list)
    positions_missing_volatility: List[str] = field(default_factory=list)
    positions_outside_impact_calibration: List[str] = field(default_factory=list)


class PortfolioStressTestEngine:
    """
    Couples a scenario price shock with a liquidity-crunch haircut and reports the
    stressed mark-to-market loss, the cost of liquidating into the crunch, and the
    Days-to-Liquidate horizon each position implies.

    The three components are reported separately and never merged: a book can be
    perfectly resilient to the shock and still be untradeable, and the two findings
    call for different responses.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run_stress_test(
        self, positions: Sequence[PortfolioPosition], scenario: StressScenario
    ) -> StressTestReport:
        """
        Runs ``scenario`` against ``positions``.

        Raises ``ValueError`` on a duplicated symbol: two rows for the same instrument
        each compute DTL against the full ADV, so a holding split across rows reports a
        liquidation horizon a fraction of its true one. Net the book before stressing it.
        """
        if isinstance(positions, (str, bytes)) or not isinstance(positions, _SequenceABC):
            raise ValueError("positions must be a sequence of PortfolioPosition.")
        if not isinstance(scenario, StressScenario):
            raise ValueError("scenario must be a StressScenario.")

        seen = set()
        for p in positions:
            if not isinstance(p, PortfolioPosition):
                raise ValueError(
                    f"positions must contain PortfolioPosition, got {type(p).__name__}.")
            if p.symbol in seen:
                raise ValueError(
                    f"duplicate symbol {p.symbol!r} in positions. Days-to-Liquidate is a "
                    f"property of the aggregate holding; net the book before stressing it.")
            seen.add(p.symbol)

        results: List[PositionStressResult] = []
        gross_exposure = 0.0
        net_exposure = 0.0
        price_shock_loss = 0.0
        spread_cost_total = 0.0
        impact_cost_total = 0.0
        max_dtl = 0.0
        illiquid_symbols: List[str] = []
        missing_vol: List[str] = []
        extrapolated: List[str] = []

        participation = self.config.daily_participation_rate

        for p in positions:
            abs_qty = abs(p.quantity)
            pos_val = abs_qty * p.current_price
            gross_exposure += pos_val
            net_exposure += p.quantity * p.current_price

            # 1. Stressed mark-to-market. Signed: a long gains on a positive shock and a
            #    short gains on a negative one, so a hedged book nets. The sign is
            #    flipped once so the reported field reads as a loss.
            shock = scenario.shock_for(p.symbol)
            position_loss = -(p.quantity * p.current_price * shock)
            price_shock_loss += position_loss

            # 2. Stressed capacity and Days-to-Liquidate.
            stressed_adv = p.adv_shares * (1.0 - scenario.liquidity_drop_pct)
            daily_capacity = participation * stressed_adv
            dtl_days = abs_qty / daily_capacity
            phi = abs_qty / stressed_adv
            max_dtl = max(max_dtl, dtl_days)

            is_illiquid = dtl_days > self.config.max_allowed_dtl_days
            if is_illiquid:
                illiquid_symbols.append(p.symbol)

            # 3. Exogenous spread cost: half the stressed spread, charged once per share
            #    (Bangia et al. 1999), not once per share per session.
            stressed_spread_bps = p.spread_bps * scenario.spread_expansion_factor
            spread_cost = 0.5 * (stressed_spread_bps / _BPS_PER_UNIT) * pos_val
            spread_cost_total += spread_cost

            # 4. Endogenous impact: Toth et al. (2011) Eq. (1), priced only where a
            #    volatility was supplied.
            impact_priced = p.daily_volatility is not None
            if impact_priced:
                impact_cost = (
                    self.config.impact_coefficient_y
                    * float(p.daily_volatility)
                    * math.sqrt(phi)
                    * pos_val
                )
            else:
                impact_cost = 0.0
                missing_vol.append(p.symbol)
            impact_cost_total += impact_cost

            impact_extrapolated = impact_priced and phi > IMPACT_CALIBRATION_MAX_PHI
            if impact_extrapolated:
                extrapolated.append(p.symbol)

            results.append(PositionStressResult(
                symbol=p.symbol,
                quantity=p.quantity,
                gross_exposure_usd=round(pos_val, 2),
                price_shock_pct=shock,
                price_shock_loss_usd=round(position_loss, 2),
                stressed_adv_shares=round(stressed_adv, 4),
                daily_capacity_shares=round(daily_capacity, 4),
                dtl_days=round(dtl_days, 4),
                volume_fraction=round(phi, 6),
                stressed_spread_bps=round(stressed_spread_bps, 4),
                spread_cost_usd=round(spread_cost, 2),
                market_impact_cost_usd=round(impact_cost, 2),
                is_illiquid=is_illiquid,
                impact_extrapolated=impact_extrapolated,
                impact_priced=impact_priced,
            ))

        haircut = spread_cost_total + impact_cost_total
        total_stressed_loss = price_shock_loss + haircut
        loss_pct = (total_stressed_loss / gross_exposure) if gross_exposure > 0.0 else 0.0

        status = STATUS_ILLIQUID_WARNING if illiquid_symbols else STATUS_PASSED
        notes = (
            f"PORTFOLIO STRESS TEST [{scenario.scenario_name} - {status}]: "
            f"Gross Exposure = ${gross_exposure:,.2f}, Net Exposure = ${net_exposure:,.2f}, "
            f"Price Shock Loss = ${price_shock_loss:,.2f}, "
            f"Spread Cost = ${spread_cost_total:,.2f}, "
            f"Market Impact = ${impact_cost_total:,.2f}, "
            f"Liquidity Haircut = ${haircut:,.2f}, "
            f"Total Stressed Loss = ${total_stressed_loss:,.2f} ({loss_pct:.2%}). "
            f"Max DTL = {max_dtl:.2f} days, Illiquid Symbols = {illiquid_symbols}."
        )
        if missing_vol:
            notes += (
                f" NOTE: market impact NOT priced for {missing_vol} "
                f"(no daily_volatility supplied); the haircut is a lower bound.")
        if extrapolated:
            notes += (
                f" NOTE: volume fraction beyond the square-root law's fitted range for "
                f"{extrapolated}; read their impact as a flag, not a cost estimate.")

        if illiquid_symbols:
            logger.warning("LIQUIDITY CRUNCH WARNING: %s", notes)
        else:
            logger.info("%s", notes)

        return StressTestReport(
            scenario_name=scenario.scenario_name,
            total_portfolio_value_usd=round(gross_exposure, 2),
            net_exposure_usd=round(net_exposure, 2),
            price_shock_loss_usd=round(price_shock_loss, 2),
            spread_cost_usd=round(spread_cost_total, 2),
            market_impact_cost_usd=round(impact_cost_total, 2),
            liquidity_slippage_haircut_usd=round(haircut, 2),
            total_stressed_loss_usd=round(total_stressed_loss, 2),
            total_stressed_loss_pct_of_gross=round(loss_pct, 6),
            max_dtl_days=round(max_dtl, 4),
            illiquid_symbols=illiquid_symbols,
            status=status,
            audit_notes=notes,
            positions=results,
            positions_missing_volatility=missing_vol,
            positions_outside_impact_calibration=extrapolated,
        )
