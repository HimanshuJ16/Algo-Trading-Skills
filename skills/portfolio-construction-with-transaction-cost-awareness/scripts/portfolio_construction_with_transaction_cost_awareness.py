"""
portfolio-construction-with-transaction-cost-awareness: turnover-aware rebalance
planner that applies a no-trade buffer band and prices the resulting trade list
with proportional (commission + spread) and quadratic (market impact) costs.

WHAT THIS IS
------------
A deterministic, per-asset *rebalance filter and cost accountant*. Given current
and target weights it decides which assets to trade, prices those trades, and
reports gross return, total cost, net return, turnover, and the resulting weight
vector.

WHAT THIS IS NOT
----------------
This module performs **no optimization**. There is no covariance matrix, no risk
aversion parameter, no mean-variance objective, and no quadratic program: nothing
here searches over weight vectors. The target weights are an *input*, produced
upstream by whatever allocator the caller uses. The engine only decides whether
each proposed trade survives the buffer band and what it costs. Callers who need
a genuine net-of-cost optimizer must solve that problem elsewhere and feed the
solution in as ``target_weight``.

COST MODEL
----------
Per traded asset, with ``dw`` the weight change as a fraction of portfolio value:

    TC_i = (commission_rate + spread_cost_bps / 10_000) * |dw_i|   # proportional
         + impact_coeff * dw_i ** 2                                # quadratic

Costs are expressed as a fraction of total portfolio value, the same unit as the
weights, so they subtract directly from the weighted expected return.

The quadratic impact term is the Garleanu-Pedersen (2013) tractable form: a cost
quadratic in trade size is exactly equivalent to a *linear* price impact function.
It is chosen for tractability and additivity, not because it matches the data.
Empirically, metaorder impact is **concave** in size -- the "square-root law",
with fitted exponents in roughly the 0.4-0.7 range (Almgren et al. 2005; Kyle and
Obizhaeva 2016). A quadratic model therefore *understates* the cost of small
trades and *overstates* the cost of large ones relative to observed impact. Treat
``impact_coeff`` as a calibrated fudge factor fitted to your own realized slippage
over your own typical trade sizes, not as a transferable physical constant. There
is no defensible universal default; see ``DEFAULT_IMPACT_COEFF``.

NO-TRADE BAND POLICY
--------------------
Under purely proportional costs the optimal policy is a no-trade region whose
breach is corrected by trading back to the **nearest boundary**, not all the way
to the target (Constantinides 1986; Davis and Norman 1990). Trading to target
overshoots: it pays proportional cost on weight change that the theory says buys
no utility, and it makes the next drift more likely to breach the band again.

Both policies are available:

- ``trade_to_band_edge=False`` (default): breach the band, snap fully to target.
  Simpler, and the historical behaviour of this engine -- retained as the default
  so existing callers are not silently re-sized.
- ``trade_to_band_edge=True``: breach the band, move only to the band edge, i.e.
  ``final = current +/- threshold``. Closer to the theoretical optimum for
  proportional costs.

Note the theory assumes proportional costs only. With a meaningful *fixed* cost
per trade the optimum moves to a point strictly inside the band; neither mode
implements that, and this engine models no fixed per-trade cost at all.

TURNOVER CONVENTION
-------------------
``total_turnover`` is **two-way** turnover, the L1 norm ``sum |dw_i|`` over traded
assets. A full liquidation-and-replacement of a fully invested long portfolio is
2.0 under this convention. ``one_way_turnover`` is half that, the convention most
fund disclosures use. ``max_turnover_limit`` is compared against the *two-way*
figure. Mixing the two conventions silently doubles or halves an effective limit,
so both are reported explicitly.

BUDGET / SELF-FINANCING
-----------------------
Suppressing some trades while executing others breaks the budget identity: the
final weight vector generally sums to neither the current nor the target sum. The
difference is real money that has to come from, or go to, cash. The engine
computes it (``net_weight_change``, ``final_weight_sum``) and flags a plan that
is not self-financing rather than returning a weight vector that cannot be
executed. Weight sums are *reported, not enforced*: portfolios legitimately hold
cash or run leverage, so the engine cannot know the intended budget.

OTHER LIMITATIONS
-----------------
- **Single period, no schedule.** Costs are charged as if each trade executes at
  once. Nothing here slices an order or models a participation rate.
- **Weights, not shares.** No lot sizing, tick rounding, or minimum notional.
- **Expected returns are taken on faith.** ``expected_return`` is an input; the
  engine never validates that it is achievable or horizon-matched to the cost.
  Comparing an annual alpha against a one-off rebalance cost overstates net
  return; align the horizons before trusting ``net_expected_return``.
- **Costs are symmetric.** Buying and selling are priced identically. Real
  markets charge asymmetric spreads, borrow fees on shorts, and taxes on sales.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

#: Basis points per unit (1.0 = 100% = 10,000 bps).
BPS_PER_UNIT = 10_000.0

#: Sanity bound on any single weight. Weights are *fractions* (0.40 = 40%), never
#: percentages. Passing 40 for 40% inflates the quadratic impact term by 10,000x,
#: so this guard rejects the percent-vs-fraction mistake loudly instead of
#: returning a plausible-looking but wildly mispriced plan. Set generously so that
#: legitimate leveraged or short books are not rejected.
MAX_ABS_WEIGHT = 10.0

#: Tolerance (in weight units) within which a rebalance plan counts as
#: self-financing. 1e-9 absorbs float noise only, not real funding gaps.
SELF_FINANCING_TOLERANCE = 1e-9

#: Tolerances for the band comparison. Without these the inclusive band is a lie:
#: a 0.20 -> 0.18 move against a 0.02 threshold computes as 0.020000000000000018
#: in binary floating point and TRADES, incurring exactly the cost the band exists
#: to suppress. Whether a boundary case suppresses would otherwise depend on the
#: binary representation of the particular weights rather than on the contract.
BAND_REL_TOL = 1e-9
BAND_ABS_TOL = 1e-12

#: Placeholder impact coefficient. There is NO defensible universal value: the
#: correct coefficient depends on the instrument's liquidity, the portfolio's
#: notional, and the execution horizon, and must be fitted to realized slippage.
#: At this value a 10% weight trade is charged 0.5 * 0.10^2 = 50 bps of portfolio
#: value in impact alone, which dwarfs the ~1 bp proportional term -- deliberately
#: conspicuous, so an uncalibrated default cannot pass unnoticed.
DEFAULT_IMPACT_COEFF = 0.5


@dataclass
class Config:
    enabled: bool = True
    rebalance_threshold: float = 0.02    # 2% weight shift buffer band (inclusive: |dw| <= threshold is suppressed)
    max_turnover_limit: float = 0.50     # 50% max single-rebalance TWO-WAY turnover
    trade_to_band_edge: bool = False     # False: snap to target. True: move only to the band edge.


@dataclass
class AssetAlphaSpec:
    symbol: str
    expected_return: float               # e.g. 0.10 for 10%, over the caller's chosen horizon
    current_weight: float                # e.g. 0.40 for 40% of portfolio value
    target_weight: float                 # e.g. 0.45 for 45% of portfolio value


@dataclass
class CostSpec:
    """
    Note the deliberately preserved but inconsistent units: ``commission_rate`` is a
    decimal fraction while ``spread_cost_bps`` is in basis points. 0.0005 and 5.0
    are the same 5 bps. The field names are part of the public API and are not
    renamed; read them carefully when constructing this object, since supplying
    5.0 to ``commission_rate`` charges 500% per unit of turnover.
    """
    commission_rate: float = 0.0005      # 5 bps, as a DECIMAL FRACTION
    spread_cost_bps: float = 5.0         # 5 bps half-spread, in BASIS POINTS
    impact_coeff: float = DEFAULT_IMPACT_COEFF  # Quadratic impact; MUST be calibrated, see module docstring


@dataclass
class AssetTradeDecision:
    """Per-asset audit record. Costs are fractions of total portfolio value."""
    symbol: str
    current_weight: float
    target_weight: float
    final_weight: float
    proposed_delta: float                # target - current, before the band filter
    executed_delta: float                # final - current, after the band filter
    traded: bool
    proportional_cost: float             # (commission + spread) * |executed_delta|
    impact_cost: float                   # impact_coeff * executed_delta^2
    total_cost: float


@dataclass
class TCAwarePortfolioReport:
    total_assets: int
    gross_expected_return: float
    total_transaction_cost: float
    net_expected_return: float
    total_turnover: float                # TWO-WAY turnover, sum |dw| over traded assets
    traded_symbols: List[str]
    suppressed_symbols: List[str]
    status: str                          # 'REBALANCED_COST_OPTIMIZED', 'TURNOVER_LIMIT_EXCEEDED', 'ENGINE_DISABLED'
    audit_notes: str
    # --- Fields below are additive; they default so positional construction still works. ---
    final_weights: Dict[str, float] = field(default_factory=dict)
    trade_decisions: List[AssetTradeDecision] = field(default_factory=list)
    one_way_turnover: float = 0.0        # 0.5 * total_turnover
    turnover_limit_breached: bool = False
    current_weight_sum: float = 0.0
    final_weight_sum: float = 0.0
    net_weight_change: float = 0.0       # final_weight_sum - current_weight_sum; funding drawn from (+) or returned to (-) cash
    is_self_financing: bool = True


def _validate_finite(value: float, label: str) -> float:
    """Rejects non-numeric and non-finite inputs before they poison the arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{label} is non-finite ({value}). A non-finite weight or return propagates "
            "silently to a NaN net expected return, which compares False against every "
            "threshold and would let an unpriced rebalance through."
        )
    return float(value)


def _validate_config(config: Config) -> None:
    _validate_finite(config.rebalance_threshold, "Config.rebalance_threshold")
    _validate_finite(config.max_turnover_limit, "Config.max_turnover_limit")
    if config.rebalance_threshold < 0.0:
        raise ValueError(
            f"Config.rebalance_threshold must be >= 0, got {config.rebalance_threshold}. "
            "A negative band would suppress nothing and invert the filter's meaning."
        )
    if config.max_turnover_limit <= 0.0:
        raise ValueError(
            f"Config.max_turnover_limit must be > 0, got {config.max_turnover_limit}."
        )


def _validate_cost_spec(cost_spec: CostSpec) -> None:
    for label, value in (
        ("CostSpec.commission_rate", cost_spec.commission_rate),
        ("CostSpec.spread_cost_bps", cost_spec.spread_cost_bps),
        ("CostSpec.impact_coeff", cost_spec.impact_coeff),
    ):
        _validate_finite(value, label)
        if value < 0.0:
            raise ValueError(
                f"{label} must be >= 0, got {value}. A negative cost would credit the "
                "portfolio for trading and drive turnover without bound."
            )


def _validate_assets(assets: List[AssetAlphaSpec]) -> None:
    if assets is None:
        raise ValueError("assets is required, got None.")
    if not assets:
        raise ValueError(
            "assets is empty. An empty rebalance is not a successful rebalance; it is "
            "almost always an upstream universe-construction failure, and returning a "
            "zero-cost zero-return report would hide it."
        )
    seen = set()
    for i, a in enumerate(assets):
        if not isinstance(a.symbol, str) or not a.symbol.strip():
            raise ValueError(f"assets[{i}].symbol must be a non-empty string, got {a.symbol!r}.")
        if a.symbol in seen:
            raise ValueError(
                f"Duplicate symbol {a.symbol!r} in assets. Duplicates double-count both the "
                "weight and its transaction cost; net the positions upstream instead."
            )
        seen.add(a.symbol)
        for suffix, value in (
            ("expected_return", a.expected_return),
            ("current_weight", a.current_weight),
            ("target_weight", a.target_weight),
        ):
            _validate_finite(value, f"assets[{i}]({a.symbol}).{suffix}")
        for suffix, value in (("current_weight", a.current_weight), ("target_weight", a.target_weight)):
            if abs(value) > MAX_ABS_WEIGHT:
                raise ValueError(
                    f"assets[{i}]({a.symbol}).{suffix} = {value} exceeds |{MAX_ABS_WEIGHT}|. "
                    "Weights are fractions (0.40 = 40%), not percentages; passing 40 for 40% "
                    "inflates the quadratic impact term by four orders of magnitude."
                )


class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled


class PortfolioConstructionEngine:
    """
    Transaction-cost aware rebalance planner: applies a no-trade buffer band, then
    prices the surviving trades with proportional (commission + spread) and
    quadratic (market impact) costs.

    This class does not optimize weights -- ``target_weight`` is an input. See the
    module docstring for the cost model, the band policy, and the limitations.
    """
    def __init__(
        self,
        config: Optional[Config] = None,
        cost_spec: Optional[CostSpec] = None
    ):
        self.config = config or Config()
        self.cost_spec = cost_spec or CostSpec()
        _validate_config(self.config)
        _validate_cost_spec(self.cost_spec)

    def _resolve_final_weight(self, current: float, target: float) -> float:
        """
        Applies the no-trade band. Returns the post-rebalance weight.

        The band is inclusive: a proposed change of exactly ``rebalance_threshold``
        is SUPPRESSED. The comparison is tolerant, because subtracting two weights
        rarely lands on the threshold exactly in binary floating point -- 0.18-0.20
        is -0.020000000000000018, which a naive ``<=`` sends to the trading path.
        Boundary direction matters whenever a caller sets the threshold to the same
        value as a systematic drift, so it is pinned rather than left to float luck.
        """
        delta = target - current
        abs_delta = abs(delta)
        within_band = (
            abs_delta <= self.config.rebalance_threshold
            or math.isclose(
                abs_delta, self.config.rebalance_threshold,
                rel_tol=BAND_REL_TOL, abs_tol=BAND_ABS_TOL,
            )
        )
        if within_band:
            return current
        if self.config.trade_to_band_edge:
            # Move to the nearest band boundary, not to target (Constantinides 1986).
            # math.copysign preserves the trade direction; the band edge always lies
            # strictly between current and target here, since |delta| > threshold.
            return current + math.copysign(self.config.rebalance_threshold, delta)
        return target

    def construct_portfolio(
        self, assets: List[AssetAlphaSpec]
    ) -> TCAwarePortfolioReport:
        """
        Filters proposed trades through the no-trade band, prices them, and returns
        the executable weight vector alongside a full cost and turnover audit.

        Raises:
            TypeError: a weight or return is non-numeric.
            ValueError: empty asset list, duplicate symbol, non-finite value,
                out-of-range weight, or an invalid config/cost parameter.
        """
        if not self.config.enabled:
            return TCAwarePortfolioReport(
                total_assets=len(assets) if assets else 0,
                gross_expected_return=0.0,
                total_transaction_cost=0.0,
                net_expected_return=0.0,
                total_turnover=0.0,
                traded_symbols=[],
                suppressed_symbols=[a.symbol for a in assets] if assets else [],
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled. No rebalance plan produced; do not treat as a no-trade decision."
            )

        _validate_assets(assets)

        traded_symbols: List[str] = []
        suppressed_symbols: List[str] = []
        final_weights: Dict[str, float] = {}
        decisions: List[AssetTradeDecision] = []
        total_tc = 0.0
        total_turnover = 0.0

        spread_pct = self.cost_spec.spread_cost_bps / BPS_PER_UNIT
        proportional_rate = self.cost_spec.commission_rate + spread_pct

        for a in assets:
            proposed_delta = a.target_weight - a.current_weight
            final_weight = self._resolve_final_weight(a.current_weight, a.target_weight)
            executed_delta = final_weight - a.current_weight
            final_weights[a.symbol] = final_weight

            # Cost is charged on the delta actually EXECUTED, not the one proposed.
            # Under trade_to_band_edge these differ, and charging the proposed delta
            # would overstate cost on every banded trade.
            traded = executed_delta != 0.0
            if traded:
                traded_symbols.append(a.symbol)
                abs_delta = abs(executed_delta)
                total_turnover += abs_delta
                proportional_cost = proportional_rate * abs_delta
                impact_cost = self.cost_spec.impact_coeff * (executed_delta ** 2)
            else:
                suppressed_symbols.append(a.symbol)
                proportional_cost = 0.0
                impact_cost = 0.0

            asset_tc = proportional_cost + impact_cost
            total_tc += asset_tc
            decisions.append(AssetTradeDecision(
                symbol=a.symbol,
                current_weight=a.current_weight,
                target_weight=a.target_weight,
                final_weight=final_weight,
                proposed_delta=proposed_delta,
                executed_delta=executed_delta,
                traded=traded,
                proportional_cost=proportional_cost,
                impact_cost=impact_cost,
                total_cost=asset_tc,
            ))

        # Audit turnover against the TWO-WAY limit.
        is_turnover_exceeded = total_turnover > self.config.max_turnover_limit

        gross_return = sum(final_weights[a.symbol] * a.expected_return for a in assets)
        net_return = gross_return - total_tc

        # Budget audit: partial suppression breaks the budget identity, so the plan
        # can require unfunded cash. Report it rather than returning weights that
        # cannot be executed as-is.
        current_weight_sum = sum(a.current_weight for a in assets)
        final_weight_sum = sum(final_weights.values())
        net_weight_change = final_weight_sum - current_weight_sum
        is_self_financing = abs(net_weight_change) <= SELF_FINANCING_TOLERANCE

        status = "TURNOVER_LIMIT_EXCEEDED" if is_turnover_exceeded else "REBALANCED_COST_OPTIMIZED"
        band_mode = "band-edge" if self.config.trade_to_band_edge else "full-target"
        notes = (
            f"TC-AWARE PORTFOLIO REBALANCE [{status}]: Assets = {len(assets)}, Traded = {len(traded_symbols)}, "
            f"Suppressed (No-Trade Band) = {len(suppressed_symbols)}, Band mode = {band_mode}. "
            f"Turnover (two-way) = {total_turnover:.2%}, one-way = {total_turnover / 2.0:.2%}, "
            f"Total TC = {total_tc * BPS_PER_UNIT:.1f} bps. "
            f"Gross Return = {gross_return:.2%}, Net Return = {net_return:.2%}. "
            f"Weight sum {current_weight_sum:.4f} -> {final_weight_sum:.4f} "
            f"(net {net_weight_change:+.4f}, self-financing = {is_self_financing})."
        )

        if is_turnover_exceeded:
            # Advisory only: the plan is still returned in full. The engine does not
            # know which trades the caller would rather drop, so it refuses to pick.
            logger.warning(
                "Turnover limit breached (%.4f > %.4f, two-way). Plan returned UNCLAMPED - "
                "the caller must gate execution on turnover_limit_breached. %s",
                total_turnover, self.config.max_turnover_limit, notes,
            )
        else:
            logger.info(notes)

        if not is_self_financing:
            logger.warning(
                "Rebalance plan is not self-financing: weight sum moves %+.6f. "
                "%.4f of portfolio value must be drawn from (or returned to) cash; "
                "executing these weights without that funding leg will misallocate.",
                net_weight_change, abs(net_weight_change),
            )

        return TCAwarePortfolioReport(
            total_assets=len(assets),
            gross_expected_return=round(gross_return, 4),
            total_transaction_cost=round(total_tc, 6),
            net_expected_return=round(net_return, 4),
            total_turnover=round(total_turnover, 4),
            traded_symbols=traded_symbols,
            suppressed_symbols=suppressed_symbols,
            status=status,
            audit_notes=notes,
            final_weights=final_weights,
            trade_decisions=decisions,
            one_way_turnover=round(total_turnover / 2.0, 4),
            turnover_limit_breached=is_turnover_exceeded,
            current_weight_sum=round(current_weight_sum, 6),
            final_weight_sum=round(final_weight_sum, 6),
            net_weight_change=round(net_weight_change, 6),
            is_self_financing=is_self_financing,
        )
