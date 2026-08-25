"""
execution-slippage-attribution-timing-vs-sizing: post-trade decomposition of the
*executed* leg of Implementation Shortfall (IS) into a timing/delay component and a
sizing/market-impact component, so a desk can tell which half of the execution stack
to fix.

Cost convention
---------------
All figures are **cost-signed basis points relative to the decision price**: a
positive number is money lost, a negative number is money gained. ``side_sign`` is
+1 for a BUY and -1 for a SELL, so a buy filled above the decision price and a sell
filled below it both report a positive cost.

    total_is_bps  = side_sign * (P_exec - P_decision) / P_decision * 10_000
    timing_bps    = side_sign * (P_arrival - P_decision) / P_decision * 10_000
    sizing_bps    = side_sign * (P_exec - P_arrival) / P_decision * 10_000

Every term is divided by ``P_decision`` -- not by ``P_arrival`` -- which is what makes
the decomposition additive:

    total_is_bps == timing_bps + sizing_bps      (algebraically exact)

The identity is verified in full precision on every call
(``_assert_decomposition_identity``) rather than being asserted in a comment. After
rounding for reporting the two sides can differ by up to one 0.01 bps ulp; the
reported ``total_is_slippage_bps`` is the directly computed total, never the sum of
the rounded parts.

Scope: what this engine does and does not measure
-------------------------------------------------
Perold (1988) defines Implementation Shortfall as the return difference between the
paper portfolio and the implemented portfolio. In the standard expanded form the
shortfall has four parts:

    IS = delay cost + trading cost + opportunity cost + explicit fees

This engine measures the first two -- the price-based cost incurred on shares that
actually filled. It does **not** measure:

- **Opportunity cost** on the unexecuted quantity, ``(Q_order - Q_filled) *
  (P_end_of_horizon - P_decision)``. That term needs an end-of-horizon price this
  engine is never given, and on a badly underfilled order it can dominate everything
  reported here. Use ``implementation-shortfall-minimization`` for the full
  four-component shortfall.
- **Explicit fees** -- commissions, exchange fees, taxes, stamp duty.

So ``total_is_slippage_bps`` is the *executed-leg* shortfall, not the whole
shortfall. Treat it as the complete cost of an order only when ``is_partial_fill``
is False and explicit fees are accounted for elsewhere.

Basis-point denominators
------------------------
Two different denominators are reported, because they answer different questions:

- ``total_is_slippage_bps`` / ``timing_delay_slippage_bps`` /
  ``sizing_impact_slippage_bps`` are **per executed share**, relative to the decision
  price. This is what a trader compares against a per-share cost benchmark.
- ``executed_is_contribution_bps`` weights that per-share cost by the fill ratio, so
  it is expressed on the **intended notional** ``order_qty * decision_price`` -- the
  denominator the canonical IS calculation uses. On a full fill the two coincide;
  on a 40%-filled order the per-share number overstates the contribution to IS by
  2.5x, which is exactly the mistake this field exists to prevent.

References
----------
- Perold, A. F. (1988). "The Implementation Shortfall: Paper vs. Reality."
  *Journal of Portfolio Management* 14(3), Spring 1988, pp. 4-9.
  doi:10.3905/jpm.1988.409150 -- the original IS definition (paper vs implemented
  portfolio) and the opportunity-cost argument.
- The delay-cost / trading-cost split used here (decision -> order release ->
  execution, each weighted by executed shares, normalised on total shares x decision
  price) follows the standard expanded IS formulation taught in the CFA Level III
  trade-cost material.

Deliberate limitations
----------------------
- **Attribution, not causation.** The timing component is whatever the price did
  between decision and arrival. On a liquid name over a short delay that is mostly
  market drift and news, not a latency defect. A large timing number justifies
  *investigating* the dispatch path; it does not prove the dispatch path is slow.
  Correlate with ``delay_seconds`` before acting.
- **No market/beta adjustment.** Neither component is decontaminated of index
  movement, so a timing figure measured during a broad market move is partly beta.
- **Single parent order.** No aggregation across child orders, venues or days.
- **Average execution price only.** The caller supplies one quantity-weighted
  average fill price; per-fill trajectory and intraday reversion are out of scope.
- **The recommendation is a triage hint, not a control action.** It names the
  larger *adverse* component. It is not wired to any risk control and must not be
  auto-applied to live algo parameters without human review.
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "TradeExecutionSummary",
    "SlippageAttributionAuditReport",
    "ExecutionSlippageAttributionEngine",
    "DEFAULT_MATERIALITY_THRESHOLD_BPS",
    "VALID_SIDES",
]

#: Sides this engine accepts. Anything else is a data error and is rejected rather
#: than coerced: silently treating an unrecognised side as SELL flips ``side_sign``
#: and reports a cost as a gain of equal size.
VALID_SIDES = ("BUY", "SELL")

#: Default reporting threshold, in bps, below which a component is treated as noise
#: rather than a driver. This is a desk reporting convention, not a standard or a
#: regulatory figure -- 1 bp is roughly a single tick on a $100 name. Set it from
#: your own cost distribution via ``ExecutionSlippageAttributionEngine(...)``.
DEFAULT_MATERIALITY_THRESHOLD_BPS = 1.0

#: Relative tolerance for the additive-identity check. The identity is algebraically
#: exact; this only absorbs floating-point representation error.
_IDENTITY_RELATIVE_TOLERANCE = 1e-6


@dataclass
class TradeExecutionSummary:
    trade_id: str
    symbol: str
    side: str                           # 'BUY' or 'SELL' (case-insensitive; nothing else accepted)
    order_qty: int                      # Parent/intended quantity -- the IS notional denominator
    decision_price: float               # Price when PM made trading decision
    arrival_price: float                # Price when order reached broker/exchange
    average_exec_price: float           # Quantity-weighted average fill price achieved
    decision_time_iso: str              # ISO-8601, MUST be timezone-aware
    arrival_time_iso: str               # ISO-8601, MUST be timezone-aware
    completion_time_iso: str            # ISO-8601, MUST be timezone-aware
    filled_qty: Optional[int] = None    # Executed quantity; defaults to order_qty (full fill)


@dataclass
class SlippageAttributionAuditReport:
    trade_id: str
    symbol: str
    side: str
    decision_price: float
    arrival_price: float
    average_exec_price: float
    total_is_slippage_bps: float        # Executed-leg IS, per executed share
    timing_delay_slippage_bps: float    # Decision -> Arrival, per executed share
    sizing_impact_slippage_bps: float   # Arrival -> Completion, per executed share
    timing_contribution_pct: float      # Share of gross attributed cost (|timing| + |sizing|)
    sizing_contribution_pct: float      # Share of gross attributed cost (|timing| + |sizing|)
    primary_slippage_driver: str        # See ExecutionSlippageAttributionEngine docstring
    strategy_action_recommendation: str  # Triage hint -- never auto-applied
    audit_notes: str
    fill_ratio: float = 1.0             # filled_qty / order_qty
    is_partial_fill: bool = False       # True => opportunity cost is missing from this report
    executed_is_contribution_bps: float = 0.0   # Executed-leg cost on the INTENDED notional
    delay_seconds: float = 0.0          # Decision -> arrival, measured from the timestamps
    execution_duration_seconds: float = 0.0     # Arrival -> completion
    secondary_driver_material: bool = False     # The non-primary component is ALSO adverse
    materiality_threshold_bps: float = DEFAULT_MATERIALITY_THRESHOLD_BPS


def _require_positive_price(value: float, field_name: str) -> float:
    """
    Rejects non-finite and non-positive prices.

    A NaN price previously propagated straight through: every bps figure became NaN,
    both ``abs(nan) > abs(nan)`` comparisons evaluated False, and the trade fell into
    the final branch and was reported as ZERO_SLIPPAGE / OPTIMAL. Corrupt input
    produced a clean bill of health, which is the worst possible failure mode for a
    TCA engine. Prices must fail loudly instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite, got {value!r}.")
    if numeric <= 0.0:
        raise ValueError(f"{field_name} must be > 0, got {numeric}.")
    return numeric


def _normalise_side(side: str) -> Tuple[str, float]:
    """
    Maps a side string to its canonical form and cost sign.

    Anything outside ``VALID_SIDES`` raises. The previous ``+1 if side == 'BUY' else
    -1`` treated 'BUYY', 'B', 'LONG' and '' as SELL, so a single typo reported a
    +70 bps cost as a -70 bps gain -- the exact sign-convention failure this skill's
    own pitfalls warn about.
    """
    if not isinstance(side, str):
        raise TypeError(f"side must be a string, got {type(side).__name__}.")
    canonical = side.strip().upper()
    if canonical not in VALID_SIDES:
        raise ValueError(f"side must be one of {VALID_SIDES}, got {side!r}.")
    return canonical, (1.0 if canonical == "BUY" else -1.0)


def _parse_iso_timestamp(value: str, field_name: str) -> datetime:
    """
    Parses an ISO-8601 timestamp and requires it to be timezone-aware.

    Naive timestamps are rejected rather than assumed-UTC: a delay measured across a
    DST transition or between two venues in different zones is silently wrong, and a
    wrong ``delay_seconds`` is what makes an ACCELERATE_ORDER_DISPATCH
    recommendation unfalsifiable. A trailing 'Z' is accepted and normalised.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 string, got {value!r}.")
    raw = value.strip()
    normalised = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO-8601 timestamp: {value!r}.") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"{field_name} must be timezone-aware (e.g. '2026-07-30T10:00:00Z'), got {value!r}."
        )
    return parsed


def _assert_decomposition_identity(total_bps: float, timing_bps: float, sizing_bps: float) -> None:
    """
    Verifies ``total == timing + sizing`` in full precision.

    ``references/standards.md`` mandates this identity. The previous implementation
    stated it in a comment and then *substituted* the sum for the total, which
    guaranteed the printed numbers agreed while checking nothing.
    """
    residual = abs(total_bps - (timing_bps + sizing_bps))
    tolerance = _IDENTITY_RELATIVE_TOLERANCE * max(1.0, abs(timing_bps) + abs(sizing_bps))
    if residual > tolerance:
        raise ArithmeticError(
            f"IS decomposition identity violated: total={total_bps!r} vs "
            f"timing+sizing={timing_bps + sizing_bps!r} (residual {residual!r} > {tolerance!r})."
        )


class ExecutionSlippageAttributionEngine:
    """
    Decomposes the executed leg of Implementation Shortfall into timing/delay and
    sizing/market-impact components and names the larger *adverse* component.

    ``primary_slippage_driver`` values:

    ``TIMING_DRIVEN_SLIPPAGE``
        The decision-to-arrival leg is the larger materially adverse component.
        Recommendation: ``ACCELERATE_ORDER_DISPATCH``.
    ``SIZING_DRIVEN_SLIPPAGE``
        The arrival-to-completion leg is the larger materially adverse component.
        Recommendation: ``REDUCE_PARTICIPATION_RATE_CEILING``.
    ``BOTH_DRIVERS_MATERIAL``
        Both legs are materially adverse and neither leads by more than the
        materiality threshold. Recommendation: ``REDUCE_DELAY_AND_PARTICIPATION``.
    ``FAVORABLE_EXECUTION``
        No materially adverse component and the total is materially favourable.
        Recommendation: ``NO_ACTION_COST_FAVORABLE``.
    ``ZERO_SLIPPAGE``
        Nothing material in either direction. Recommendation: ``OPTIMAL``.

    Ranking uses the **cost-signed** component, not its absolute value. Ranking by
    ``abs()`` let a favourable leg win: a -50 bps timing gain beside a +20 bps sizing
    cost was labelled TIMING_DRIVEN_SLIPPAGE and recommended ACCELERATE_ORDER_DISPATCH
    -- advice that would have forfeited the gain and left the only real cost untouched.
    A component that made money is never a slippage driver.
    """

    def __init__(self, materiality_threshold_bps: float = DEFAULT_MATERIALITY_THRESHOLD_BPS) -> None:
        """
        :param materiality_threshold_bps: components with an adverse cost at or below
            this many bps are treated as noise, and it doubles as the tie band when
            ranking two adverse components. Must be finite and >= 0. This is a desk
            reporting convention, not a standard -- see ``DEFAULT_MATERIALITY_THRESHOLD_BPS``.
        """
        if isinstance(materiality_threshold_bps, bool) or not isinstance(
            materiality_threshold_bps, (int, float)
        ):
            raise TypeError("materiality_threshold_bps must be a number.")
        threshold = float(materiality_threshold_bps)
        if not math.isfinite(threshold) or threshold < 0.0:
            raise ValueError(
                f"materiality_threshold_bps must be finite and >= 0, "
                f"got {materiality_threshold_bps!r}."
            )
        self.materiality_threshold_bps = threshold

    def attribute_execution_slippage(
        self, trade: TradeExecutionSummary
    ) -> SlippageAttributionAuditReport:
        """
        Decomposes the executed leg of Implementation Shortfall into timing vs sizing
        components and returns a structured audit report.

        Raises ``ValueError``/``TypeError`` on invalid prices, sides, quantities or
        timestamps. It never returns a "no action needed" verdict derived from data
        it could not validate.
        """
        decision_price = _require_positive_price(trade.decision_price, "decision_price")
        arrival_price = _require_positive_price(trade.arrival_price, "arrival_price")
        average_exec_price = _require_positive_price(trade.average_exec_price, "average_exec_price")

        canonical_side, side_sign = _normalise_side(trade.side)
        fill_ratio, filled_qty, order_qty = self._resolve_fill(trade)
        delay_seconds, execution_seconds = self._resolve_durations(trade)

        # --- Decomposition, full precision, all terms normalised on the decision price ---
        total_is_exact = side_sign * ((average_exec_price - decision_price) / decision_price) * 10000.0
        timing_exact = side_sign * ((arrival_price - decision_price) / decision_price) * 10000.0
        sizing_exact = side_sign * ((average_exec_price - arrival_price) / decision_price) * 10000.0
        _assert_decomposition_identity(total_is_exact, timing_exact, sizing_exact)

        total_is_bps = round(total_is_exact, 2)
        timing_is_bps = round(timing_exact, 2)
        sizing_is_bps = round(sizing_exact, 2)

        # --- Contribution shares, normalised on GROSS attributed cost ---------------
        # Dividing by abs(total) let offsetting components explode: +500 bps timing
        # against -499 bps sizing gave 50000% / -49900%. Normalising on
        # |timing| + |sizing| keeps every share inside [-100, 100] and reproduces the
        # familiar 71.4 / 28.6 split when both components share a sign.
        gross_cost_bps = abs(timing_is_bps) + abs(sizing_is_bps)
        if gross_cost_bps > 0.0:
            timing_pct = round((timing_is_bps / gross_cost_bps) * 100.0, 1)
            sizing_pct = round((sizing_is_bps / gross_cost_bps) * 100.0, 1)
        else:
            timing_pct = 0.0
            sizing_pct = 0.0

        executed_is_contribution_bps = round(total_is_exact * fill_ratio, 2)

        driver, recommendation, secondary_material = self._classify_driver(
            timing_is_bps, sizing_is_bps, total_is_bps
        )
        notes = self._build_audit_notes(
            trade=trade,
            driver=driver,
            total_is_bps=total_is_bps,
            timing_is_bps=timing_is_bps,
            sizing_is_bps=sizing_is_bps,
            timing_pct=timing_pct,
            sizing_pct=sizing_pct,
            delay_seconds=delay_seconds,
            execution_seconds=execution_seconds,
            fill_ratio=fill_ratio,
            filled_qty=filled_qty,
            order_qty=order_qty,
            secondary_material=secondary_material,
        )

        # Routine attribution is an INFO-level analytic result; a batch TCA run over
        # 10,000 trades must not emit 10,000 warnings. WARNING is reserved for a
        # materially adverse total.
        if total_is_bps > self.materiality_threshold_bps:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SlippageAttributionAuditReport(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            side=canonical_side,
            decision_price=decision_price,
            arrival_price=arrival_price,
            average_exec_price=average_exec_price,
            total_is_slippage_bps=total_is_bps,
            timing_delay_slippage_bps=timing_is_bps,
            sizing_impact_slippage_bps=sizing_is_bps,
            timing_contribution_pct=timing_pct,
            sizing_contribution_pct=sizing_pct,
            primary_slippage_driver=driver,
            strategy_action_recommendation=recommendation,
            audit_notes=notes,
            fill_ratio=round(fill_ratio, 6),
            is_partial_fill=filled_qty < order_qty,
            executed_is_contribution_bps=executed_is_contribution_bps,
            delay_seconds=delay_seconds,
            execution_duration_seconds=execution_seconds,
            secondary_driver_material=secondary_material,
            materiality_threshold_bps=self.materiality_threshold_bps,
        )

    # ------------------------------------------------------------------ helpers ---

    def _resolve_fill(self, trade: TradeExecutionSummary) -> Tuple[float, int, int]:
        """
        Validates quantities and returns ``(fill_ratio, filled_qty, order_qty)``.

        ``order_qty`` was previously carried on the input and never read, so a
        partially filled order was reported as though the per-share cost were the
        whole story -- silently omitting the opportunity cost on the unfilled residual.
        """
        if isinstance(trade.order_qty, bool) or not isinstance(trade.order_qty, int):
            raise TypeError(f"order_qty must be an int, got {type(trade.order_qty).__name__}.")
        if trade.order_qty <= 0:
            raise ValueError(f"order_qty must be > 0, got {trade.order_qty}.")

        filled_qty = trade.order_qty if trade.filled_qty is None else trade.filled_qty
        if isinstance(filled_qty, bool) or not isinstance(filled_qty, int):
            raise TypeError(f"filled_qty must be an int or None, got {type(filled_qty).__name__}.")
        if filled_qty <= 0:
            raise ValueError(
                f"filled_qty must be > 0, got {filled_qty}. A fully unfilled order has no "
                "execution price to attribute; its entire cost is opportunity cost -- see "
                "implementation-shortfall-minimization."
            )
        if filled_qty > trade.order_qty:
            raise ValueError(
                f"filled_qty ({filled_qty}) cannot exceed order_qty ({trade.order_qty})."
            )
        return filled_qty / trade.order_qty, filled_qty, trade.order_qty

    def _resolve_durations(self, trade: TradeExecutionSummary) -> Tuple[float, float]:
        """
        Parses the three timestamps, enforces ``decision <= arrival <= completion``
        and returns ``(delay_seconds, execution_duration_seconds)``.

        The timestamps were previously ingested and never parsed, so out-of-order
        data passed silently and ACCELERATE_ORDER_DISPATCH was issued without anyone
        knowing whether the delay was 2 ms or 20 minutes.
        """
        decision_ts = _parse_iso_timestamp(trade.decision_time_iso, "decision_time_iso")
        arrival_ts = _parse_iso_timestamp(trade.arrival_time_iso, "arrival_time_iso")
        completion_ts = _parse_iso_timestamp(trade.completion_time_iso, "completion_time_iso")

        if arrival_ts < decision_ts:
            raise ValueError(
                f"arrival_time_iso ({trade.arrival_time_iso}) precedes decision_time_iso "
                f"({trade.decision_time_iso}); timing slippage would be attributed backwards."
            )
        if completion_ts < arrival_ts:
            raise ValueError(
                f"completion_time_iso ({trade.completion_time_iso}) precedes arrival_time_iso "
                f"({trade.arrival_time_iso})."
            )
        return (
            (arrival_ts - decision_ts).total_seconds(),
            (completion_ts - arrival_ts).total_seconds(),
        )

    def _classify_driver(
        self, timing_is_bps: float, sizing_is_bps: float, total_is_bps: float
    ) -> Tuple[str, str, bool]:
        """
        Ranks the two components by *adverse* cost and returns
        ``(driver, recommendation, secondary_driver_material)``.
        """
        threshold = self.materiality_threshold_bps
        timing_material = timing_is_bps > threshold
        sizing_material = sizing_is_bps > threshold

        if not timing_material and not sizing_material:
            if total_is_bps < -threshold:
                return "FAVORABLE_EXECUTION", "NO_ACTION_COST_FAVORABLE", False
            return "ZERO_SLIPPAGE", "OPTIMAL", False

        both_material = timing_material and sizing_material
        if both_material and abs(timing_is_bps - sizing_is_bps) <= threshold:
            # Previously this exact tie fell through to ZERO_SLIPPAGE / OPTIMAL, so a
            # +50/+50 split reported 100 bps of real cost as "minimal slippage".
            return "BOTH_DRIVERS_MATERIAL", "REDUCE_DELAY_AND_PARTICIPATION", True

        if timing_is_bps > sizing_is_bps:
            return "TIMING_DRIVEN_SLIPPAGE", "ACCELERATE_ORDER_DISPATCH", both_material
        return "SIZING_DRIVEN_SLIPPAGE", "REDUCE_PARTICIPATION_RATE_CEILING", both_material

    def _build_audit_notes(
        self,
        trade: TradeExecutionSummary,
        driver: str,
        total_is_bps: float,
        timing_is_bps: float,
        sizing_is_bps: float,
        timing_pct: float,
        sizing_pct: float,
        delay_seconds: float,
        execution_seconds: float,
        fill_ratio: float,
        filled_qty: int,
        order_qty: int,
        secondary_material: bool,
    ) -> str:
        """Builds the human-readable audit line recorded on the report."""
        header = (
            f"SLIPPAGE ATTRIBUTION [{trade.trade_id} - {trade.symbol}]: "
            f"Total executed-leg IS = {total_is_bps:+.2f}bps."
        )
        if driver == "TIMING_DRIVEN_SLIPPAGE":
            body = (
                f" Driven primarily by TIMING DELAY ({timing_is_bps:+.2f}bps / {timing_pct:.1f}% of "
                f"gross cost) over a {delay_seconds:.3f}s decision-to-arrival delay. "
                "Recommend ACCELERATE_ORDER_DISPATCH."
            )
        elif driver == "SIZING_DRIVEN_SLIPPAGE":
            body = (
                f" Driven primarily by SIZING MARKET IMPACT ({sizing_is_bps:+.2f}bps / "
                f"{sizing_pct:.1f}% of gross cost) over a {execution_seconds:.3f}s execution window. "
                "Recommend REDUCE_PARTICIPATION_RATE_CEILING."
            )
        elif driver == "BOTH_DRIVERS_MATERIAL":
            body = (
                f" TIMING ({timing_is_bps:+.2f}bps) and SIZING ({sizing_is_bps:+.2f}bps) are both "
                "materially adverse and neither dominates. Recommend REDUCE_DELAY_AND_PARTICIPATION; "
                "fixing only one leg leaves most of the cost in place."
            )
        elif driver == "FAVORABLE_EXECUTION":
            body = (
                f" Execution was favourable (timing {timing_is_bps:+.2f}bps, sizing "
                f"{sizing_is_bps:+.2f}bps); no component is materially adverse. No action."
            )
        elif total_is_bps > self.materiality_threshold_bps:
            # Both legs sit under the per-component threshold, yet they sum to a
            # materially adverse total. Saying "minimal slippage" here would
            # contradict the WARNING this line is logged at.
            body = (
                f" Neither leg is individually material (timing {timing_is_bps:+.2f}bps, sizing "
                f"{sizing_is_bps:+.2f}bps, threshold {self.materiality_threshold_bps:.2f}bps), but "
                "they sum to a materially adverse total; there is no single leg to action."
            )
        else:
            body = (
                f" Minimal slippage (timing {timing_is_bps:+.2f}bps, sizing {sizing_is_bps:+.2f}bps; "
                f"materiality threshold {self.materiality_threshold_bps:.2f}bps)."
            )

        if secondary_material and driver in ("TIMING_DRIVEN_SLIPPAGE", "SIZING_DRIVEN_SLIPPAGE"):
            body += " NOTE: the secondary component is also materially adverse."
        if filled_qty < order_qty:
            body += (
                f" PARTIAL FILL {filled_qty}/{order_qty} ({fill_ratio:.1%}): opportunity cost on the "
                "unfilled residual is NOT included -- this is not the full Implementation Shortfall."
            )
        return header + body
