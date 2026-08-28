"""
strategy-specific-vs-shared-risk-budget-allocation: dual-tier risk limit audit
for a multi-strategy book.

Two limits are enforced per strategy, and conflating them is the main way risk
budgeting goes wrong:

**Strategy-specific (standalone) volatility limit**
    A cap on the strategy's *own* return volatility, sigma_i = sqrt(Sigma_ii),
    annualized. This is a property of the strategy's return series alone. It
    ignores correlation entirely, so it will flag a high-volatility strategy that
    is in fact the book's best diversifier.

**Shared risk budget (component risk contribution)**
    A cap on the share of *portfolio* volatility the strategy accounts for once
    correlation is taken into account. Computed by Euler allocation.

Euler decomposition (Tasche 2008; Maillard, Roncalli and Teiletche 2009, Sec. 2)::

    sigma_p = sqrt(w' Sigma w)                  portfolio volatility
    MCR_i   = d sigma_p / d w_i = (Sigma w)_i / sigma_p
    RC_i    = w_i * MCR_i                       risk contribution, sums to sigma_p
    c_i     = RC_i / sigma_p                    contribution *share*, sums to 1

sigma is homogeneous of degree 1 in w, so by Euler's theorem sum_i RC_i = sigma_p
exactly, with no residual. ``c_i`` is also the Component-VaR share: because
parametric VaR is a fixed multiple of sigma_p, Component VaR_i = c_i * VaR_p
(Jorion, *Value at Risk*, 3rd ed., Ch. 7).

Terminology warning
-------------------
``c_i`` is the **Component VaR** share. It is *not* CVaR. In the risk literature
CVaR means Conditional Value-at-Risk (a.k.a. Expected Shortfall / average
value-at-risk) -- a tail-average loss measure, which this module does not compute.
Do not abbreviate Component VaR as "CVaR" in reports produced from this engine.

Recommended adjustment factors
------------------------------
Two factors are produced, and they act on different things:

``shared_budget_capital_factor``
    Multiply the strategy's *target capital* by this to bring its component risk
    share down to its budget. Component risk share is **not** linear in the
    capital weight -- it is roughly quadratic for a dominant strategy, and scaling
    one strategy renormalizes every other weight -- so the naive
    ``budget / actual`` ratio does not work. On a 70/30 book with
    Sigma = [[4e-4, 2e-5], [2e-5, 1e-4]], the dominant strategy's share is 93.8%;
    ``budget / actual = 0.4264`` and applying it leaves the share at **77.6%**,
    not the 40% budget. This module therefore *solves* for the factor by bisection
    on the exact share function and returns the conservative (lower) bracket, so
    the resulting share is <= budget.

``standalone_delever_factor``
    ``limit / sigma_i``. This must be applied by de-levering the strategy's
    positions, **not** by reallocating capital in this engine: sigma_i is read
    from the covariance diagonal and is invariant to capital weights, so
    re-running the engine with reduced capital reports the identical standalone
    breach forever. A standalone breach is a gate on the strategy, not something
    the allocator can fix.

Both factors are computed one strategy at a time, holding all others fixed. When
several strategies breach at once, applying every factor simultaneously does not
land them all on budget -- re-run the engine and iterate until
``breached_strategies`` is empty.

Limitations (documented, deliberate)
------------------------------------
- **Ex-ante and only as good as Sigma.** Every figure is a function of the caller's
  covariance matrix. The engine does not estimate it, does not know it is stale,
  and does not know that strategy correlations rise under stress. A book balanced
  on a calm-period Sigma is not balanced in the drawdown the budget existed for.
- **Parametric (variance-covariance) VaR.** Gaussian, zero-mean. Strategy returns
  are fat-tailed and often skewed, so this understates tail loss. It is not an
  Expected Shortfall and says nothing about losses beyond the quantile.
- **Square-root-of-time scaling.** VaR is scaled from the daily covariance by
  sqrt(horizon). Danielsson and Zigrand (2006) show this rule *systematically
  underestimates* risk under jump diffusion, and that the underestimation worsens
  with the horizon and the confidence level. The default horizon here is 252 days
  (one year), which is the most aggressive use of the rule, not the least. Treat
  the annual figure as a capital-planning number and set ``var_horizon_days=1``
  for anything used as a monitoring limit.
- **Volatility, not drawdown or tail risk.** Equal volatility contribution is not
  equal tail contribution. A short-gamma or carry strategy contributes little
  volatility right up to the point it contributes all of the loss. Pair with
  ``kill-switch-and-drawdown-circuit-breakers``.
- **No transaction costs, turnover limits or weight bounds.** The audit is
  recomputed from scratch each call; whether a reallocation is worth paying for is
  the caller's decision.

References
----------
Tasche, D. (2008), "Capital Allocation to Business Units and Sub-Portfolios: the
    Euler Principle", arXiv:0708.2542.
Maillard, S., Roncalli, T. and Teiletche, J. (2009), "On the properties of
    equally-weighted risk contributions portfolios", published in Journal of
    Portfolio Management 36(4), 2010, pp. 60-70.
Danielsson, J. and Zigrand, J.-P. (2006), "On time-scaling of risk and the
    square-root-of-time rule", Journal of Banking & Finance 30(10), pp. 2701-2713.
Jorion, P. (2007), Value at Risk: The New Benchmark for Managing Financial Risk,
    3rd ed., McGraw-Hill, Ch. 7 (marginal, incremental and component VaR).
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Z-score for a one-tailed 95% parametric VaR. The exact standard-normal
#: quantile is 1.6448536269514722; 1.645 is the conventional rounding and is
#: retained as the default (the 0.009% difference is immaterial next to the
#: normality assumption itself).
DEFAULT_CONFIDENCE_Z = 1.645

#: Trading days per year used to annualize a daily covariance matrix. 252 is the
#: US equity convention; other venues differ (NSE ~250, crypto 365). It is a
#: convention, not a standard -- set it to match how Sigma was estimated. If Sigma
#: is *already* annualized, pass ``trading_days_per_year=1`` and
#: ``var_horizon_days=1``; otherwise the volatility is annualized twice and every
#: reported figure is ~15.9x too large. The engine cannot detect this from the
#: numbers alone, so the unit convention is the caller's responsibility.
DEFAULT_TRADING_DAYS_PER_YEAR = 252

#: VaR horizon in trading days. Defaults to one year to match the annualized
#: volatility figure reported alongside it. See the square-root-of-time
#: limitation in the module docstring before using this as a monitoring limit.
DEFAULT_VAR_HORIZON_DAYS = 252

#: Tolerance on |sum_i c_i - 1|. The identity holds algebraically, so this
#: detects numerical degeneracy (NaN propagation, a near-zero sigma_p), not a
#: modelling error. See ``is_euler_decomposition_valid``.
EULER_IDENTITY_TOLERANCE = 1e-4

#: Relative tolerance for the Sigma symmetry check.
COVARIANCE_SYMMETRY_TOLERANCE = 1e-9

#: Minimum eigenvalue of Sigma relative to its largest, i.e. a reciprocal
#: condition-number floor. A strict ``min_eig > 0`` test does not detect
#: singularity in floating point: two perfectly correlated strategies leave a
#: smallest eigenvalue around 1e-20 rather than exactly zero.
COVARIANCE_MIN_EIGENVALUE_RATIO = 1e-12

#: Bisection settings for the shared-budget capital factor.
SCALE_SOLVER_TOLERANCE = 1e-12
SCALE_SOLVER_MAX_ITERATIONS = 200
MIN_SCALE_FACTOR = 1e-9


def _floor_to(value: float, decimals: int) -> float:
    """
    Round ``value`` *down* to ``decimals`` places.

    Every reported scaling factor is floored rather than rounded: ordinary
    rounding can move the factor above the solved root -- 0.17142857 rounds to
    0.171429 -- and the strategy then sits fractionally above its budget after
    the recommendation is applied.
    """
    shift = 10.0 ** decimals
    return math.floor(value * shift) / shift


@dataclass
class Config:
    """Legacy Config container for backward compatibility."""

    enabled: bool = True


class Engine:
    """Legacy Engine class for backward compatibility."""

    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled


@dataclass
class StrategyRiskBudgetSpec:
    """
    Risk budget declaration for one strategy.

    Args:
        strategy_id: Unique, non-empty identifier.
        target_capital_usd: Capital earmarked for the strategy. Finite and
            strictly positive; a zero or negative allocation is not a budget.
        max_standalone_volatility_pct: Cap on the strategy's own annualized
            return volatility, in percent (``15.0`` means 15%). Correlation-blind.
        max_shared_risk_contribution_pct: Cap on the strategy's share of total
            portfolio volatility, in percent (``30.0`` means 30% of portfolio
            risk). Must be strictly positive.
    """

    strategy_id: str
    target_capital_usd: float
    max_standalone_volatility_pct: float      # Strategy-specific risk limit (e.g. 15%)
    max_shared_risk_contribution_pct: float   # Shared risk budget (e.g. 30% of portfolio risk)

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string.")
        for field_name in (
            "target_capital_usd",
            "max_standalone_volatility_pct",
            "max_shared_risk_contribution_pct",
        ):
            raw = getattr(self, field_name)
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self.strategy_id}: {field_name} must be numeric, got {raw!r}."
                ) from exc
            if not math.isfinite(value):
                raise ValueError(
                    f"{self.strategy_id}: {field_name} must be finite, got {raw!r}."
                )
            if value <= 0.0:
                raise ValueError(
                    f"{self.strategy_id}: {field_name} must be > 0, got {raw!r}. "
                    "A zero or negative limit cannot be satisfied and must not be "
                    "silently clamped."
                )
            setattr(self, field_name, value)


@dataclass
class StrategyRiskBreakdown:
    """Per-strategy result of the dual-tier audit."""

    strategy_id: str
    capital_weight: float
    standalone_volatility_pct: float
    marginal_contribution_to_risk: float          # MCR_i, in daily volatility units
    component_risk_pct: float                     # Share of total portfolio volatility (%)
    standalone_limit_breached: bool
    shared_budget_breached: bool
    recommended_capital_adjustment_factor: float  # min of the two factors below
    #: ``limit / sigma_i``. Apply by de-levering the strategy's positions.
    #: Reallocating capital does NOT change sigma_i and will not clear the breach.
    standalone_delever_factor: float = 1.0
    #: Multiply target capital by this to bring the component risk share to budget.
    #: Solved by bisection on the exact share function, not by ``budget / actual``.
    shared_budget_capital_factor: float = 1.0
    #: True when no capital scaling can satisfy the shared budget (e.g. a
    #: single-strategy book, whose share is 100% at every capital level).
    shared_budget_infeasible_by_scaling: bool = False


@dataclass
class PortfolioRiskBudgetAllocationReport:
    """Portfolio-level result of the dual-tier audit."""

    total_portfolio_capital_usd: float
    total_portfolio_volatility_pct: float
    total_portfolio_var_95_usd: float
    #: Numerical self-check that sum_i c_i == 1. The identity is algebraic, so a
    #: False here means the computation degenerated (NaN, near-zero sigma_p), not
    #: that the risk model is wrong. It is not evidence the covariance matrix is
    #: sound.
    is_euler_decomposition_valid: bool
    strategy_breakdown: Dict[str, StrategyRiskBreakdown]
    breached_strategies: List[str]
    audit_notes: str
    var_horizon_days: int = DEFAULT_VAR_HORIZON_DAYS
    var_confidence_z: float = DEFAULT_CONFIDENCE_Z
    #: False when the declared shared budgets sum to less than 100%, in which case
    #: no allocation can satisfy them all -- component shares always sum to 100%.
    budgets_feasible: bool = True


def _validate_covariance(matrix: Sequence[Sequence[float]], n: int) -> np.ndarray:
    """
    Coerce and validate a daily strategy-return covariance matrix.

    Rejects (rather than repairs) anything that would make the Euler decomposition
    meaningless: wrong shape, non-finite entries, asymmetry, or a matrix that is
    not positive definite. Flooring a bad matrix -- ``max(variance, 1e-8)`` -- turns
    an impossible correlation into a plausible-looking volatility and a passing
    audit, which is strictly worse than an exception.

    Raises:
        ValueError: on any of the above.
    """
    try:
        cov = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Covariance matrix is not numeric: {exc}") from exc

    if cov.ndim != 2 or cov.shape != (n, n):
        raise ValueError(
            f"Covariance matrix shape {cov.shape} does not match {n} strategies."
        )
    if not np.all(np.isfinite(cov)):
        raise ValueError(
            "Covariance matrix contains NaN or infinite entries. A single NaN "
            "propagates to NaN risk figures, and 'NaN > limit' is False, so every "
            "limit check would silently pass."
        )

    scale = float(np.max(np.abs(cov))) or 1.0
    if not np.allclose(cov, cov.T, atol=COVARIANCE_SYMMETRY_TOLERANCE * scale, rtol=0.0):
        raise ValueError("Covariance matrix is not symmetric.")

    # Symmetrize the last bit of floating-point noise before the eigen-decomposition.
    cov = 0.5 * (cov + cov.T)

    eigenvalues = np.linalg.eigvalsh(cov)
    min_eig = float(eigenvalues[0])
    max_eig = float(eigenvalues[-1])
    if max_eig <= 0.0 or min_eig <= COVARIANCE_MIN_EIGENVALUE_RATIO * max_eig:
        raise ValueError(
            f"Covariance matrix is not positive definite (min eigenvalue {min_eig:.3e} "
            f"vs max {max_eig:.3e}). A singular or indefinite Sigma yields a "
            "portfolio variance that is zero or negative, for which volatility, MCR "
            "and every risk share are undefined. Repair the estimate (shrinkage, a "
            "longer sample) rather than flooring the variance."
        )
    return cov


def _component_share_pct(capitals: np.ndarray, cov: np.ndarray, idx: int) -> float:
    """
    Component risk share of strategy ``idx``, in percent.

    ``c_i = w_i (Sigma w)_i / (w' Sigma w)``, which is invariant to the overall
    scale of ``capitals`` -- only the relative split matters.
    """
    weights = capitals / float(np.sum(capitals))
    port_variance = float(weights @ cov @ weights)
    return 100.0 * float(weights[idx] * (cov @ weights)[idx]) / port_variance


def _solve_shared_budget_scale(
    capitals: np.ndarray, cov: np.ndarray, idx: int, budget_pct: float
) -> Tuple[float, bool]:
    """
    Solve for the capital scaling factor that brings strategy ``idx`` to its
    shared risk budget, holding every other strategy's capital fixed.

    Component risk share is not linear in the capital weight, so the customary
    ``budget / actual`` ratio undershoots badly (see the module docstring). It is
    also not guaranteed monotone in the weight: when the strategy is a strong
    hedge (negative covariance with the rest of the book), raising its weight can
    *lower* its share. Bisection only needs a sign change, which is available here
    because the share tends to 0 as the weight tends to 0 while exceeding the
    budget at the current weight.

    Returns:
        ``(factor, infeasible)``. ``factor`` is the conservative lower bracket, so
        the resulting share is <= ``budget_pct``. ``infeasible`` is True when no
        scaling works -- then ``factor`` is 0.0, i.e. the strategy must be removed
        from the book or the budget renegotiated.
    """

    def excess(scale: float) -> float:
        scaled = capitals.copy()
        scaled[idx] = capitals[idx] * scale
        return _component_share_pct(scaled, cov, idx) - budget_pct

    if excess(1.0) <= 0.0:
        # The caller's breach flag and this solver compute the share by slightly
        # different float paths, so they can disagree by an ulp for a strategy
        # sitting exactly on its budget. No bracket exists; no reduction is due.
        return 1.0, False
    if excess(MIN_SCALE_FACTOR) > 0.0:
        return 0.0, True

    low, high = MIN_SCALE_FACTOR, 1.0
    for _ in range(SCALE_SOLVER_MAX_ITERATIONS):
        if high - low <= SCALE_SOLVER_TOLERANCE:
            break
        mid = 0.5 * (low + high)
        if excess(mid) > 0.0:
            high = mid
        else:
            low = mid
    return low, False


class StrategySpecificVsSharedRiskBudgetEngine:
    """
    Dual-tier risk budget audit: strategy-specific standalone volatility limits and
    shared portfolio risk-contribution budgets, decomposed by Euler allocation.

    The engine validates its inputs and raises rather than repairing them. See the
    module docstring for the distinction between the two limits, for why the
    shared-budget factor is solved rather than divided, and for the standing
    limitations of parametric VaR.
    """

    def __init__(
        self,
        strategy_specs: List[StrategyRiskBudgetSpec],
        confidence_level_z: float = DEFAULT_CONFIDENCE_Z,
        trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
        var_horizon_days: int = DEFAULT_VAR_HORIZON_DAYS,
    ) -> None:
        if not strategy_specs:
            raise ValueError("At least one strategy spec is required.")

        seen: set = set()
        for spec in strategy_specs:
            if spec.strategy_id in seen:
                raise ValueError(
                    f"Duplicate strategy_id {spec.strategy_id!r}. Deduplicating "
                    "silently would drop one strategy's capital from the book."
                )
            seen.add(spec.strategy_id)

        if not math.isfinite(confidence_level_z) or confidence_level_z <= 0.0:
            raise ValueError(
                f"confidence_level_z must be finite and > 0, got {confidence_level_z!r}."
            )
        if trading_days_per_year <= 0:
            raise ValueError(
                f"trading_days_per_year must be > 0, got {trading_days_per_year!r}."
            )
        if var_horizon_days <= 0:
            raise ValueError(f"var_horizon_days must be > 0, got {var_horizon_days!r}.")

        self.specs: Dict[str, StrategyRiskBudgetSpec] = {
            s.strategy_id: s for s in strategy_specs
        }
        self.z_score = float(confidence_level_z)
        self.trading_days_per_year = int(trading_days_per_year)
        self.var_horizon_days = int(var_horizon_days)

    def _validate_ids(self, strategy_ids_order: Sequence[str]) -> List[str]:
        """
        Check that ``strategy_ids_order`` is a permutation of the registered specs.

        A missing id would drop that strategy's capital from the portfolio and
        under-report total risk; a duplicate id would count one strategy's capital
        twice. Both are silent in an unvalidated implementation.
        """
        ids = list(strategy_ids_order)
        if not ids:
            raise ValueError("strategy_ids_order must not be empty.")

        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        if duplicates:
            raise ValueError(
                f"Duplicate ids in strategy_ids_order: {duplicates}. Each duplicate "
                "double-counts that strategy's capital."
            )

        unknown = sorted(set(ids) - set(self.specs))
        if unknown:
            raise ValueError(f"Unknown strategy ids: {unknown}.")

        missing = sorted(set(self.specs) - set(ids))
        if missing:
            raise ValueError(
                f"strategy_ids_order omits registered strategies: {missing}. Their "
                "capital would be excluded from the portfolio and total risk "
                "under-reported."
            )
        return ids

    def evaluate_risk_budgets(
        self,
        strategy_returns_cov_matrix: Sequence[Sequence[float]],
        strategy_ids_order: Sequence[str],
    ) -> PortfolioRiskBudgetAllocationReport:
        """
        Run the dual-tier audit.

        Args:
            strategy_returns_cov_matrix: N x N covariance matrix of **daily**
                strategy returns, ordered to match ``strategy_ids_order``. Must be
                finite, symmetric and positive definite.
            strategy_ids_order: Every registered ``strategy_id`` exactly once, in
                the row/column order of the covariance matrix.

        Returns:
            A :class:`PortfolioRiskBudgetAllocationReport`.

        Raises:
            ValueError: on any invalid input (see ``_validate_covariance`` and
                ``_validate_ids``). Nothing is silently repaired.
        """
        ids = self._validate_ids(strategy_ids_order)
        n = len(ids)
        cov = _validate_covariance(strategy_returns_cov_matrix, n)

        capitals = np.array(
            [self.specs[sid].target_capital_usd for sid in ids], dtype=float
        )
        total_capital = float(np.sum(capitals))
        if not math.isfinite(total_capital) or total_capital <= 0.0:
            # Each spec is individually finite and positive, but the sum can still
            # overflow to infinity, which would silently produce zero weights.
            raise ValueError(
                f"Total portfolio capital must be finite and > 0, got {total_capital!r}."
            )

        weights = capitals / total_capital

        # Portfolio volatility. Sigma is positive definite and w != 0, so the
        # variance is strictly positive -- no floor is needed, and none is applied.
        port_variance = float(weights @ cov @ weights)
        if not math.isfinite(port_variance) or port_variance <= 0.0:
            raise ValueError(
                f"Portfolio variance is {port_variance!r}; volatility, MCR and every "
                "risk share are undefined. This is not floored to a small positive "
                "number -- doing so would manufacture a plausible volatility from a "
                "degenerate input."
            )
        port_vol = math.sqrt(port_variance)

        annual_scale = math.sqrt(self.trading_days_per_year)
        ann_port_vol_pct = port_vol * annual_scale * 100.0
        port_var_95_usd = (
            total_capital * port_vol * math.sqrt(self.var_horizon_days) * self.z_score
        )

        # Standalone volatility is read from the covariance diagonal alone: it is a
        # property of the strategy's returns and is invariant to capital weights.
        standalone_vols = np.sqrt(np.diag(cov)) * annual_scale * 100.0

        # Euler allocation.
        mcr = (cov @ weights) / port_vol
        component_risk_frac = (weights * mcr) / port_vol
        component_risk_pct = component_risk_frac * 100.0

        euler_valid = (
            abs(float(np.sum(component_risk_frac)) - 1.0) < EULER_IDENTITY_TOLERANCE
        )

        budget_sum = sum(self.specs[sid].max_shared_risk_contribution_pct for sid in ids)
        budgets_feasible = budget_sum >= 100.0 - EULER_IDENTITY_TOLERANCE
        if not budgets_feasible:
            logger.warning(
                "Shared risk budgets sum to %.2f%%, below 100%%. Component shares "
                "always sum to 100%%, so no allocation can satisfy every budget.",
                budget_sum,
            )

        breakdown_dict: Dict[str, StrategyRiskBreakdown] = {}
        breached: List[str] = []

        for idx, sid in enumerate(ids):
            spec = self.specs[sid]
            stand_vol = float(standalone_vols[idx])
            comp_risk = float(component_risk_pct[idx])

            # Strict '>': a strategy sitting exactly on its limit is compliant.
            stand_breach = stand_vol > spec.max_standalone_volatility_pct
            shared_breach = comp_risk > spec.max_shared_risk_contribution_pct

            delever_factor = 1.0
            if stand_breach:
                delever_factor = spec.max_standalone_volatility_pct / stand_vol

            capital_factor = 1.0
            infeasible = False
            if shared_breach:
                capital_factor, infeasible = _solve_shared_budget_scale(
                    capitals, cov, idx, spec.max_shared_risk_contribution_pct
                )

            delever_factor = _floor_to(delever_factor, 6)
            capital_factor = _floor_to(capital_factor, 6)
            adj_factor = _floor_to(min(delever_factor, capital_factor), 4)

            if stand_breach or shared_breach:
                breached.append(sid)

            breakdown_dict[sid] = StrategyRiskBreakdown(
                strategy_id=sid,
                capital_weight=round(float(weights[idx]), 4),
                standalone_volatility_pct=round(stand_vol, 2),
                marginal_contribution_to_risk=round(float(mcr[idx]), 6),
                component_risk_pct=round(comp_risk, 2),
                standalone_limit_breached=stand_breach,
                shared_budget_breached=shared_breach,
                recommended_capital_adjustment_factor=adj_factor,
                standalone_delever_factor=delever_factor,
                shared_budget_capital_factor=capital_factor,
                shared_budget_infeasible_by_scaling=infeasible,
            )

        notes = (
            f"EULER RISK ALLOCATION: Total Capital = ${total_capital:,.2f}, "
            f"Total Volatility = {ann_port_vol_pct:.2f}% annualized, "
            f"{self.var_horizon_days}-day 95% parametric VaR = ${port_var_95_usd:,.2f}, "
            f"Euler Identity Valid = {euler_valid}, "
            f"Budgets Feasible = {budgets_feasible}, "
            f"Breached Strategies = {breached}."
        )

        if breached or not budgets_feasible:
            logger.warning(notes)
        else:
            logger.info(notes)

        return PortfolioRiskBudgetAllocationReport(
            total_portfolio_capital_usd=round(total_capital, 2),
            total_portfolio_volatility_pct=round(ann_port_vol_pct, 2),
            total_portfolio_var_95_usd=round(port_var_95_usd, 2),
            is_euler_decomposition_valid=euler_valid,
            strategy_breakdown=breakdown_dict,
            breached_strategies=breached,
            audit_notes=notes,
            var_horizon_days=self.var_horizon_days,
            var_confidence_z=self.z_score,
            budgets_feasible=budgets_feasible,
        )
