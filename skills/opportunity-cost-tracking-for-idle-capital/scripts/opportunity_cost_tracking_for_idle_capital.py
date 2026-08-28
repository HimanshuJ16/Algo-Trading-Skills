"""
opportunity-cost-tracking-for-idle-capital: measures the return drag created by
unallocated cash in a trading portfolio and decides whether sweeping it into a
yield vehicle is economic.

Core accrual
------------
Opportunity cost is the yield *foregone*, i.e. the spread between the benchmark
rate the cash could earn and the rate it already earns where it currently sits::

    net_spread_pct = benchmark_rate_pct - cash_yield_pct
    period_yield   = accrue(net_spread_pct, days, day_count_basis)
    gross_drag_usd = unallocated_cash * period_yield

Idle cash in a brokerage or custody account is rarely earning zero -- it usually
earns broker credit interest or a sweep money-market yield. Charging the *full*
benchmark rate as "lost" overstates the drag by exactly the yield already being
received, which is the difference between a ~45 bp decision and a ~525 bp one.
``cash_yield_pct`` defaults to 0.0, which reproduces the full-benchmark drag; set
it to the rate actually being credited.

Day count
---------
SOFR is quoted on an **Actual/360** day count, the standard convention in US money
markets, and the New York Fed's SOFR Averages and SOFR Index compound on that same
Actual/360 basis (ARRC, *An Updated User's Guide to SOFR*, 2021; FRBNY SOFR Averages
and Index methodology). US Treasury bills and other money-market instruments share
the convention. Accruing a SOFR-quoted rate over ``days/365`` understates the drag
by 365/360 - 1 = 1.39%. ``DayCount.ACT_360`` is therefore the default;
``DayCount.ACT_365F`` is available for rates genuinely quoted on a 365-day year
(e.g. SONIA, or coupon Treasury yield quotes).

Accrual method
--------------
``AccrualMethod.SIMPLE`` (default) applies simple interest and is adequate for the
short horizons this engine is normally pointed at. ``AccrualMethod.DAILY_COMPOUNDED``
compounds at ``rate/basis`` every calendar day, approximating the compounding the
published SOFR Index performs. It is only an approximation: the FRBNY index compounds
on *business* days and applies simple interest across non-business days at the
preceding business day's rate, so calendar-day compounding is a slight overestimate.
If an exact realized figure is required, take the ratio of two published SOFR Index
values rather than modelling any accrual here.

Limitations (documented, deliberate)
------------------------------------
- **SOFR is a benchmark, not an achievable yield.** SOFR measures the cost of
  borrowing cash overnight collateralized by Treasury securities. It is not an
  instrument a portfolio can invest in. A sweep lands in a money-market fund, a
  T-bill ladder, or a broker credit-interest program, each of which yields near but
  not equal to SOFR and carries its own fees, cut-off times, and settlement lag.
  Treat the output as a decision threshold, not as a realizable P&L forecast.
- **No credit, liquidity, or settlement risk is modelled.** The engine assumes swept
  cash is recoverable when the strategy needs it. Redemption timing (T+1 money-market
  settlement, T-bill maturity) is the caller's problem, and is exactly why
  ``operational_buffer_usd`` exists.
- **Single currency.** All amounts are assumed to be in one currency (USD by the
  field names). Multi-currency cash requires a benchmark rate per currency -- see
  ``multi-currency-pnl-and-fx-conversion``.
- **A rate is a point-in-time input, not a constant.** ``benchmark_rate_pct`` has no
  default: a hardcoded rate silently accrues against a level that may not have been
  current for years. Pull it from the FRBNY publication for the relevant date.
- **Tax is out of scope.** Sweep yield is generally taxable income; the pre-tax net
  gain computed here is not the after-tax gain.
"""
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class DayCount(str, Enum):
    """
    Day count basis for accruing an annualized rate over a holding period.

    ACT_360 is the US money-market convention and the one SOFR, the SOFR Averages,
    the SOFR Index, and T-bills are quoted on. ACT_365F is the fixed 365-day year
    used by SONIA and by coupon Treasury yield quotes.
    """
    ACT_360 = "ACT/360"
    ACT_365F = "ACT/365F"

    @property
    def basis(self) -> float:
        return 360.0 if self is DayCount.ACT_360 else 365.0


class AccrualMethod(str, Enum):
    """SIMPLE = simple interest. DAILY_COMPOUNDED = compounded each calendar day."""
    SIMPLE = "SIMPLE"
    DAILY_COMPOUNDED = "DAILY_COMPOUNDED"


#: Rates below this magnitude (in percent) are almost certainly a fraction that was
#: never converted -- 0.0525 passed where 5.25 was meant understates the drag 100x.
SUSPICIOUS_RATE_PCT_LOWER = 0.5

#: Rates above this magnitude (in percent) are almost certainly a unit error for a
#: short-term money-market benchmark.
SUSPICIOUS_RATE_PCT_UPPER = 100.0

# Sweep-blocked reason codes surfaced on OpportunityCostReport.sweep_blocked_reason.
REASON_NO_YIELD_ADVANTAGE = "NO_YIELD_ADVANTAGE"
REASON_BELOW_MIN_SWEEP_THRESHOLD = "BELOW_MIN_SWEEP_THRESHOLD"
REASON_SWEEP_COST_EXCEEDS_YIELD = "SWEEP_COST_EXCEEDS_YIELD"
REASON_BELOW_THRESHOLD_AND_UNECONOMIC = "BELOW_MIN_SWEEP_THRESHOLD_AND_UNECONOMIC"


def _require_finite(value: float, name: str) -> float:
    """Rejects non-numeric and non-finite inputs before they reach the accrual."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{name} must be a finite number, got {value}. A non-finite input "
            "propagates NaN silently through the drag calculation and yields a "
            "'MAINTAIN_IDLE_CASH' recommendation justified by no data at all."
        )
    return float(value)


def accrue(
    rate_pct: float,
    days: float,
    day_count: DayCount = DayCount.ACT_360,
    method: AccrualMethod = AccrualMethod.SIMPLE,
) -> float:
    """
    Accrues an annualized percentage rate over ``days`` on the given day count basis.

    Returns the period yield as a fraction (0.004375 == 43.75 bp). The result is
    negative when ``rate_pct`` is negative, which is meaningful here: a negative net
    spread means the cash is already placed better than the benchmark.

        SIMPLE:            (r / 100) * days / basis
        DAILY_COMPOUNDED:  (1 + (r / 100) / basis) ** days - 1
    """
    rate_frac = _require_finite(rate_pct, "rate_pct") / 100.0
    days = _require_finite(days, "days")
    if days < 0:
        raise ValueError(f"days must be non-negative, got {days}.")

    basis = day_count.basis
    if method is AccrualMethod.SIMPLE:
        return rate_frac * (days / basis)

    growth = 1.0 + rate_frac / basis
    if growth <= 0.0:
        raise ValueError(
            f"rate_pct {rate_pct} implies a non-positive daily growth factor on a "
            f"{day_count.value} basis; daily compounding is undefined."
        )
    try:
        return math.pow(growth, days) - 1.0
    except OverflowError as exc:
        # Keep the failure mode inside the documented (TypeError, ValueError) contract:
        # math.pow raises OverflowError, which callers guarding on ValueError miss.
        raise ValueError(
            f"Compounding {rate_pct}% over {days} days on a {day_count.value} basis "
            "overflows. A holding period this long is a unit error (days, not years)."
        ) from exc


@dataclass
class PortfolioCapitalState:
    """
    Point-in-time capital snapshot.

    ``allocated_capital + unallocated_cash`` must reconcile to ``total_capital``
    within ``SweepConfig.capital_reconciliation_tolerance_usd``; otherwise the idle
    ratio is not interpretable against the total.

    ``benchmark_rate_pct`` is deliberately required. It is a market observation for a
    specific date (pull SOFR from the FRBNY publication), not a constant.

    ``cash_yield_pct`` is the annualized rate the unallocated cash is *already*
    earning where it currently sits. Leaving it at 0.0 asserts the cash earns nothing.
    """
    total_capital: float
    allocated_capital: float
    unallocated_cash: float
    benchmark_rate_pct: float            # e.g. SOFR, in percent, for the relevant date
    holding_period_days: float = 30.0    # Capital idle duration in days
    cash_yield_pct: float = 0.0          # Rate the idle cash already earns, in percent


@dataclass
class SweepConfig:
    """
    Sweep policy. All defaults are illustrative starting points, not standards -- no
    regulator or standards body publishes a mandatory idle-cash ratio or sweep
    threshold. Calibrate them against your own settlement and margin profile.
    """
    min_sweep_threshold_usd: float = 100000.0  # Min sweepable cash to trigger a sweep
    sweep_transaction_cost_usd: float = 50.0   # All-in ROUND-TRIP cost (out and back)
    target_idle_ratio_max: float = 0.05        # Max idle ratio, as a FRACTION (0.05 = 5%)
    operational_buffer_usd: float = 0.0        # Cash reserved for margin/settlement
    day_count: DayCount = DayCount.ACT_360     # SOFR / T-bill money-market convention
    accrual_method: AccrualMethod = AccrualMethod.SIMPLE
    capital_reconciliation_tolerance_usd: float = 1.0

    def __post_init__(self) -> None:
        for name in ("min_sweep_threshold_usd", "sweep_transaction_cost_usd",
                     "operational_buffer_usd", "capital_reconciliation_tolerance_usd"):
            value = _require_finite(getattr(self, name), f"SweepConfig.{name}")
            if value < 0:
                raise ValueError(f"SweepConfig.{name} must be non-negative, got {value}.")
            setattr(self, name, value)

        ratio = _require_finite(self.target_idle_ratio_max, "SweepConfig.target_idle_ratio_max")
        if not 0.0 <= ratio <= 1.0:
            raise ValueError(
                f"SweepConfig.target_idle_ratio_max must be a fraction in [0, 1], got "
                f"{ratio}. Passing 5 to mean 5% disables the idle-ratio alert entirely."
            )
        self.target_idle_ratio_max = ratio

        # Accept the raw string values too, so config loaded from JSON/YAML works.
        if not isinstance(self.day_count, DayCount):
            self.day_count = DayCount(self.day_count)
        if not isinstance(self.accrual_method, AccrualMethod):
            self.accrual_method = AccrualMethod(self.accrual_method)


@dataclass
class OpportunityCostReport:
    total_capital_usd: float
    unallocated_cash_usd: float
    idle_capital_ratio_pct: float
    gross_opportunity_cost_usd: float    # Drag on the FULL idle balance over the period
    drag_basis_points: float             # Period drag vs total capital -- NOT annualized
    net_yield_gain_usd: float            # Recoverable yield on sweepable cash, less cost
    recommendation: str                  # 'SWEEP_TO_YIELD_BENCHMARK', 'MAINTAIN_IDLE_CASH'
    status: str                          # 'IDLE_RATIO_HEALTHY', 'IDLE_CAPITAL_RATIO_EXCEEDED'
    audit_notes: str
    # --- appended fields (defaulted, so existing positional construction stays valid) ---
    net_benchmark_spread_pct: float = 0.0        # benchmark_rate_pct - cash_yield_pct
    annualized_drag_basis_points: float = 0.0    # Period-independent drag vs total capital
    sweepable_cash_usd: float = 0.0              # Idle cash less the operational buffer
    operational_buffer_usd: float = 0.0
    recoverable_yield_usd: float = 0.0           # Drag on sweepable cash only
    breakeven_sweep_notional_usd: Optional[float] = None  # None when there is no spread
    sweep_blocked_reason: Optional[str] = None   # Why MAINTAIN_IDLE_CASH was returned
    day_count: str = DayCount.ACT_360.value
    accrual_method: str = AccrualMethod.SIMPLE.value


class OpportunityCostTrackerEngine:
    """
    Opportunity cost tracking engine measuring the return drag of idle capital against
    a money-market benchmark (SOFR by convention), net of the yield the cash already
    earns, and testing whether a cash sweep clears its own round-trip cost.
    """

    def __init__(self, config: Optional[SweepConfig] = None):
        self.config = config or SweepConfig()

    def _validate_state(self, state: PortfolioCapitalState) -> None:
        """Rejects snapshots that cannot produce an interpretable audit."""
        total = _require_finite(state.total_capital, "total_capital")
        if total <= 0:
            raise ValueError("Total portfolio capital must be greater than zero.")

        allocated = _require_finite(state.allocated_capital, "allocated_capital")
        if allocated < 0:
            raise ValueError(f"allocated_capital must be non-negative, got {allocated}.")

        cash = _require_finite(state.unallocated_cash, "unallocated_cash")
        if cash < 0:
            # A negative cash balance is a margin debit, not idle capital. Left
            # unchecked it produced a negative drag, a negative idle ratio, and an
            # 'IDLE_RATIO_HEALTHY' status -- the account is paying borrowing interest
            # and the audit reports the all-clear.
            raise ValueError(
                f"unallocated_cash must be non-negative, got {cash}. A negative balance "
                "is a margin debit, which is a borrowing cost rather than an idle-capital "
                "opportunity cost; route it to 'margin-utilization-circuit-breaker' and "
                "'broker-margin-interest-accrual-tracking'."
            )

        days = _require_finite(state.holding_period_days, "holding_period_days")
        if days <= 0:
            raise ValueError(
                f"holding_period_days must be greater than zero, got {days}. A negative "
                "period silently inverted the sign of the accrued drag."
            )

        drift = abs((allocated + cash) - total)
        if drift > self.config.capital_reconciliation_tolerance_usd:
            raise ValueError(
                f"Capital does not reconcile: allocated ({allocated:,.2f}) + unallocated "
                f"({cash:,.2f}) = {allocated + cash:,.2f}, but total_capital is "
                f"{total:,.2f} (drift {drift:,.2f} exceeds tolerance "
                f"{self.config.capital_reconciliation_tolerance_usd:,.2f}). The idle "
                "capital ratio is not interpretable against an unreconciled total."
            )

        rate = _require_finite(state.benchmark_rate_pct, "benchmark_rate_pct")
        cash_yield = _require_finite(state.cash_yield_pct, "cash_yield_pct")
        for name, value in (("benchmark_rate_pct", rate), ("cash_yield_pct", cash_yield)):
            if 0.0 < abs(value) < SUSPICIOUS_RATE_PCT_LOWER:
                logger.warning(
                    "%s = %s looks like a fraction rather than a percentage. This field is "
                    "in PERCENT (5.25 means 5.25%%); passing 0.0525 understates the drag 100x.",
                    name, value,
                )
            elif abs(value) > SUSPICIOUS_RATE_PCT_UPPER:
                logger.warning(
                    "%s = %s exceeds %.0f%% annualized, which is implausible for a "
                    "money-market benchmark and is most likely a unit error.",
                    name, value, SUSPICIOUS_RATE_PCT_UPPER,
                )

    def analyze_opportunity_cost(
        self, state: PortfolioCapitalState
    ) -> OpportunityCostReport:
        """
        Computes the idle capital ratio, the opportunity cost drag net of the yield the
        cash already earns, and whether a sweep of the non-buffer balance clears its
        round-trip cost.

        Raises rather than returning a report when the snapshot is unusable (negative
        cash, unreconciled capital, non-finite rate, non-positive holding period): each
        of those previously produced a confident-looking recommendation from bad input.
        """
        self._validate_state(state)
        cfg = self.config

        idle_ratio = state.unallocated_cash / state.total_capital
        idle_ratio_pct = idle_ratio * 100.0

        # Opportunity cost is the FOREGONE yield: benchmark less what the cash already
        # earns. A spread of zero or less means the cash is already placed at least as
        # well as the benchmark, so there is nothing to recover by sweeping.
        net_spread_pct = state.benchmark_rate_pct - state.cash_yield_pct
        period_yield_frac = accrue(
            net_spread_pct, state.holding_period_days, cfg.day_count, cfg.accrual_method
        )

        gross_drag_usd = state.unallocated_cash * period_yield_frac

        # Period drag vs total capital. Deliberately NOT annualized -- the annualized
        # figure is reported separately so the two are never conflated.
        drag_bps = (gross_drag_usd / state.total_capital) * 10000.0
        annualized_drag_bps = idle_ratio * (net_spread_pct / 100.0) * 10000.0

        # Only the balance above the operational buffer is actually sweepable: the
        # buffer covers margin calls and settlement obligations and must stay liquid.
        sweepable_cash = max(0.0, state.unallocated_cash - cfg.operational_buffer_usd)
        recoverable_yield_usd = sweepable_cash * period_yield_frac
        net_yield_gain_usd = recoverable_yield_usd - cfg.sweep_transaction_cost_usd

        # Smallest balance whose period yield covers the round-trip cost.
        breakeven_notional: Optional[float] = None
        if period_yield_frac > 0.0:
            breakeven_notional = cfg.sweep_transaction_cost_usd / period_yield_frac

        has_spread = period_yield_frac > 0.0
        is_threshold_met = sweepable_cash >= cfg.min_sweep_threshold_usd
        is_profitable = net_yield_gain_usd > 0

        blocked_reason: Optional[str] = None
        if has_spread and is_threshold_met and is_profitable:
            recommendation = "SWEEP_TO_YIELD_BENCHMARK"
        else:
            recommendation = "MAINTAIN_IDLE_CASH"
            if not has_spread:
                blocked_reason = REASON_NO_YIELD_ADVANTAGE
            elif not is_threshold_met and not is_profitable:
                blocked_reason = REASON_BELOW_THRESHOLD_AND_UNECONOMIC
            elif not is_threshold_met:
                blocked_reason = REASON_BELOW_MIN_SWEEP_THRESHOLD
            else:
                blocked_reason = REASON_SWEEP_COST_EXCEEDS_YIELD

        is_ratio_exceeded = idle_ratio > cfg.target_idle_ratio_max
        status = "IDLE_CAPITAL_RATIO_EXCEEDED" if is_ratio_exceeded else "IDLE_RATIO_HEALTHY"

        notes = (
            f"OPPORTUNITY COST AUDIT [{status}]: Unallocated Cash = ${state.unallocated_cash:,.2f} "
            f"({idle_ratio_pct:.2f}% of ${state.total_capital:,.2f} total). "
            f"Gross Drag @ {net_spread_pct:.2f}% net spread "
            f"({state.benchmark_rate_pct:.2f}% benchmark - {state.cash_yield_pct:.2f}% cash yield) "
            f"over {state.holding_period_days:.0f} days {cfg.day_count.value} "
            f"{cfg.accrual_method.value} = ${gross_drag_usd:,.2f} "
            f"({drag_bps:.2f} bps period / {annualized_drag_bps:.2f} bps annualized). "
            f"Sweepable = ${sweepable_cash:,.2f} after ${cfg.operational_buffer_usd:,.2f} buffer; "
            f"net gain after ${cfg.sweep_transaction_cost_usd:,.2f} round-trip cost = "
            f"${net_yield_gain_usd:,.2f}. Recommendation: '{recommendation}'"
            + (f" ({blocked_reason})." if blocked_reason else ".")
        )

        if is_ratio_exceeded:
            logger.warning(notes)
        else:
            logger.info(notes)

        if not has_spread and state.unallocated_cash > 0:
            logger.info(
                "No yield advantage: cash yield %.2f%% is at or above the %.2f%% benchmark, "
                "so there is no opportunity cost to recover by sweeping.",
                state.cash_yield_pct, state.benchmark_rate_pct,
            )

        return OpportunityCostReport(
            total_capital_usd=round(state.total_capital, 2),
            unallocated_cash_usd=round(state.unallocated_cash, 2),
            idle_capital_ratio_pct=round(idle_ratio_pct, 2),
            gross_opportunity_cost_usd=round(gross_drag_usd, 2),
            drag_basis_points=round(drag_bps, 2),
            net_yield_gain_usd=round(net_yield_gain_usd, 2),
            recommendation=recommendation,
            status=status,
            audit_notes=notes,
            net_benchmark_spread_pct=round(net_spread_pct, 4),
            annualized_drag_basis_points=round(annualized_drag_bps, 2),
            sweepable_cash_usd=round(sweepable_cash, 2),
            operational_buffer_usd=round(cfg.operational_buffer_usd, 2),
            recoverable_yield_usd=round(recoverable_yield_usd, 2),
            breakeven_sweep_notional_usd=(
                round(breakeven_notional, 2) if breakeven_notional is not None else None
            ),
            sweep_blocked_reason=blocked_reason,
            day_count=cfg.day_count.value,
            accrual_method=cfg.accrual_method.value,
        )
