"""
post-trade-execution-quality-scorecard: broker/venue execution quality scorecard
computing arrival-price slippage, VWAP slippage, effective spread, effective-over-
quoted spread (E/Q), fill rate, and a Perold implementation shortfall.

What this engine is
-------------------
A *house* post-trade scorecard for ranking brokers, algos and venues from your own
executed-order records. Two of the statistics it produces (effective spread, E/Q) are
defined the same way SEC Rule 605 defines them, so the numbers are comparable in kind
to a market centre's published Rule 605 report.

What this engine is NOT
-----------------------
It is **not** a Rule 605 report generator and produces nothing that can be filed.
Rule 605 (17 CFR 242.605), as amended by SEC Release No. 34-99679 (adopted 6 March
2024, effective 14 June 2024), is a *reporting obligation* placed on market centres,
on broker-dealers that introduce or carry 100,000 or more customer accounts, and on
single-dealer platforms. A filed report requires monthly aggregation, notional order
size categories, fractional/odd-lot/round-lot classification, price *and size*
improvement statistics, realized spreads at five post-execution horizons (50 ms, 1 s,
15 s, 1 min, 5 min), time-to-execution buckets down to sub-100-microsecond, and a
human-readable summary in CSV and PDF. None of that is implemented here. The
compliance date for the amendments was extended to 1 August 2026 by SEC Release No.
34-104147 (effective 2 October 2025).

Metric definitions
------------------
``side_sign`` is +1 for BUY and -1 for SELL, so every cost metric is signed such that
**positive = cost, negative = price improvement**, in both directions.

Arrival slippage (the filled-share implicit cost component), in bps::

    arrival_slippage_bps = side_sign * (avg_fill_price - arrival_price)
                           / arrival_price * 10_000

VWAP slippage, in bps, against the interval VWAP the caller supplies::

    vwap_slippage_bps = side_sign * (avg_fill_price - market_vwap)
                        / market_vwap * 10_000

Effective spread, per Rule 605, is twice the signed distance from the consolidated
midpoint *at the time of order receipt* -- not at the time of execution::

    effective_spread = 2 * side_sign * (avg_fill_price - arrival_midquote)
    eqr              = effective_spread / arrival_quoted_spread

Implementation shortfall follows Perold (1988, "The Implementation Shortfall: Paper
versus Reality", Journal of Portfolio Management 14(3)): the shortfall of the real
portfolio against a paper portfolio filled entirely at the decision price. It is the
sum of an execution-cost term on the shares that filled and an **opportunity-cost term
on the shares that did not**::

    filled_fraction      = executed_qty / parent_qty
    execution_cost_bps   = arrival_slippage_bps * filled_fraction
    opportunity_cost_bps = side_sign * (end_price - arrival_price) / arrival_price
                           * 10_000 * (1 - filled_fraction)
    implementation_shortfall_bps = execution_cost_bps + opportunity_cost_bps

``end_price`` -- the price at which the unfilled residual is marked, normally the last
price of the trading horizon -- is **optional and has no safe default**. Without it the
opportunity-cost term is unknowable, so ``implementation_shortfall_bps`` is reported as
``None`` rather than as the filled-share cost. Reporting the filled-share cost as
"implementation shortfall" is the single most common way a scorecard flatters a broker
that missed half the order: leaving 50% unfilled while the stock ran away costs real
money and shows up in no price-based statistic.

Aggregation
-----------
Every aggregate is **notional-weighted**, not a mean over orders. An unweighted mean
lets a 1-share order and a 1,000,000-share order carry identical weight, which is how a
broker with one excellent odd-lot fill outranks one that worked the whole programme.
Price metrics weight by executed notional; implementation shortfall weights by parent
notional at the arrival price (the paper-portfolio value it is a shortfall against).
Overall fill rate is ``sum(executed_qty) / sum(parent_qty)``, not the mean of the
per-order rates. Unweighted means are reported alongside for reference.

Rule 605's E/Q is a *ratio of share-weighted averages*, not the average of per-order
ratios; the two differ, and the difference is not small when quoted spreads vary. The
report carries the ratio-of-averages as ``eqr_ratio_of_averages`` (the Rule-605-style
figure) and the notional-weighted mean of per-order ratios as ``avg_eqr_ratio``.

Limitations (documented, deliberate)
------------------------------------
- **Points, not paths.** One arrival price, one average fill price and one interval
  VWAP per parent order. No child-order or fill-level detail, so nothing here can
  attribute cost to timing versus sizing.
- **The caller owns the benchmarks.** ``arrival_price``, ``market_vwap``,
  ``arrival_midquote`` and ``arrival_quoted_spread`` are taken as given. A midquote
  stamped at the wrong instant silently produces a wrong effective spread and the
  engine cannot detect it.
- **Commissions, fees, taxes and borrow are excluded.** These metrics are gross of
  explicit costs; a maker-rebate venue and a taker-fee venue are not comparable on
  these numbers alone.
- **No realized spread, no price/size improvement, no execution speed.** Measuring
  reversion needs post-trade marks the record does not carry.
- **The composite grade is a house heuristic, not a standard.** Its weights are
  configurable and its letter boundaries are arbitrary. No regulator defines a grade.
- **E/Q does not travel from a marketable order to a worked parent order.** Rule 605
  computes E/Q for individual marketable orders against the receipt-time quote, where a
  value near 1.0 is normal. A large parent order worked over minutes walks the book, so
  its effective spread is measured against a quote that no longer describes the
  liquidity it consumed and E/Q of 5-15 is routine in a tight-spread name. The default
  ``eqr_penalty_per_unit`` of 20.0 then drives every such order to a score of 0 and the
  scorecard stops discriminating. Recalibrate the weight (or set it to 0.0 and rank on
  slippage and fill rate) for any book dominated by worked parent orders.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Basis points per unit of relative price change.
BPS = 10_000.0

#: Sides the engine accepts. Anything else raises rather than defaulting to SELL.
VALID_SIDES = ("BUY", "SELL")

#: Composite grade boundaries (house convention -- no regulator defines these).
GRADE_BOUNDARIES: Tuple[Tuple[float, str], ...] = (
    (90.0, "A"),
    (80.0, "B"),
    (70.0, "C"),
    (60.0, "D"),
)

#: Score at or above which the audit is reported as passed (house convention).
PASS_SCORE_THRESHOLD = 70.0


@dataclass
class Config:
    """
    Scorecard configuration.

    The penalty weights below define a *house* scoring heuristic. They are exposed so a
    desk can calibrate them against its own cost model; they carry no regulatory
    authority and no external standard prescribes their values.
    """
    enabled: bool = True

    #: Arrival slippage (bps) treated as acceptable before any penalty accrues.
    #: Previously declared but never read by the engine; now wired into scoring.
    benchmark_target_is_bps: float = 10.0

    #: Score points deducted per bp of arrival slippage beyond the target.
    is_penalty_per_bps: float = 2.0

    #: Score points deducted per 1.0 of E/Q above 1.0 (i.e. per full quoted spread paid
    #: beyond the quote). Calibrated for marketable orders, where E/Q near 1.0 is
    #: normal. Worked parent orders routinely reach E/Q of 5-15 by construction, which
    #: at this weight saturates every score to 0 -- lower it, or set it to 0.0, for a
    #: book of worked parents. See the module docstring.
    eqr_penalty_per_unit: float = 20.0

    #: Score points deducted per percentage point of unfilled parent quantity.
    fill_penalty_per_pct: float = 1.0

    #: Minimum executed notional a venue needs before it is graded on its own. Below
    #: this a venue is reported but ungraded ("NR"), because a grade from two odd lots
    #: is noise presented as a measurement.
    min_venue_notional_for_grade: float = 0.0


@dataclass
class ExecutedOrderRecord:
    """
    One completed parent order.

    All prices are in the instrument's quote currency and must be finite and strictly
    positive. ``arrival_midquote`` and ``arrival_quoted_spread`` must be stamped from
    the consolidated quote **at the time of order receipt**, per Rule 605 -- not at the
    time of execution.

    ``avg_fill_price`` is ignored when ``executed_qty`` is zero: an unfilled order has
    no fill price, and treating a placeholder 0.0 as one produces a -10,000 bps
    "saving" that poisons every average it touches.
    """
    order_id: str
    venue: str
    symbol: str
    side: str                            # 'BUY' or 'SELL'
    parent_qty: float
    executed_qty: float
    avg_fill_price: float
    arrival_price: float
    market_vwap: float
    arrival_midquote: float
    arrival_quoted_spread: float
    #: Price marking the unfilled residual, normally the last price of the trading
    #: horizon. Required for Perold implementation shortfall; without it the
    #: opportunity-cost term -- and therefore IS -- is reported as None.
    end_price: Optional[float] = None


@dataclass
class SingleOrderMetrics:
    """
    Per-order metrics. Price-based fields are ``None`` for a wholly unfilled order,
    which has no execution to measure.
    """
    order_id: str
    venue: str
    symbol: str
    #: Full Perold IS in bps over the parent notional (execution + opportunity cost).
    #: None when ``end_price`` was not supplied.
    implementation_shortfall_bps: Optional[float]
    vwap_slippage_bps: Optional[float]
    effective_spread: Optional[float]
    eqr_ratio: Optional[float]
    fill_rate_pct: float
    score_points: float                  # 0 to 100
    #: Filled-share arrival-price slippage in bps. This is the implicit cost component
    #: only -- it is NOT implementation shortfall, and it is what scoring uses.
    arrival_slippage_bps: Optional[float] = None
    #: Opportunity cost of the unfilled residual, in bps of parent notional.
    opportunity_cost_bps: Optional[float] = None
    executed_notional: float = 0.0
    parent_notional: float = 0.0
    parent_qty: float = 0.0
    executed_qty: float = 0.0
    is_fully_filled: bool = False


@dataclass
class VenueScorecard:
    """Notional-weighted rollup for one venue."""
    venue: str
    orders: int
    executed_notional: float
    avg_arrival_slippage_bps: Optional[float]
    avg_vwap_slippage_bps: Optional[float]
    eqr_ratio_of_averages: Optional[float]
    fill_rate_pct: float
    score_points: float
    rating: str                          # 'A'-'F', or 'NR' when below the size floor


@dataclass
class ExecutionQualityScorecardReport:
    total_orders_audited: int
    avg_implementation_shortfall_bps: Optional[float]
    avg_vwap_slippage_bps: Optional[float]
    avg_eqr_ratio: Optional[float]
    overall_fill_rate_pct: float
    composite_scorecard_rating: str      # 'A', 'B', 'C', 'D', 'F', or 'N/A'
    order_metrics: List[SingleOrderMetrics]
    status: str
    audit_notes: str
    #: Notional-weighted filled-share arrival slippage. Always available when at least
    #: one order filled, unlike IS which needs ``end_price``.
    avg_arrival_slippage_bps: Optional[float] = None
    #: Rule-605-style E/Q: share-weighted average effective spread divided by
    #: share-weighted average quoted spread. Rule 605 expresses this as a percentage;
    #: it is reported here as a ratio (multiply by 100 to compare with a filing).
    eqr_ratio_of_averages: Optional[float] = None
    #: Unweighted mean over orders, for reference only. Not the headline number.
    unweighted_avg_arrival_slippage_bps: Optional[float] = None
    #: True when every order carried an ``end_price``, so IS is complete for all.
    implementation_shortfall_complete: bool = False
    orders_missing_end_price: int = 0
    unfilled_orders: int = 0
    total_parent_notional: float = 0.0
    total_executed_notional: float = 0.0
    venue_scorecards: List[VenueScorecard] = field(default_factory=list)


class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


def _require_positive_price(value: float, name: str, order_id: str) -> float:
    """
    Prices must be finite and strictly positive.

    Guarding a denominator with ``max(0.0001, price)`` does not make a bad price safe;
    it converts a zero price into a ~1,000,000,000 bps slippage figure that flows into
    the aggregate as if it were a measurement. Bad reference data must fail loudly.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Order '{order_id}': {name} must be numeric, got {type(value).__name__}."
        )
    if not math.isfinite(value):
        raise ValueError(f"Order '{order_id}': {name} must be finite, got {value!r}.")
    if value <= 0.0:
        raise ValueError(f"Order '{order_id}': {name} must be > 0, got {value!r}.")
    return float(value)


def _require_finite(value: float, name: str, order_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"Order '{order_id}': {name} must be numeric, got {type(value).__name__}."
        )
    if not math.isfinite(value):
        raise ValueError(f"Order '{order_id}': {name} must be finite, got {value!r}.")
    return float(value)


def _validate_order(order: ExecutedOrderRecord) -> Tuple[str, float]:
    """
    Validates one record and returns ``(normalised_side, side_sign)``.

    Raises rather than coercing. An unrecognised side previously fell through to
    ``side_sign = -1.0``, silently scoring every typo'd BUY as a SELL and inverting the
    sign of its cost -- a broker that paid 50 bps would be reported as saving 50.
    """
    if not order.order_id:
        raise ValueError("Every order requires a non-empty order_id.")

    side = str(order.side).strip().upper()
    if side not in VALID_SIDES:
        raise ValueError(
            f"Order '{order.order_id}': side must be one of {VALID_SIDES}, "
            f"got {order.side!r}."
        )
    side_sign = 1.0 if side == "BUY" else -1.0

    for name in ("arrival_price", "market_vwap", "arrival_midquote"):
        _require_positive_price(getattr(order, name), name, order.order_id)

    spread = _require_finite(
        order.arrival_quoted_spread, "arrival_quoted_spread", order.order_id
    )
    if spread <= 0.0:
        raise ValueError(
            f"Order '{order.order_id}': arrival_quoted_spread must be > 0, got {spread!r}. "
            f"A locked or crossed book has no meaningful E/Q denominator -- exclude the "
            f"order instead of flooring the divisor."
        )

    parent_qty = _require_finite(order.parent_qty, "parent_qty", order.order_id)
    if parent_qty <= 0.0:
        raise ValueError(
            f"Order '{order.order_id}': parent_qty must be > 0, got {parent_qty!r}."
        )
    executed_qty = _require_finite(order.executed_qty, "executed_qty", order.order_id)
    if executed_qty < 0.0:
        raise ValueError(
            f"Order '{order.order_id}': executed_qty must be >= 0, got {executed_qty!r}."
        )
    if executed_qty > parent_qty:
        raise ValueError(
            f"Order '{order.order_id}': executed_qty ({executed_qty}) exceeds parent_qty "
            f"({parent_qty}); an over-fill is a reconciliation break, not a >100% fill rate."
        )

    if executed_qty > 0.0:
        _require_positive_price(order.avg_fill_price, "avg_fill_price", order.order_id)
    if order.end_price is not None:
        _require_positive_price(order.end_price, "end_price", order.order_id)

    return side, side_sign


def _weighted_mean(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Notional-weighted mean of ``(value, weight)``; None when total weight is zero."""
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0.0:
        return None
    return sum(value * weight for value, weight in pairs) / total_weight


def _grade(score: float) -> str:
    for threshold, letter in GRADE_BOUNDARIES:
        if score >= threshold:
            return letter
    return "F"


class PostTradeExecutionQualityScorecard:
    """
    Post-trade execution quality scorecard: arrival slippage, VWAP slippage, effective
    spread, effective-over-quoted ratio, fill rate, Perold implementation shortfall, and
    a notional-weighted composite grade per venue and overall.

    See the module docstring for metric definitions, the aggregation rule, and an
    explicit statement of what this engine does *not* do (it does not produce a filable
    SEC Rule 605 report).
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def _score_order(
        self,
        arrival_slippage_bps: Optional[float],
        eqr: Optional[float],
        fill_rate_pct: float,
    ) -> float:
        """
        House scoring heuristic, clamped to [0, 100]::

            100
              - max(0, arrival_slippage_bps - benchmark_target_is_bps) * is_penalty_per_bps
              - max(0, eqr - 1.0) * eqr_penalty_per_unit
              - (100 - fill_rate_pct) * fill_penalty_per_pct

        Scoring deliberately uses arrival slippage rather than implementation shortfall,
        so a score exists for every order whether or not ``end_price`` was supplied. The
        unfilled residual is already penalised through the fill term; charging it again
        through IS would double-count it.

        A wholly unfilled order scores on the fill term alone -- it has no price outcome
        to reward, and crediting one would reward not trading.
        """
        cfg = self.config
        score = 100.0
        if arrival_slippage_bps is not None:
            excess = max(0.0, arrival_slippage_bps - cfg.benchmark_target_is_bps)
            score -= excess * cfg.is_penalty_per_bps
        if eqr is not None:
            score -= max(0.0, eqr - 1.0) * cfg.eqr_penalty_per_unit
        score -= max(0.0, 100.0 - fill_rate_pct) * cfg.fill_penalty_per_pct
        return max(0.0, min(100.0, score))

    def _empty_report(
        self, total: int, status: str, notes: str
    ) -> ExecutionQualityScorecardReport:
        return ExecutionQualityScorecardReport(
            total_orders_audited=total,
            avg_implementation_shortfall_bps=None,
            avg_vwap_slippage_bps=None,
            avg_eqr_ratio=None,
            overall_fill_rate_pct=0.0,
            composite_scorecard_rating="N/A",
            order_metrics=[],
            status=status,
            audit_notes=notes,
        )

    def evaluate_scorecard(
        self, orders: List[ExecutedOrderRecord]
    ) -> ExecutionQualityScorecardReport:
        """
        Computes per-order metrics, per-venue rollups and a notional-weighted composite
        grade.

        Raises:
            ValueError / TypeError: on any invalid record. Validation runs over the whole
                batch before any metric is computed, so a malformed record can never
                contribute a partial result to an aggregate.
        """
        if not self.config.enabled:
            return self._empty_report(len(orders), "ENGINE_DISABLED", "Engine is disabled.")

        if not orders:
            return self._empty_report(
                0, "NO_ORDERS_AUDITED", "No orders submitted for scorecard audit."
            )

        validated = [(order, *_validate_order(order)) for order in orders]

        order_metrics_list: List[SingleOrderMetrics] = []
        # (value, weight) pairs. Price metrics weight by executed notional; shortfall
        # and score weight by parent notional.
        slip_pairs: List[Tuple[float, float]] = []
        vwap_pairs: List[Tuple[float, float]] = []
        eqr_pairs: List[Tuple[float, float]] = []
        is_pairs: List[Tuple[float, float]] = []
        score_pairs: List[Tuple[float, float]] = []
        eff_spread_pairs: List[Tuple[float, float]] = []
        quoted_spread_pairs: List[Tuple[float, float]] = []
        unweighted_slippage: List[float] = []

        total_parent_qty = 0.0
        total_executed_qty = 0.0
        total_parent_notional = 0.0
        total_executed_notional = 0.0
        missing_end_price = 0
        unfilled_orders = 0
        venue_rows: Dict[str, List[SingleOrderMetrics]] = {}

        for order, _side, side_sign in validated:
            filled_fraction = order.executed_qty / order.parent_qty
            fill_rate = filled_fraction * 100.0
            parent_notional = order.parent_qty * order.arrival_price
            executed_notional = (
                order.executed_qty * order.avg_fill_price if order.executed_qty > 0.0 else 0.0
            )

            arrival_slippage_bps: Optional[float] = None
            vwap_bps: Optional[float] = None
            eff_spread: Optional[float] = None
            eqr: Optional[float] = None

            if order.executed_qty > 0.0:
                arrival_slippage_bps = (
                    side_sign
                    * (order.avg_fill_price - order.arrival_price)
                    / order.arrival_price
                    * BPS
                )
                vwap_bps = (
                    side_sign
                    * (order.avg_fill_price - order.market_vwap)
                    / order.market_vwap
                    * BPS
                )
                eff_spread = 2.0 * side_sign * (order.avg_fill_price - order.arrival_midquote)
                eqr = eff_spread / order.arrival_quoted_spread
            else:
                unfilled_orders += 1

            # Perold IS: execution cost on the filled shares + opportunity cost on the rest.
            opportunity_cost_bps: Optional[float] = None
            is_bps: Optional[float] = None
            if order.end_price is None:
                missing_end_price += 1
            else:
                opportunity_cost_bps = (
                    side_sign
                    * (order.end_price - order.arrival_price)
                    / order.arrival_price
                    * BPS
                    * (1.0 - filled_fraction)
                )
                execution_cost_bps = (arrival_slippage_bps or 0.0) * filled_fraction
                is_bps = execution_cost_bps + opportunity_cost_bps

            score = self._score_order(arrival_slippage_bps, eqr, fill_rate)

            total_parent_qty += order.parent_qty
            total_executed_qty += order.executed_qty
            total_parent_notional += parent_notional
            total_executed_notional += executed_notional

            if arrival_slippage_bps is not None:
                slip_pairs.append((arrival_slippage_bps, executed_notional))
                unweighted_slippage.append(arrival_slippage_bps)
            if vwap_bps is not None:
                vwap_pairs.append((vwap_bps, executed_notional))
            if eqr is not None:
                eqr_pairs.append((eqr, executed_notional))
            if eff_spread is not None:
                # Rule 605 share-weights its spread averages, and the E/Q it publishes is
                # the ratio of those two averages -- not the average of per-order ratios.
                eff_spread_pairs.append((eff_spread, order.executed_qty))
                quoted_spread_pairs.append((order.arrival_quoted_spread, order.executed_qty))
            if is_bps is not None:
                is_pairs.append((is_bps, parent_notional))
            score_pairs.append((score, parent_notional))

            metrics = SingleOrderMetrics(
                order_id=order.order_id,
                venue=order.venue,
                symbol=order.symbol,
                implementation_shortfall_bps=None if is_bps is None else round(is_bps, 2),
                vwap_slippage_bps=None if vwap_bps is None else round(vwap_bps, 2),
                effective_spread=None if eff_spread is None else round(eff_spread, 4),
                eqr_ratio=None if eqr is None else round(eqr, 4),
                fill_rate_pct=round(fill_rate, 2),
                score_points=round(score, 1),
                arrival_slippage_bps=(
                    None if arrival_slippage_bps is None else round(arrival_slippage_bps, 2)
                ),
                opportunity_cost_bps=(
                    None if opportunity_cost_bps is None else round(opportunity_cost_bps, 2)
                ),
                executed_notional=round(executed_notional, 2),
                parent_notional=round(parent_notional, 2),
                parent_qty=order.parent_qty,
                executed_qty=order.executed_qty,
                is_fully_filled=math.isclose(
                    order.executed_qty, order.parent_qty, rel_tol=1e-9
                ),
            )
            order_metrics_list.append(metrics)
            venue_rows.setdefault(order.venue, []).append(metrics)

        n = len(orders)
        avg_slip = _weighted_mean(slip_pairs)
        avg_vwap = _weighted_mean(vwap_pairs)
        avg_eqr = _weighted_mean(eqr_pairs)
        avg_is = _weighted_mean(is_pairs)
        avg_score = _weighted_mean(score_pairs)
        if avg_score is None:                    # defensive: every parent notional zero
            avg_score = sum(score for score, _ in score_pairs) / len(score_pairs)

        avg_eff = _weighted_mean(eff_spread_pairs)
        avg_quoted = _weighted_mean(quoted_spread_pairs)
        eqr_of_averages = (
            None if (avg_eff is None or not avg_quoted) else avg_eff / avg_quoted
        )

        overall_fill = (total_executed_qty / total_parent_qty) * 100.0
        rating = _grade(avg_score)
        status = (
            "SCORECARD_AUDIT_PASSED"
            if avg_score >= PASS_SCORE_THRESHOLD
            else "SCORECARD_AUDIT_FAILED"
        )

        venue_scorecards = self._build_venue_scorecards(venue_rows)

        def _fmt(value: Optional[float], suffix: str = "", signed: bool = False) -> str:
            if value is None:
                return "n/a"
            return f"{value:+.2f}{suffix}" if signed else f"{value:.2f}{suffix}"

        notes = (
            f"EXECUTION QUALITY SCORECARD AUDIT [{status} - RATING: {rating} "
            f"({avg_score:.1f}/100)]: Orders = {n}, Notional-weighted arrival slippage = "
            f"{_fmt(avg_slip, ' bps', signed=True)}, IS = {_fmt(avg_is, ' bps', signed=True)}, "
            f"VWAP slippage = {_fmt(avg_vwap, ' bps', signed=True)}, "
            f"E/Q (ratio of averages) = {_fmt(eqr_of_averages)}, "
            f"Overall fill rate = {overall_fill:.1f}%."
        )
        if missing_end_price:
            notes += (
                f" WARNING: {missing_end_price}/{n} order(s) lack end_price, so their "
                f"opportunity cost and implementation shortfall are unmeasured; the IS "
                f"figure above covers only the orders that supplied one."
            )
        if unfilled_orders:
            notes += (
                f" NOTE: {unfilled_orders} order(s) went wholly unfilled and contribute to "
                f"fill rate and scoring but to no price-based metric."
            )

        logger.info(notes)

        return ExecutionQualityScorecardReport(
            total_orders_audited=n,
            avg_implementation_shortfall_bps=None if avg_is is None else round(avg_is, 2),
            avg_vwap_slippage_bps=None if avg_vwap is None else round(avg_vwap, 2),
            avg_eqr_ratio=None if avg_eqr is None else round(avg_eqr, 4),
            overall_fill_rate_pct=round(overall_fill, 2),
            composite_scorecard_rating=rating,
            order_metrics=order_metrics_list,
            status=status,
            audit_notes=notes,
            avg_arrival_slippage_bps=None if avg_slip is None else round(avg_slip, 2),
            eqr_ratio_of_averages=(
                None if eqr_of_averages is None else round(eqr_of_averages, 4)
            ),
            unweighted_avg_arrival_slippage_bps=(
                None
                if not unweighted_slippage
                else round(sum(unweighted_slippage) / len(unweighted_slippage), 2)
            ),
            implementation_shortfall_complete=(missing_end_price == 0),
            orders_missing_end_price=missing_end_price,
            unfilled_orders=unfilled_orders,
            total_parent_notional=round(total_parent_notional, 2),
            total_executed_notional=round(total_executed_notional, 2),
            venue_scorecards=venue_scorecards,
        )

    def _build_venue_scorecards(
        self, venue_rows: Dict[str, List[SingleOrderMetrics]]
    ) -> List[VenueScorecard]:
        """
        One notional-weighted rollup per venue, sorted worst score first so the venue
        needing attention is the first row a reviewer reads.

        A venue whose executed notional falls below ``min_venue_notional_for_grade`` is
        reported but graded ``NR``: a letter grade derived from two odd lots is noise
        wearing the costume of a measurement, and desks route on letter grades.
        """
        cards: List[VenueScorecard] = []
        for venue, rows in venue_rows.items():
            executed_notional = sum(row.executed_notional for row in rows)
            parent_notional = sum(row.parent_notional for row in rows)

            slippage = _weighted_mean(
                [
                    (row.arrival_slippage_bps, row.executed_notional)
                    for row in rows
                    if row.arrival_slippage_bps is not None
                ]
            )
            vwap = _weighted_mean(
                [
                    (row.vwap_slippage_bps, row.executed_notional)
                    for row in rows
                    if row.vwap_slippage_bps is not None
                ]
            )
            effective = _weighted_mean(
                [
                    (row.effective_spread, row.executed_notional)
                    for row in rows
                    if row.effective_spread is not None
                ]
            )
            # Recovering the quoted spread as effective/eqr keeps the rollup on the same
            # ratio-of-averages basis as Rule 605 without re-reading the raw records.
            quoted = _weighted_mean(
                [
                    (row.effective_spread / row.eqr_ratio, row.executed_notional)
                    for row in rows
                    if row.eqr_ratio not in (None, 0.0) and row.effective_spread is not None
                ]
            )
            score = _weighted_mean([(row.score_points, row.parent_notional) for row in rows])
            if score is None:
                score = sum(row.score_points for row in rows) / len(rows)

            # Fill rate is a quantity ratio, not a notional one: executed notional is
            # struck at the fill price and parent notional at the arrival price, so
            # dividing one by the other would fold price drift into the fill statistic.
            venue_parent_qty = sum(row.parent_qty for row in rows)
            venue_executed_qty = sum(row.executed_qty for row in rows)
            venue_fill_rate = (
                (venue_executed_qty / venue_parent_qty * 100.0) if venue_parent_qty > 0.0 else 0.0
            )
            rating = (
                _grade(score)
                if executed_notional >= self.config.min_venue_notional_for_grade
                else "NR"
            )
            cards.append(
                VenueScorecard(
                    venue=venue,
                    orders=len(rows),
                    executed_notional=round(executed_notional, 2),
                    avg_arrival_slippage_bps=None if slippage is None else round(slippage, 2),
                    avg_vwap_slippage_bps=None if vwap is None else round(vwap, 2),
                    eqr_ratio_of_averages=(
                        None if (effective is None or not quoted) else round(effective / quoted, 4)
                    ),
                    fill_rate_pct=round(venue_fill_rate, 2),
                    score_points=round(score, 1),
                    rating=rating,
                )
            )
        cards.sort(key=lambda card: (card.score_points, card.venue))
        return cards
