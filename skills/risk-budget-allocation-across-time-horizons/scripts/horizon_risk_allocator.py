"""
risk-budget-allocation-across-time-horizons: distributes one portfolio volatility
budget across time-horizon sleeves (intraday, short-term, medium-term, long-term).

Each horizon receives a share ``b_h`` of the total risk budget. Following the risk
budgeting definition of Bruder & Roncalli (2012, Sec. 2.1), a risk budget is an
*amount of risk*, and the budgeting constraint is that the sleeve's risk contribution
equals its budget. Denominating risk in annualized volatility:

    budget_implied_vol_target_h = b_h * portfolio_vol_target
    position_size_scalar_h      = budget_implied_vol_target_h / base_annualized_vol_h

so a sleeve that runs at ``base_annualized_vol_h`` at unit sizing runs at exactly its
budgeted volatility after scaling. Note the direction: the scalar is *inverse* to the
sleeve's own volatility, matching `dynamic-position-sizing-based-on-realized-volatility`.
A volatile sleeve is scaled down to fit its budget, not up.

The budgets drive the vol targets. They are not two independent knobs: a per-horizon
vol target declared separately from the budget is unreconcilable with it, and the
resulting risk split is then whatever the sizing happens to produce.

What this module can and cannot claim:

- **No covariance matrix, therefore no Euler decomposition.** A true risk contribution
  is ``RC_h = x_h * dR/dx_h`` and needs the covariance of the sleeves (Bruder &
  Roncalli, Sec. 2.1). This module has only standalone volatilities, so it applies the
  comonotonic (perfect-correlation) convention: because volatility is sub-additive,
  ``sigma(sum of sleeves) <= sum of sleeve volatilities``, with equality only when the
  sleeves are perfectly correlated. Sizing each sleeve to ``b_h * sigma_p`` therefore
  makes the *sum* of sleeve volatilities equal ``sigma_p``, and realized portfolio
  volatility at most ``sigma_p``. That is deliberately conservative: it is a ceiling,
  not a forecast, and it credits no diversification between horizons. For budgets that
  use actual correlations, see `strategy-specific-vs-shared-risk-budget-allocation`.
- **Ex-ante only.** ``base_annualized_vol`` is an estimate the caller supplies. If the
  sleeve's realized volatility exceeds it, every downstream number is understated. This
  module does not measure volatility and cannot detect a stale input.
- **Advisory report, not an enforcement gate.** ``allocate_risk_budget`` returns breach
  flags; it does not block trading. The caller must gate on ``over_allocated`` and
  ``drawdown_over_allocated``.
- **Holding-period volatility is a square-root-of-time diagnostic.** It assumes iid
  returns and constant volatility. Under jumps the rule systematically understates risk,
  and the understatement worsens with the horizon (Danielsson & Zigrand, 2006). Treat
  ``holding_period_vol`` as an order-of-magnitude sanity check on drawdown limits, never
  as a risk figure in its own right.

Units are mixed by design and validated at the door: ``*_pct`` fields are percentages
on a 0-100 scale, volatilities are fractions (0.15 = 15% annualized).
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

#: Default total portfolio annualized volatility budget (15%).
DEFAULT_PORTFOLIO_VOL_TARGET = 0.15

#: Default observations per year used to scale an annualized volatility down to a
#: holding period. 252 is the conventional US equity trading-day count; set it to the
#: session count of the market actually traded.
DEFAULT_TRADING_DAYS_PER_YEAR = 252

#: Tolerance, in percentage points, applied to the 100% cap. Two-decimal percentages
#: are not exactly representable in binary floating point, so a set of allocations
#: intended to total exactly 100% can sum to 100.00000000000001 and read as a breach.
#: 1e-9 pp is far below any economically meaningful allocation and far above that
#: representation error.
ALLOCATION_TOLERANCE_PCT = 1e-9

#: Upper bound on an accepted annualized volatility (300%). Exists to catch the
#: percent/fraction mix-up (passing 15 where 0.15 is meant), which would otherwise
#: silently produce a 100x position-size scalar. It is an engineering guard chosen to
#: sit above even crypto-perpetual sleeve volatility, not a published limit.
MAX_PLAUSIBLE_ANNUALIZED_VOL = 3.0

#: Every status string ``allocate_risk_budget`` can emit.
VALID_STATUSES = (
    "RISK_BUDGET_VALID",
    "RISK_BUDGET_OVER_ALLOCATED",
    "DRAWDOWN_BUDGET_OVER_ALLOCATED",
    "RISK_AND_DRAWDOWN_OVER_ALLOCATED",
)


@dataclass
class TimeHorizonBucket:
    """
    One time-horizon sleeve of the portfolio.

    Attributes:
        horizon_label: Unique sleeve identifier, e.g. 'INTRADAY', 'SHORT_TERM'.
        holding_period_days: Typical holding period in trading days, >= 1.
        allocated_risk_pct: Share of the total portfolio risk budget, in percent,
            in (0, 100].
        base_annualized_vol: The sleeve's ex-ante annualized volatility at unit
            (scalar = 1.0) sizing, as a fraction. This is an *input estimate of what
            the sleeve does*, not a target - the target is derived from the budget.
        max_drawdown_limit_pct: The sleeve's own maximum drawdown limit, in percent
            of portfolio equity, in (0, 100].
    """
    horizon_label: str
    holding_period_days: int
    allocated_risk_pct: float
    base_annualized_vol: float
    max_drawdown_limit_pct: float


@dataclass
class HorizonAllocation:
    """
    Per-horizon output.

    ``is_within_limits`` answers only "can this horizon breach the portfolio drawdown
    limit on its own?". It is ``None`` when no portfolio drawdown limit was configured
    (the check did not run), and it says nothing about whether the portfolio total is
    within budget - that is ``RiskBudgetAllocationReport.over_allocated``.
    """
    horizon_label: str
    holding_period_days: int
    risk_budget_pct: float
    budget_implied_vol_target: float     # b_h * portfolio_vol_target
    base_annualized_vol: float           # caller-supplied sleeve vol at unit sizing
    position_size_scalar: float          # budget_implied_vol_target / base_annualized_vol
    holding_period_vol: float            # sqrt-time diagnostic, see module docstring
    max_drawdown_limit_pct: float
    drawdown_limit_below_one_sigma: bool
    is_within_limits: Optional[bool]


@dataclass
class RiskBudgetAllocationReport:
    total_risk_budget_pct: float         # exact sum, never rounded - see audit_notes
    unallocated_risk_pct: float          # 100 - total, clamped at 0
    horizon_allocations: List[HorizonAllocation]
    total_horizons: int
    over_allocated: bool
    under_allocated: bool                # informational, not a breach
    total_drawdown_limit_pct: float
    drawdown_over_allocated: bool
    portfolio_vol_target: float
    status: str                          # one of VALID_STATUSES
    audit_notes: str


def _require_number(value: object, name: str, context: str) -> float:
    """Rejects non-numeric, boolean and non-finite values before any arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{context}: {name} must be numeric, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{context}: {name} is non-finite ({value}). A NaN allocation compares False "
            "against the 100% cap and would pass an over-allocated budget as valid."
        )
    return numeric


def _require_pct(value: object, name: str, context: str) -> float:
    """Validates a percentage on the 0-100 scale, exclusive of zero."""
    numeric = _require_number(value, name, context)
    if not 0.0 < numeric <= 100.0:
        raise ValueError(
            f"{context}: {name} must be in (0, 100] percent, got {numeric}. A negative "
            "allocation offsets a positive one and silently defeats the total cap."
        )
    return numeric


def _require_vol(value: object, name: str, context: str) -> float:
    """Validates an annualized volatility expressed as a fraction."""
    numeric = _require_number(value, name, context)
    if numeric <= 0.0:
        raise ValueError(
            f"{context}: {name} must be > 0, got {numeric}. A zero volatility divides to "
            "an infinite position-size scalar; a negative one flips the position short."
        )
    if numeric > MAX_PLAUSIBLE_ANNUALIZED_VOL:
        raise ValueError(
            f"{context}: {name} is {numeric}, above the {MAX_PLAUSIBLE_ANNUALIZED_VOL} "
            "sanity bound. Volatilities are fractions, not percentages - pass 0.15 for "
            "15% annualized, not 15."
        )
    return numeric


class RiskBudgetAllocationEngine:
    """
    Distributes a portfolio volatility budget across time-horizon sleeves and audits
    the result against the 100% risk cap and an optional portfolio drawdown cap.

    Args:
        total_portfolio_vol_target: Total portfolio annualized volatility budget as a
            fraction (0.15 = 15%).
        portfolio_max_drawdown_limit_pct: Optional portfolio-level maximum drawdown, in
            percent. When supplied, the sum of per-horizon drawdown limits is checked
            against it under the comonotonic assumption that horizons can draw down
            together - the skill's own first pitfall. Omit it and no drawdown check runs.
        trading_days_per_year: Observations per year used for the square-root-of-time
            holding-period diagnostic.

    Raises:
        TypeError, ValueError: on any invalid configuration value.
    """

    def __init__(
        self,
        total_portfolio_vol_target: float = DEFAULT_PORTFOLIO_VOL_TARGET,
        portfolio_max_drawdown_limit_pct: Optional[float] = None,
        trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
    ) -> None:
        context = "RiskBudgetAllocationEngine"
        self.total_portfolio_vol_target: float = _require_vol(
            total_portfolio_vol_target, "total_portfolio_vol_target", context
        )
        self.portfolio_max_drawdown_limit_pct: Optional[float]
        if portfolio_max_drawdown_limit_pct is None:
            self.portfolio_max_drawdown_limit_pct = None
        else:
            self.portfolio_max_drawdown_limit_pct = _require_pct(
                portfolio_max_drawdown_limit_pct, "portfolio_max_drawdown_limit_pct", context
            )
        if isinstance(trading_days_per_year, bool) or not isinstance(trading_days_per_year, int):
            raise TypeError(
                f"{context}: trading_days_per_year must be an int, "
                f"got {type(trading_days_per_year).__name__}."
            )
        if trading_days_per_year < 1:
            raise ValueError(
                f"{context}: trading_days_per_year must be >= 1, got {trading_days_per_year}."
            )
        self.trading_days_per_year: int = trading_days_per_year

    def _validate_buckets(
        self, buckets: Optional[Sequence[TimeHorizonBucket]]
    ) -> List[TimeHorizonBucket]:
        """
        Validates the whole bucket set before any allocation arithmetic runs.

        Rejects rather than repairs: an unusable horizon definition is a configuration
        error, not a risk state, and returning a report for it would hand the caller a
        risk verdict computed from numbers nobody intended.
        """
        if buckets is None:
            raise ValueError("allocate_risk_budget: buckets is required, got None.")
        validated = list(buckets)
        if not validated:
            raise ValueError(
                "allocate_risk_budget: buckets is empty. An empty horizon set has no risk "
                "budget to validate; reporting it as valid would assert a control that ran "
                "over nothing."
            )
        seen: Set[str] = set()
        for index, bucket in enumerate(validated):
            if not isinstance(bucket, TimeHorizonBucket):
                raise TypeError(
                    f"allocate_risk_budget: buckets[{index}] must be a TimeHorizonBucket, "
                    f"got {type(bucket).__name__}."
                )
            context = f"horizon[{index}]"
            label = bucket.horizon_label
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"{context}: horizon_label must be a non-empty string.")
            key = label.strip()
            if key in seen:
                raise ValueError(
                    f"{context}: duplicate horizon_label {key!r}. Duplicates are summed "
                    "into the total twice while appearing as one sleeve in the report."
                )
            seen.add(key)

            days = bucket.holding_period_days
            if isinstance(days, bool) or not isinstance(days, int):
                raise TypeError(
                    f"{context} ({key}): holding_period_days must be an int, "
                    f"got {type(days).__name__}."
                )
            if days < 1:
                raise ValueError(
                    f"{context} ({key}): holding_period_days must be >= 1, got {days}."
                )
            _require_pct(bucket.allocated_risk_pct, "allocated_risk_pct", f"{context} ({key})")
            _require_vol(bucket.base_annualized_vol, "base_annualized_vol", f"{context} ({key})")
            _require_pct(
                bucket.max_drawdown_limit_pct, "max_drawdown_limit_pct", f"{context} ({key})"
            )
        return validated

    def allocate_risk_budget(
        self,
        buckets: Optional[Sequence[TimeHorizonBucket]],
    ) -> RiskBudgetAllocationReport:
        """
        Allocates the portfolio volatility budget across the supplied horizons.

        Every horizon is sized so its standalone volatility equals its share of the
        portfolio budget. When the shares total 100%, the sleeve volatilities sum to
        exactly ``total_portfolio_vol_target`` - the comonotonic ceiling on realized
        portfolio volatility.

        Horizons are reported in input order, so the caller controls report ordering.

        Raises:
            TypeError: a field has the wrong type.
            ValueError: a field is out of range, non-finite, duplicated, or the bucket
                list is empty.
        """
        validated = self._validate_buckets(buckets)
        sigma_p = self.total_portfolio_vol_target
        portfolio_dd = self.portfolio_max_drawdown_limit_pct
        period_scale_denominator = float(self.trading_days_per_year)

        allocations: List[HorizonAllocation] = []
        for bucket in validated:
            budget_fraction = bucket.allocated_risk_pct / 100.0
            budget_implied_vol_target = budget_fraction * sigma_p
            scalar = budget_implied_vol_target / bucket.base_annualized_vol
            holding_period_vol = budget_implied_vol_target * math.sqrt(
                bucket.holding_period_days / period_scale_denominator
            )
            allocations.append(
                HorizonAllocation(
                    horizon_label=bucket.horizon_label,
                    holding_period_days=bucket.holding_period_days,
                    risk_budget_pct=bucket.allocated_risk_pct,
                    budget_implied_vol_target=budget_implied_vol_target,
                    base_annualized_vol=bucket.base_annualized_vol,
                    position_size_scalar=scalar,
                    holding_period_vol=holding_period_vol,
                    max_drawdown_limit_pct=bucket.max_drawdown_limit_pct,
                    drawdown_limit_below_one_sigma=(
                        bucket.max_drawdown_limit_pct / 100.0 < holding_period_vol
                    ),
                    is_within_limits=(
                        None if portfolio_dd is None
                        else bucket.max_drawdown_limit_pct <= portfolio_dd
                    ),
                )
            )

        # fsum, not a running total: the exact sum is independent of bucket order, so two
        # callers passing the same horizons in different orders cannot get different
        # verdicts on a borderline budget.
        total_pct = math.fsum(b.allocated_risk_pct for b in validated)
        total_dd_pct = math.fsum(b.max_drawdown_limit_pct for b in validated)

        over_allocated = total_pct > 100.0 + ALLOCATION_TOLERANCE_PCT
        under_allocated = total_pct < 100.0 - ALLOCATION_TOLERANCE_PCT
        drawdown_over_allocated = (
            portfolio_dd is not None and total_dd_pct > portfolio_dd + ALLOCATION_TOLERANCE_PCT
        )

        if over_allocated and drawdown_over_allocated:
            status = "RISK_AND_DRAWDOWN_OVER_ALLOCATED"
        elif over_allocated:
            status = "RISK_BUDGET_OVER_ALLOCATED"
        elif drawdown_over_allocated:
            status = "DRAWDOWN_BUDGET_OVER_ALLOCATED"
        else:
            status = "RISK_BUDGET_VALID"

        drawdown_note = (
            f" vs Portfolio Limit = {portfolio_dd:.12g}%."
            if portfolio_dd is not None
            else " (no portfolio drawdown limit configured; drawdown check not run)."
        )
        notes = (
            f"RISK BUDGET ALLOCATION [{status}]: "
            f"Horizons = {len(validated)}, Total Allocated = {total_pct:.12g}%, "
            f"Unallocated = {max(0.0, 100.0 - total_pct):.12g}%, "
            f"Portfolio Vol Target = {sigma_p:.2%}, "
            f"Sum of Horizon Drawdown Limits = {total_dd_pct:.12g}%" + drawdown_note
        )

        if over_allocated or drawdown_over_allocated:
            logger.warning(notes)
        else:
            logger.info(notes)
            if under_allocated:
                logger.info(
                    "Risk budget is under-allocated by %.12g%%: that share of the portfolio "
                    "volatility budget is unused, not unavailable.",
                    100.0 - total_pct,
                )

        return RiskBudgetAllocationReport(
            total_risk_budget_pct=total_pct,
            unallocated_risk_pct=max(0.0, 100.0 - total_pct),
            horizon_allocations=allocations,
            total_horizons=len(validated),
            over_allocated=over_allocated,
            under_allocated=under_allocated,
            total_drawdown_limit_pct=total_dd_pct,
            drawdown_over_allocated=drawdown_over_allocated,
            portfolio_vol_target=sigma_p,
            status=status,
            audit_notes=notes,
        )
