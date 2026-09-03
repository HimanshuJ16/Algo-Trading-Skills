"""Tick size regime impact assessment: spread decomposition, depth, and algo recalibration.

Scope and sourcing (verified 2026-09-02):

* **Spread definitions follow SEC Rule 605 (17 CFR 242.605) and the definitions in
  17 CFR 242.600(b).** Effective spread is ``2 x D x (P_exec - M)``; realized spread is
  ``2 x D x (P_exec - M_{t+h})``, with ``D = +1`` for a buy and ``-1`` for a sell.
  These are the formulas the SEC Tick Size Pilot itself used.

* **Rule 605 averages are share-weighted**, not equally weighted: 17 CFR 242.600(b)(8),
  (12) and (13) each define the statistic as "the share-weighted average". This module
  share-weights whenever every trading snapshot carries ``last_trade_size`` and reports
  which weighting was actually applied in ``TickMetrics.weighting``. Equal weighting
  over-counts odd lots and is not comparable to a published Rule 605 report.

* **Benchmark midpoint differs from Rule 605.** Rule 605 measures effective spread
  against the NBBO midpoint *at the time of order receipt* (17 CFR 242.600(b)(8)). This
  module measures against the midpoint of the snapshot carrying the trade -- the
  trade-time quote midpoint, which is the standard microstructure-research convention.
  The two agree only when receipt and execution occur inside the same quote. Numbers
  produced here are therefore **not** a substitute for a Rule 605 filing.

* **Realized-spread horizon.** ``future_mid_price_5m`` is the 5-minute horizon the Tick
  Size Pilot used. Amended Rule 605 requires realized spread at 50 ms, 1 s, 15 s, 1 min
  *and* 5 min (17 CFR 242.605(a)(1)(i)(O)-(X)); the Pilot Assessment found the measured
  gap between test and control groups depends on which horizon is used. Treat 5 minutes
  as one horizon, not "the" answer.

* **End-of-session proviso.** 17 CFR 242.600(b)(13) requires the midpoint of the *final*
  NBBO disseminated for regular trading hours to be used when the horizon would run past
  the close. This module cannot see the session calendar; the caller must apply the
  proviso when populating ``future_mid_price_5m`` and leave the field ``None`` if it
  cannot. A ``None`` is excluded from the realized-spread sample rather than imputed.

* **SEC Tick Size Pilot Program.** An NMS plan approved by the Commission on 2015-05-06;
  the quoting and trading requirements ran from 2016-10-03 until the close on
  2018-09-28. It is a *historical* regime, not current US market structure. See
  ``references/standards.md`` for group definitions and measured outcomes.

This module measures a regime change; it does not predict one. No effect size is
hard-coded, because the published pilot outcomes vary by an order of magnitude across
pre-pilot spread classes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Realized-spread horizon, in seconds, that ``TickSnapshot.future_mid_price_5m``
#: represents. The Tick Size Pilot and older Rule 605 both used 5 minutes.
REALIZED_SPREAD_HORIZON_SECONDS = 300

#: Screening thresholds for narrative findings and tuning recommendations. These are
#: **heuristic reporting triggers chosen for this engine**, not regulatory limits and
#: not empirical constants from any study. Tune them to the desk's own tolerances.
SPREAD_FINDING_THRESHOLD_PCT = 20.0
DEPTH_FINDING_THRESHOLD_PCT = 30.0
ADVERSE_SELECTION_FINDING_BPS = 2.0
MARKET_MAKING_ADVERSE_SELECTION_BPS = 1.5
SLICING_SPREAD_THRESHOLD_PCT = 25.0


class TickRegime(Enum):
    STANDARD_CENT = "STANDARD_CENT"          # $0.01 quoting increment (SEC Rule 612)
    WIDENED_FIVE_CENT = "WIDENED_FIVE_CENT"  # $0.05 (SEC Tick Size Pilot test groups)
    SUB_PENNY = "SUB_PENNY"                  # Finer than $0.01 (e.g. $0.005, $0.0001)
    DYNAMIC_BAND = "DYNAMIC_BAND"            # Price x liquidity band (MiFID II RTS 11)


class AlgoStrategyType(Enum):
    PASSIVE_MARKET_MAKING = "PASSIVE_MARKET_MAKING"
    TWAP_VWAP_SLICING = "TWAP_VWAP_SLICING"
    STAT_ARB = "STAT_ARB"
    MOMENTUM_TAKER = "MOMENTUM_TAKER"


class SpreadWeighting(Enum):
    """Weighting actually applied to the effective/realized spread averages."""

    SHARE_WEIGHTED = "SHARE_WEIGHTED"  # Rule 605 convention (17 CFR 242.600(b))
    EQUAL_WEIGHTED = "EQUAL_WEIGHTED"  # Fallback: at least one trade had no size


class InvalidSnapshotPolicy(Enum):
    """What to do with a snapshot that fails validation inside a batch."""

    SKIP = "SKIP"    # Exclude it, count it, carry on (default: real tick data is dirty)
    RAISE = "RAISE"  # Abort the batch (use when the feed is expected to be clean)


class MicrostructureError(ValueError):
    """Raised for invalid market state or invalid analytical input."""


@dataclass
class TickSnapshot:
    """One quote snapshot, optionally carrying the trade that printed against it.

    ``future_mid_price_5m`` must already respect the Rule 605 end-of-session proviso
    (17 CFR 242.600(b)(13)); leave it ``None`` when the horizon cannot be observed.
    """

    timestamp_ns: int
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    last_trade_price: Optional[float] = None
    last_trade_size: Optional[float] = None
    last_trade_is_buy: Optional[bool] = None
    future_mid_price_5m: Optional[float] = None


@dataclass
class TickMetrics:
    """Aggregate microstructure metrics for one symbol under one tick regime.

    Spread and adverse-selection fields are ``None`` when the sample cannot support
    them -- no trades, or no observed future midpoints. They are never imputed from
    another metric: a fabricated effective spread silently becomes a fabricated
    regime comparison.
    """

    symbol: str
    regime: TickRegime
    sample_count: int
    avg_quoted_spread: float
    avg_effective_spread: Optional[float]
    avg_realized_spread_5m: Optional[float]
    avg_top_depth_shares: float
    avg_order_to_trade_ratio: Optional[float]
    share_fill_rate_pct: Optional[float]
    adverse_selection_bps: Optional[float]
    avg_midpoint: float
    trade_sample_count: int = 0
    realized_sample_count: int = 0
    excluded_snapshot_count: int = 0
    weighting: SpreadWeighting = SpreadWeighting.EQUAL_WEIGHTED


@dataclass
class RegimeComparisonResult:
    """Baseline-vs-test comparison.

    Percentage-change fields are ``None`` when undefined -- the baseline metric was
    absent, zero, or non-positive. A zero baseline effective spread is a real outcome
    (every print at the midpoint), not a data error, so it is reported as undefined
    rather than as an infinite or sign-flipped percentage.

    ``fill_rate_change_pp`` is a difference in **percentage points**, not a percentage
    change, because the underlying metric is already a percentage.
    """

    symbol: str
    baseline_regime: TickRegime
    test_regime: TickRegime
    quoted_spread_change_pct: Optional[float]
    effective_spread_change_pct: Optional[float]
    top_depth_change_pct: Optional[float]
    fill_rate_change_pp: Optional[float]
    adverse_selection_change_bps: Optional[float]
    key_findings: List[str] = field(default_factory=list)
    undefined_metrics: List[str] = field(default_factory=list)


def _require_finite(value: float, label: str) -> float:
    """Reject NaN/Inf before they propagate silently into an average."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MicrostructureError(f"{label} must be a number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MicrostructureError(f"{label} must be finite, got {value!r}")
    return numeric


def _pct_change(baseline: Optional[float], test: Optional[float]) -> Optional[float]:
    """Percentage change, or ``None`` where the ratio is not meaningful.

    A non-positive baseline is treated as undefined rather than divided through: at
    zero it raises, and at a negative baseline the sign of the result inverts, which
    would report a widening spread as a compression.
    """
    if baseline is None or test is None or baseline <= 0.0:
        return None
    return ((test - baseline) / baseline) * 100.0


class TickSizeImpactEngine:
    """Measures how a tick size regime change moved spreads, depth and fill rates.

    The engine is stateless: every method derives its result solely from its arguments,
    so the same inputs always produce the same output and results can be recomputed
    from an audit log.
    """

    def __init__(self) -> None:
        logger.info(
            "Initialized tick size impact engine (realized-spread horizon %ds)",
            REALIZED_SPREAD_HORIZON_SECONDS,
        )

    @staticmethod
    def calculate_quoted_spread(bid: float, ask: float) -> float:
        """Quoted spread (ask - bid).

        A **locked** quote (ask == bid) returns ``0.0``: it is a transient but real
        state in consolidated data and its quoted spread genuinely is zero. A
        **crossed** quote (ask < bid) raises, because a negative spread is not a
        measurement, and averaging it in would understate the true cost of liquidity.
        """
        bid_value = _require_finite(bid, "bid")
        ask_value = _require_finite(ask, "ask")
        if bid_value <= 0.0 or ask_value <= 0.0:
            raise MicrostructureError(
                f"Prices must be positive: bid={bid_value}, ask={ask_value}")
        if ask_value < bid_value:
            raise MicrostructureError(
                f"Crossed market: ask ({ask_value}) < bid ({bid_value})")
        return ask_value - bid_value

    @staticmethod
    def calculate_effective_spread(trade_price: float, mid_price: float, is_buy: bool) -> float:
        """Effective spread in currency units: ``2 x D x (P_trade - M)``.

        Per 17 CFR 242.600(b)(8) and the Tick Size Pilot Assessment: a print at the
        midpoint yields exactly zero, and a print inside the midpoint (price improvement
        beyond the midpoint) yields a negative value. Both are valid measurements.
        """
        price = _require_finite(trade_price, "trade_price")
        mid = _require_finite(mid_price, "mid_price")
        if price <= 0.0 or mid <= 0.0:
            raise MicrostructureError(
                f"Prices must be positive: trade_price={price}, mid_price={mid}")
        direction = 1.0 if is_buy else -1.0
        return 2.0 * direction * (price - mid)

    @staticmethod
    def calculate_realized_spread(trade_price: float, future_mid_price: float, is_buy: bool) -> float:
        """Realized spread: ``2 x D x (P_trade - M_{t+h})``.

        Measures liquidity-provider revenue net of the price move against the maker.
        ``future_mid_price`` must already honour the end-of-session proviso in
        17 CFR 242.600(b)(13).
        """
        price = _require_finite(trade_price, "trade_price")
        future_mid = _require_finite(future_mid_price, "future_mid_price")
        if price <= 0.0 or future_mid <= 0.0:
            raise MicrostructureError(
                f"Prices must be positive: trade_price={price}, future_mid_price={future_mid}")
        direction = 1.0 if is_buy else -1.0
        return 2.0 * direction * (price - future_mid)

    @staticmethod
    def _weighted_average(samples: Sequence[Tuple[float, float]]) -> Optional[float]:
        """Weighted mean of ``(value, weight)`` pairs; ``None`` if the weight sums to 0."""
        if not samples:
            return None
        total_weight = math.fsum(weight for _, weight in samples)
        if total_weight <= 0.0:
            return None
        return math.fsum(value * weight for value, weight in samples) / total_weight

    def evaluate_microstructure_metrics(
        self,
        symbol: str,
        regime: TickRegime,
        snapshots: Sequence[TickSnapshot],
        total_messages: int = 0,
        total_fills: int = 0,
        total_shares_ordered: int = 0,
        total_shares_executed: int = 0,
        invalid_snapshot_policy: InvalidSnapshotPolicy = InvalidSnapshotPolicy.SKIP,
    ) -> TickMetrics:
        """Aggregate microstructure metrics across a series of snapshots.

        Args:
            symbol: Instrument identifier, carried through to the result.
            regime: The tick regime this sample was drawn under.
            snapshots: Quote snapshots, optionally carrying the trade that printed
                against each quote.
            total_messages: Order and quote messages sent over the sample window,
                for the order-to-trade ratio.
            total_fills: Executions over the same window. OTR is
                ``total_messages / total_fills``.
            total_shares_ordered: Shares represented by orders submitted.
            total_shares_executed: Shares executed. The fill rate is
                ``executed / ordered``, which is how the Tick Size Pilot reported it --
                *not* fills per message, which is only the reciprocal of the OTR.
            invalid_snapshot_policy: ``SKIP`` excludes unusable snapshots and counts
                them in ``excluded_snapshot_count``; ``RAISE`` aborts the batch.

        Effective and realized spreads are share-weighted when every trading snapshot
        carries ``last_trade_size``, and equally weighted otherwise; the choice is
        reported in ``TickMetrics.weighting``.
        """
        if not snapshots:
            raise MicrostructureError("Cannot compute metrics on an empty snapshot list.")
        for label, count in (
            ("total_messages", total_messages),
            ("total_fills", total_fills),
            ("total_shares_ordered", total_shares_ordered),
            ("total_shares_executed", total_shares_executed),
        ):
            if count < 0:
                raise MicrostructureError(f"{label} must be >= 0, got {count}")

        quoted_spreads: List[float] = []
        midpoints: List[float] = []
        top_depths: List[float] = []
        effective_samples: List[Tuple[float, float]] = []
        realized_samples: List[Tuple[float, float]] = []
        excluded = 0
        share_weighted = True

        for index, snapshot in enumerate(snapshots):
            try:
                quoted_spread = self.calculate_quoted_spread(
                    snapshot.bid_price, snapshot.ask_price)
                bid_size = _require_finite(snapshot.bid_size, "bid_size")
                ask_size = _require_finite(snapshot.ask_size, "ask_size")
                if bid_size < 0.0 or ask_size < 0.0:
                    raise MicrostructureError(
                        f"Sizes must be >= 0: bid_size={bid_size}, ask_size={ask_size}")
            except MicrostructureError:
                if invalid_snapshot_policy is InvalidSnapshotPolicy.RAISE:
                    raise
                excluded += 1
                logger.warning(
                    "Excluded snapshot %d for %s: unusable quote (bid=%r ask=%r)",
                    index, symbol, snapshot.bid_price, snapshot.ask_price)
                continue

            mid = (snapshot.bid_price + snapshot.ask_price) / 2.0
            quoted_spreads.append(quoted_spread)
            midpoints.append(mid)
            top_depths.append(bid_size + ask_size)

            if snapshot.last_trade_price is None or snapshot.last_trade_is_buy is None:
                continue

            try:
                effective = self.calculate_effective_spread(
                    snapshot.last_trade_price, mid, snapshot.last_trade_is_buy)
                weight = 1.0
                if snapshot.last_trade_size is None:
                    share_weighted = False
                else:
                    weight = _require_finite(snapshot.last_trade_size, "last_trade_size")
                    if weight <= 0.0:
                        raise MicrostructureError(
                            f"last_trade_size must be > 0, got {weight}")
                realized: Optional[float] = None
                if snapshot.future_mid_price_5m is not None:
                    realized = self.calculate_realized_spread(
                        snapshot.last_trade_price,
                        snapshot.future_mid_price_5m,
                        snapshot.last_trade_is_buy,
                    )
            except MicrostructureError:
                if invalid_snapshot_policy is InvalidSnapshotPolicy.RAISE:
                    raise
                logger.warning(
                    "Excluded trade on snapshot %d for %s: unusable trade fields "
                    "(price=%r size=%r future_mid=%r)",
                    index, symbol, snapshot.last_trade_price,
                    snapshot.last_trade_size, snapshot.future_mid_price_5m)
                continue

            effective_samples.append((effective, weight))
            if realized is not None:
                realized_samples.append((realized, weight))

        if not quoted_spreads:
            raise MicrostructureError(
                f"No usable snapshots for {symbol}: all {len(snapshots)} were excluded.")

        weighting = (
            SpreadWeighting.SHARE_WEIGHTED
            if share_weighted and effective_samples
            else SpreadWeighting.EQUAL_WEIGHTED
        )
        if weighting is SpreadWeighting.EQUAL_WEIGHTED and effective_samples:
            logger.warning(
                "%s: at least one trade lacked last_trade_size; falling back to equal "
                "weighting. Results are not comparable to a Rule 605 share-weighted report.",
                symbol,
            )
            effective_samples = [(value, 1.0) for value, _ in effective_samples]
            realized_samples = [(value, 1.0) for value, _ in realized_samples]

        avg_quoted_spread = math.fsum(quoted_spreads) / len(quoted_spreads)
        avg_midpoint = math.fsum(midpoints) / len(midpoints)
        avg_depth = math.fsum(top_depths) / len(top_depths)
        avg_effective = self._weighted_average(effective_samples)
        avg_realized = self._weighted_average(realized_samples)

        # Adverse selection (price impact) = effective - realized, expressed against the
        # average midpoint, mirroring the Rule 605 "average percentage spread" construction
        # (17 CFR 242.600(b)(10)-(11)): a ratio of averages, not an average of ratios.
        adverse_selection_bps: Optional[float] = None
        if avg_effective is not None and avg_realized is not None and avg_midpoint > 0.0:
            adverse_selection_bps = (avg_effective - avg_realized) / avg_midpoint * 10_000.0

        otr = (total_messages / total_fills) if total_fills > 0 else None
        share_fill_rate = (
            (total_shares_executed / total_shares_ordered * 100.0)
            if total_shares_ordered > 0
            else None
        )

        logger.info(
            "Evaluated %s under %s: quoted spread=%.4f, depth=%.0f shares, "
            "%d trades (%s), %d excluded snapshots",
            symbol, regime.value, avg_quoted_spread, avg_depth,
            len(effective_samples), weighting.value, excluded,
        )

        return TickMetrics(
            symbol=symbol,
            regime=regime,
            sample_count=len(quoted_spreads),
            avg_quoted_spread=avg_quoted_spread,
            avg_effective_spread=avg_effective,
            avg_realized_spread_5m=avg_realized,
            avg_top_depth_shares=avg_depth,
            avg_order_to_trade_ratio=otr,
            share_fill_rate_pct=share_fill_rate,
            adverse_selection_bps=adverse_selection_bps,
            avg_midpoint=avg_midpoint,
            trade_sample_count=len(effective_samples),
            realized_sample_count=len(realized_samples),
            excluded_snapshot_count=excluded,
            weighting=weighting,
        )

    def compare_regimes(self, baseline: TickMetrics, test: TickMetrics) -> RegimeComparisonResult:
        """Quantify the shift between a baseline and a test tick regime.

        Metrics that cannot be compared are reported as ``None`` and named in
        ``undefined_metrics`` rather than silently defaulted, so a caller never reads a
        zero as "no change" when it actually means "not measured".
        """
        if baseline.symbol != test.symbol:
            raise MicrostructureError(
                f"Cannot compare different symbols: {baseline.symbol!r} vs {test.symbol!r}")
        if baseline.regime is test.regime:
            logger.warning(
                "%s: baseline and test carry the same regime (%s); the comparison "
                "measures period effects, not a tick regime change.",
                baseline.symbol, baseline.regime.value)
        if baseline.weighting is not test.weighting:
            logger.warning(
                "%s: baseline is %s but test is %s; spread changes across different "
                "weightings are not like-for-like.",
                baseline.symbol, baseline.weighting.value, test.weighting.value)

        quoted_change = _pct_change(baseline.avg_quoted_spread, test.avg_quoted_spread)
        effective_change = _pct_change(baseline.avg_effective_spread, test.avg_effective_spread)
        depth_change = _pct_change(baseline.avg_top_depth_shares, test.avg_top_depth_shares)

        fill_rate_change = (
            test.share_fill_rate_pct - baseline.share_fill_rate_pct
            if baseline.share_fill_rate_pct is not None and test.share_fill_rate_pct is not None
            else None
        )
        adverse_selection_change = (
            test.adverse_selection_bps - baseline.adverse_selection_bps
            if baseline.adverse_selection_bps is not None and test.adverse_selection_bps is not None
            else None
        )

        undefined: List[str] = []
        for name, value in (
            ("quoted_spread_change_pct", quoted_change),
            ("effective_spread_change_pct", effective_change),
            ("top_depth_change_pct", depth_change),
            ("fill_rate_change_pp", fill_rate_change),
            ("adverse_selection_change_bps", adverse_selection_change),
        ):
            if value is None:
                undefined.append(name)

        findings: List[str] = []
        if quoted_change is not None:
            if quoted_change > SPREAD_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Quoted spread widened by {quoted_change:.1f}%. Posted top-of-book "
                    f"width increased; confirm against the effective spread before "
                    f"assuming taker costs rose by the same proportion.")
            elif quoted_change < -SPREAD_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Quoted spread compressed by {abs(quoted_change):.1f}%.")
        if effective_change is not None:
            if effective_change > SPREAD_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Effective spread widened by {effective_change:.1f}%. This is the "
                    f"realized cost paid by liquidity takers.")
            elif effective_change < -SPREAD_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Effective spread compressed by {abs(effective_change):.1f}%.")
        if depth_change is not None:
            if depth_change > DEPTH_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Inside book depth expanded by {depth_change:.1f}%. Longer queue "
                    f"waiting times for passive limit orders.")
            elif depth_change < -DEPTH_FINDING_THRESHOLD_PCT:
                findings.append(
                    f"Inside book depth thinned by {abs(depth_change):.1f}%.")
        if adverse_selection_change is not None and adverse_selection_change > ADVERSE_SELECTION_FINDING_BPS:
            findings.append(
                f"Adverse selection increased by {adverse_selection_change:.2f} bps for "
                f"passive limit orders.")
        if undefined:
            findings.append(
                f"Not measured (insufficient data): {', '.join(undefined)}.")

        logger.info(
            "Regime comparison for %s (%s -> %s): quoted=%s, effective=%s, depth=%s",
            baseline.symbol, baseline.regime.value, test.regime.value,
            "n/a" if quoted_change is None else f"{quoted_change:.1f}%",
            "n/a" if effective_change is None else f"{effective_change:.1f}%",
            "n/a" if depth_change is None else f"{depth_change:.1f}%",
        )

        return RegimeComparisonResult(
            symbol=baseline.symbol,
            baseline_regime=baseline.regime,
            test_regime=test.regime,
            quoted_spread_change_pct=quoted_change,
            effective_spread_change_pct=effective_change,
            top_depth_change_pct=depth_change,
            fill_rate_change_pp=fill_rate_change,
            adverse_selection_change_bps=adverse_selection_change,
            key_findings=findings,
            undefined_metrics=undefined,
        )

    def recommend_strategy_tuning(
        self, algo_type: AlgoStrategyType, comparison: RegimeComparisonResult
    ) -> List[str]:
        """Suggest execution parameter changes implied by a regime comparison.

        Output is advisory and screening-only: every branch is gated on the module's
        heuristic thresholds, and an undefined metric produces an explicit "cannot
        assess" line rather than being read as no change. Never route on this output
        without a human or backtested review.
        """
        if not isinstance(algo_type, AlgoStrategyType):
            raise MicrostructureError(
                f"algo_type must be an AlgoStrategyType, got {algo_type!r}")

        quoted = comparison.quoted_spread_change_pct
        depth = comparison.top_depth_change_pct
        adverse = comparison.adverse_selection_change_bps
        recommendations: List[str] = []

        if algo_type is AlgoStrategyType.PASSIVE_MARKET_MAKING:
            if quoted is None or depth is None:
                recommendations.append(
                    "Cannot assess queue impact: quoted spread or depth change is undefined.")
            elif quoted > 0.0 and depth > 0.0:
                recommendations.append(
                    "Widened tick regime: queue is deeper at fewer price points. Join "
                    "early to secure time priority; measure realised queue position "
                    "rather than assuming it.")
                recommendations.append(
                    "Consider pegged order types with an offset to avoid queue "
                    "subordination behind a longer resting queue.")
            if adverse is None:
                recommendations.append(
                    "Cannot assess adverse selection: realized spread sample is missing.")
            elif adverse > MARKET_MAKING_ADVERSE_SELECTION_BPS:
                recommendations.append(
                    "Rising adverse selection: tighten inventory risk limits and reduce "
                    "cancel latency; fills at the queue tail are the most toxic.")

        elif algo_type is AlgoStrategyType.TWAP_VWAP_SLICING:
            # Gate on the effective spread where available: the quoted spread is what
            # widens mechanically under a coarser tick, but the effective spread is what
            # the order actually pays.
            cost_change = (
                comparison.effective_spread_change_pct
                if comparison.effective_spread_change_pct is not None
                else quoted
            )
            if cost_change is None:
                recommendations.append(
                    "Cannot assess crossing cost: no spread change measured.")
            elif cost_change > SLICING_SPREAD_THRESHOLD_PCT:
                recommendations.append(
                    "Crossing cost rose: shift the passive/aggressive mix toward passive "
                    "limits and re-fit the slicing schedule to the measured cost, not to "
                    "the nominal tick ratio.")
                recommendations.append(
                    "Enforce price caps derived from the measured effective spread.")
            elif cost_change < -SLICING_SPREAD_THRESHOLD_PCT:
                recommendations.append(
                    "Crossing cost fell: more aggressive taking is affordable, subject to "
                    "unchanged adverse selection.")

        elif algo_type is AlgoStrategyType.MOMENTUM_TAKER:
            cost_change = (
                comparison.effective_spread_change_pct
                if comparison.effective_spread_change_pct is not None
                else quoted
            )
            if cost_change is None:
                recommendations.append(
                    "Cannot assess take cost: no spread change measured.")
            elif cost_change > 0.0:
                recommendations.append(
                    "Higher take cost: raise the signal conviction threshold required "
                    "before sending IOC or sweep orders.")

        elif algo_type is AlgoStrategyType.STAT_ARB:
            if quoted is None:
                recommendations.append(
                    "Cannot assess stat-arb impact: quoted spread change is undefined.")
            elif quoted > 0.0:
                recommendations.append(
                    "Coarser pricing grid: re-estimate the entry/exit band against the "
                    "wider spread; a signal narrower than one tick is not tradable.")
                recommendations.append(
                    "Re-check leg-level cost assumptions -- a multi-leg spread pays the "
                    "widened cost on every leg.")

        return recommendations
