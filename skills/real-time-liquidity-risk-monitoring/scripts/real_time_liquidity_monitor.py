"""
real-time-liquidity-risk-monitoring: portfolio market-liquidity monitor covering Days
to Liquidate (DTL), bid-ask spread spikes, L2 depth collapse, and a Liquidity-Adjusted
VaR (L-VaR) add-on.

This module measures **market (asset) liquidity** - how costly it is to unwind a
position - not **funding liquidity** (cash/collateral adequacy). Basel III's LCR and
NSFR govern the latter for banks and are *not* the standard implemented here; the
market-liquidity anchors actually used are cited in `references/standards.md`.

Metrics
-------

1. Days to Liquidate (participation-capped horizon):

       DTL_i = |Position_i| / (MaxParticipationPct * ADV_i)

   Assumes the desk can trade its full participation cap every session until flat, at
   an ADV that stays constant over the horizon. Both assumptions fail in exactly the
   stress the metric is meant to catch (see Limitations).

2. Spread spike:   SpreadRatio_i = Spread_i / NormalSpread_i
3. Depth collapse: DepthDrop_i   = 1 - (Depth_i / NormalDepth_i)

4. Cost of Liquidity (COL), per position:

       COL_i = 0.5 * Notional_i * (RelativeSpread_i + k * DTL_i)

   The first term is the exogenous half-spread cost of Bangia, Diebold, Schuermann and
   Stroughair (1999), Eq. 4: COL = 0.5 * P_t * (S_bar + a * sigma_spread), with relative
   spread defined as (Ask - Bid) / Mid. This implementation uses the **current snapshot**
   spread and **a = 0** - it carries no spread-volatility tail term, so it estimates the
   mean-condition half-spread cost, not the 99th-percentile spread cost BDSS target. It
   is therefore an under-estimate of exogenous liquidity risk in a tail.

   The second term, ``k * DTL_i``, is an *endogenous* market-impact proxy. It is
   **not** a canonical result: no regulator, exchange, or published study prescribes a
   value for ``k``. It must be calibrated against the desk's own realized transaction
   costs. Measured equity impact is concave in trade rate (Almgren, Thum, Hauptmann and
   Li, 2005, estimate a 3/5 power law and explicitly reject the square-root exponent),
   whereas this term is linear in liquidation horizon, so it grows faster than the
   empirical evidence supports for large positions. At the default k = 0.10/day a 5-day
   DTL produces an impact charge of 25% of notional, implausibly large for a liquid
   equity - treat the default as a placeholder to be calibrated or set to 0.0, never as
   a recommended value.

5. Portfolio L-VaR = baseline VaR + sum(COL_i).

   Liquidation costs are summed with no diversification benefit (costs are additive,
   unlike return risk), and COL is added directly on top of the mid-price VaR. BDSS
   make this additive assumption deliberately: they assume extreme return moves and
   extreme spread moves occur concurrently (op. cit., Sec. III.B). Where that
   assumption does not hold, the sum is conservative.

Limitations (documented, deliberate)
------------------------------------

- **ADV is an input, not a measurement.** A trailing 30-day ADV computed in calm
  markets overstates the volume actually available in a dislocation, and DTL scales
  inversely with it. The caller must supply a *stressed* ADV if the report is to drive
  stress governance; this module cannot detect a stale one.
- **Constant participation.** DTL assumes the cap is achievable every session. It
  models no partial fills, halts, limit-up/limit-down states, or auction-only sessions.
- **Snapshot, not distribution.** Every metric comes from one point-in-time observation
  per symbol. There is no smoothing, no staleness check, and no timestamp - the caller
  owns data recency.
- **No cross-symbol correlation.** Symbols are treated independently, so crowding -
  the whole market unwinding the same names at once - is out of scope.
- **Not a control.** This module reports; it does not block, resize, or cancel. Wire
  the report into a separate enforcement path.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

#: Report statuses. ``NO_POSITIONS`` is distinct from ``LIQUIDITY_HEALTHY``: an empty
#: portfolio has not been assessed, it simply has nothing to assess.
STATUS_HEALTHY = "LIQUIDITY_HEALTHY"
STATUS_ALERT = "LIQUIDITY_RISK_ALERT"
STATUS_NO_POSITIONS = "NO_POSITIONS"


def _require_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{name} is non-finite ({value}). Non-finite inputs propagate silently: "
            "every NaN comparison is False, so a NaN metric raises no flag and the "
            "portfolio reads as healthy while its L-VaR is NaN."
        )
    return float(value)


def _require_positive_finite(name: str, value: float) -> float:
    v = _require_finite(name, value)
    if v <= 0.0:
        raise ValueError(f"{name} must be > 0, got {v}.")
    return v


def _require_non_negative_finite(name: str, value: float) -> float:
    v = _require_finite(name, value)
    if v < 0.0:
        raise ValueError(f"{name} must be >= 0, got {v}.")
    return v


@dataclass
class Config:
    """
    Monitor thresholds. These are **library defaults, not regulatory limits** - no
    regulator publishes a mandatory DTL cap, spread-spike ratio, or depth-drop
    threshold for a trading book. Calibrate each against the desk's own mandate and
    record the rationale (see `references/standards.md`).

    All three breach tests are **inclusive**: a metric landing exactly on its threshold
    is reported as a breach. A monitor that stays silent precisely at its configured
    limit is a foot-gun, and exact float equality is not something a real-time feed can
    be relied on to avoid.
    """
    max_dtl_threshold_days: float = 2.0        # Alert at DTL >= this many days
    max_participation_pct: float = 0.10        # Fraction of ADV assumed tradable per session
    spread_spike_threshold_ratio: float = 2.0  # Alert at Spread / NormalSpread >= this
    depth_drop_threshold_pct: float = 0.50     # Alert at depth drop >= this fraction
    market_impact_coeff_per_day: float = 0.10  # k in the COL impact term. UNCALIBRATED - see module docstring.

    def __post_init__(self) -> None:
        _require_positive_finite("max_dtl_threshold_days", self.max_dtl_threshold_days)
        _require_positive_finite("spread_spike_threshold_ratio", self.spread_spike_threshold_ratio)
        _require_finite("max_participation_pct", self.max_participation_pct)
        if not 0.0 < self.max_participation_pct <= 1.0:
            raise ValueError(
                f"max_participation_pct must be in (0, 1], got {self.max_participation_pct}. "
                "A cap above 100% of ADV would claim the desk can trade more than the "
                "entire session's volume."
            )
        _require_finite("depth_drop_threshold_pct", self.depth_drop_threshold_pct)
        if not 0.0 < self.depth_drop_threshold_pct <= 1.0:
            raise ValueError(
                f"depth_drop_threshold_pct must be in (0, 1], got {self.depth_drop_threshold_pct}."
            )
        _require_non_negative_finite("market_impact_coeff_per_day", self.market_impact_coeff_per_day)


@dataclass
class PortfolioPositionLiquidity:
    """
    One point-in-time liquidity observation for one symbol.

    ``position_size`` may be negative (a short): magnitude is used throughout, since
    buying back a short consumes liquidity exactly as selling a long does.
    ``bid_ask_spread`` and ``normal_spread`` are quoted in **price units**, not basis
    points; ``adv``, ``l2_depth_top3`` and ``normal_l2_depth`` in **share/contract
    units**. Mixing units silently produces a plausible-looking wrong number, so the
    validator checks only signs and finiteness - unit consistency is the caller's.
    """
    symbol: str
    position_size: float
    current_price: float
    adv: float                           # Average Daily Volume, in shares/contracts
    bid_ask_spread: float
    l2_depth_top3: float
    normal_spread: float
    normal_l2_depth: float


@dataclass
class PositionLiquidityMetrics:
    symbol: str
    notional_usd: float
    days_to_liquidate: float
    spread_ratio: float
    depth_drop_pct: float                # Percent, floored at 0.0 (depth above normal reports 0.0)
    dtl_breached: bool
    spread_spike_flag: bool
    depth_collapse_flag: bool
    l_var_contribution_usd: float        # COL_i: this position's total cost-of-liquidity add-on
    spread_cost_usd: float = 0.0         # Half-spread component of COL_i (BDSS Eq. 4, a = 0)
    impact_cost_usd: float = 0.0         # Market-impact component of COL_i (uncalibrated k)


@dataclass
class RealTimeLiquidityReport:
    total_portfolio_notional_usd: float
    max_days_to_liquidate: float
    total_dtl_breaches: int
    total_spread_spikes: int
    total_depth_collapses: int
    portfolio_l_var_usd: float
    position_metrics: List[PositionLiquidityMetrics]
    status: str                          # STATUS_HEALTHY | STATUS_ALERT | STATUS_NO_POSITIONS
    audit_notes: str
    baseline_var_usd: float = 0.0             # The mid-price VaR the COL was added to
    total_cost_of_liquidity_usd: float = 0.0  # sum(COL_i); portfolio_l_var = baseline + this


def _validate_position(p: PortfolioPositionLiquidity, index: int) -> None:
    """
    Rejects observations that cannot produce a meaningful liquidity metric.

    The previous implementation clamped bad inputs instead (``max(1.0, adv * pct)``,
    ``max(0.0001, normal_spread)``, ``max(0.01, current_price)``). Clamping does not
    make a number safe, it makes it fabricated: ADV = 0 yielded one day of liquidation
    per share held (100,000 shares -> 100,000 days) and a $50bn L-VaR on a $10m
    position. A liquidity monitor fed unusable data must fail loudly, not invent a
    reading that downstream governance will treat as measured.
    """
    where = f"positions[{index}] (symbol={p.symbol!r})"
    if not isinstance(p.symbol, str) or not p.symbol.strip():
        raise ValueError(f"{where}: symbol must be a non-empty string.")
    _require_finite(f"{where}.position_size", p.position_size)
    _require_positive_finite(f"{where}.current_price", p.current_price)
    _require_positive_finite(f"{where}.adv", p.adv)
    _require_non_negative_finite(f"{where}.bid_ask_spread", p.bid_ask_spread)
    _require_non_negative_finite(f"{where}.l2_depth_top3", p.l2_depth_top3)
    _require_positive_finite(f"{where}.normal_spread", p.normal_spread)
    _require_positive_finite(f"{where}.normal_l2_depth", p.normal_l2_depth)


class RealTimeLiquidityMonitorEngine:
    """
    Real-time portfolio market-liquidity monitor: Days to Liquidate, bid-ask spread
    spikes, L2 depth collapse, and a Liquidity-Adjusted VaR add-on.

    Stateless and side-effect free apart from logging: one call, one report. Safe to
    call concurrently from several threads with one instance, provided the shared
    ``Config`` is not mutated after construction.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def audit_portfolio_liquidity(
        self,
        positions: Sequence[PortfolioPositionLiquidity],
        baseline_var_usd: float,
    ) -> RealTimeLiquidityReport:
        """
        Audits DTL, spread spikes and L2 depth collapse, and adds a cost-of-liquidity
        charge to ``baseline_var_usd`` to produce a portfolio L-VaR.

        ``baseline_var_usd`` is the desk's own mid-price VaR for the same portfolio, at
        the same confidence level and horizon, in the same currency. It is **required**:
        the previous $100,000 default silently fabricated a VaR baseline for any caller
        who forgot to pass one, and every L-VaR downstream inherited it.

        Raises ``ValueError``/``TypeError`` on non-finite or non-positive inputs and on
        duplicate symbols - a symbol appearing twice double-counts its notional and its
        liquidation cost, which reads as a portfolio twice the real size.
        """
        base_var = _require_non_negative_finite("baseline_var_usd", baseline_var_usd)

        if positions is None:
            raise ValueError("positions is required, got None.")
        position_list = list(positions)

        if not position_list:
            notes = "No positions provided for liquidity audit."
            logger.info(notes)
            return RealTimeLiquidityReport(
                total_portfolio_notional_usd=0.0,
                max_days_to_liquidate=0.0,
                total_dtl_breaches=0,
                total_spread_spikes=0,
                total_depth_collapses=0,
                portfolio_l_var_usd=round(base_var, 2),
                position_metrics=[],
                status=STATUS_NO_POSITIONS,
                audit_notes=notes,
                baseline_var_usd=round(base_var, 2),
                total_cost_of_liquidity_usd=0.0,
            )

        seen_symbols: Set[str] = set()
        for i, p in enumerate(position_list):
            _validate_position(p, i)
            if p.symbol in seen_symbols:
                raise ValueError(
                    f"positions[{i}]: duplicate symbol {p.symbol!r}. Net a symbol's lots "
                    "into a single position before auditing; passing it twice "
                    "double-counts notional, liquidation cost, and L-VaR."
                )
            seen_symbols.add(p.symbol)

        cfg = self.config
        pos_metrics_list: List[PositionLiquidityMetrics] = []
        total_notional = 0.0
        max_dtl = 0.0
        dtl_breaches = 0
        spread_spikes = 0
        depth_collapses = 0
        total_col_usd = 0.0

        for p in position_list:
            qty = abs(p.position_size)
            notional = qty * p.current_price
            total_notional += notional

            # 1. Days to Liquidate at the participation cap.
            daily_cap_vol = p.adv * cfg.max_participation_pct
            dtl = qty / daily_cap_vol
            max_dtl = max(max_dtl, dtl)
            dtl_flag = dtl >= cfg.max_dtl_threshold_days
            if dtl_flag:
                dtl_breaches += 1

            # 2. Spread spike ratio versus this symbol's own baseline.
            spread_ratio = p.bid_ask_spread / p.normal_spread
            spread_flag = spread_ratio >= cfg.spread_spike_threshold_ratio
            if spread_flag:
                spread_spikes += 1

            # 3. Top-of-book depth collapse versus baseline.
            depth_drop = 1.0 - (p.l2_depth_top3 / p.normal_l2_depth)
            depth_flag = depth_drop >= cfg.depth_drop_threshold_pct
            if depth_flag:
                depth_collapses += 1

            # 4. Cost of Liquidity: BDSS half-spread (a = 0) + uncalibrated impact proxy.
            rel_spread = p.bid_ask_spread / p.current_price
            spread_cost = 0.5 * notional * rel_spread
            impact_cost = 0.5 * notional * cfg.market_impact_coeff_per_day * dtl
            col = spread_cost + impact_cost
            total_col_usd += col

            pos_metrics_list.append(
                PositionLiquidityMetrics(
                    symbol=p.symbol,
                    notional_usd=round(notional, 2),
                    days_to_liquidate=round(dtl, 2),
                    spread_ratio=round(spread_ratio, 2),
                    depth_drop_pct=round(max(0.0, depth_drop * 100.0), 1),
                    dtl_breached=dtl_flag,
                    spread_spike_flag=spread_flag,
                    depth_collapse_flag=depth_flag,
                    l_var_contribution_usd=round(col, 2),
                    spread_cost_usd=round(spread_cost, 2),
                    impact_cost_usd=round(impact_cost, 2),
                )
            )

            if dtl_flag or spread_flag or depth_flag:
                logger.warning(
                    "Liquidity breach %s: DTL=%.2fd (breach=%s), spread=%.2fx (spike=%s), "
                    "depth drop=%.1f%% (collapse=%s), cost of liquidity=$%.2f",
                    p.symbol, dtl, dtl_flag, spread_ratio, spread_flag,
                    max(0.0, depth_drop * 100.0), depth_flag, col,
                )

        portfolio_l_var = base_var + total_col_usd
        is_healthy = dtl_breaches == 0 and spread_spikes == 0 and depth_collapses == 0
        status = STATUS_HEALTHY if is_healthy else STATUS_ALERT

        notes = (
            f"REAL-TIME LIQUIDITY REPORT [{status}]: "
            f"Portfolio Notional = ${total_notional:,.2f}, Max DTL = {max_dtl:.2f} days "
            f"(Breaches: {dtl_breaches}, Spread Spikes: {spread_spikes}, Depth Collapses: {depth_collapses}). "
            f"Base VaR = ${base_var:,.2f}, Cost of Liquidity = ${total_col_usd:,.2f}, "
            f"L-VaR = ${portfolio_l_var:,.2f}."
        )

        if not is_healthy:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RealTimeLiquidityReport(
            total_portfolio_notional_usd=round(total_notional, 2),
            max_days_to_liquidate=round(max_dtl, 2),
            total_dtl_breaches=dtl_breaches,
            total_spread_spikes=spread_spikes,
            total_depth_collapses=depth_collapses,
            portfolio_l_var_usd=round(portfolio_l_var, 2),
            position_metrics=pos_metrics_list,
            status=status,
            audit_notes=notes,
            baseline_var_usd=round(base_var, 2),
            total_cost_of_liquidity_usd=round(total_col_usd, 2),
        )
