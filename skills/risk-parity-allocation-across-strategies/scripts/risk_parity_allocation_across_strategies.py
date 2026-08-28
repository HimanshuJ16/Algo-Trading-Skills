"""
risk-parity-allocation-across-strategies: capital allocation across trading
strategies by Equal Risk Contribution (ERC) or inverse volatility.

Two allocation methods, and the difference between them is the whole point of this
module:

``INVERSE_VOLATILITY``
    Closed form, w_i = (1/sigma_i) / sum_j(1/sigma_j). This is the *exact* ERC
    solution only when every pairwise correlation is the same (Maillard, Roncalli
    and Teiletche 2009, Eq. 3; the n = 2 case is independent of rho). Under any
    other correlation structure it is a heuristic, and the risk contributions it
    produces are not equal.

``EQUAL_RISK_CONTRIBUTION``
    Solves w_i * (Sigma w)_i = b_i * sigma(w) for equal budgets b_i = 1/n, so every
    strategy contributes the same share of portfolio volatility whatever the
    correlation structure. Solved with the cyclical coordinate descent (CCD)
    algorithm of Griveau-Billion, Richard and Roncalli (2013), Eq. 4:

        w_i* = [ -beta_i + sqrt(beta_i^2 + 4 * sigma_i^2 * b_i * sigma(w)) ]
               / (2 * sigma_i^2)
        beta_i = sum_{j != i} Sigma_ij * w_j

    iterated coordinate-wise until the weights stop moving, then rescaled to sum
    to 1 (the fixed point of Eq. 4 is defined up to scale; MRT 2009 Appendix A.2).
    Starting from strictly positive weights keeps every iterate strictly positive,
    so the solution is long-only by construction.

How much the two disagree is not a rounding detail. On the four-asset example of
MRT (2009) Sec. 4.1 -- volatilities 10/20/30/40% with correlations rho_12 = 0.8,
rho_34 = -0.5 and zero elsewhere -- inverse volatility gives weights of
48/24/16/12% and risk contributions of 39.1/39.1/10.9/10.9% against a 25% target:
the two correlated strategies carry 78% of portfolio risk. The ERC solution for
the same covariance matrix is 38.4/19.2/24.3/18.2%, contributing 25% each.

Risk decomposition follows the Euler allocation used throughout that literature:

    MCR_i = (Sigma w)_i / sigma(w)          (marginal contribution to risk)
    RC_i  = w_i * MCR_i                     (risk contribution, sums to sigma(w))
    c_i   = RC_i / sigma(w)                 (risk contribution *share*, sums to 1)

Limitations (documented, deliberate):

- **Ex-ante only, and only as good as Sigma.** Every number here is a function of
  the covariance matrix the caller supplies. Risk parity does not estimate it, does
  not know it is stale, and does not know that strategy correlations rise in a
  crisis. A portfolio balanced on a calm-period covariance is not balanced during
  the drawdown it was meant to survive.
- **Volatility is the only risk measure.** Two strategies contributing equal
  volatility do not contribute equal tail risk; a short-gamma strategy looks calm
  right up to the point it does not. Pair with an independent drawdown circuit
  breaker.
- **No leverage and no volatility target.** Weights sum to 1.0 -- this allocates a
  fixed pool of capital, it does not lever the portfolio up to a target volatility.
- **No transaction costs, turnover limits or weight bounds.** The allocation is
  computed from scratch each call; the caller decides whether the move is worth
  paying for.
- **Allocated capital is rounded to the cent**, so the allocations sum to the
  capital pool only to within half a cent per strategy. Reconcile the residual;
  do not assume the figures tie out exactly.
- **Sigma must be positive definite.** The CCD convergence proof (Tseng 2001,
  Thm 5.1, as invoked by Griveau-Billion et al. Remark 2) needs a strictly convex
  objective. A singular or indefinite matrix is rejected rather than silently
  producing a number.

References
----------
Maillard, S., Roncalli, T. and Teiletche, J. (2009), "On the properties of
    equally-weighted risk contributions portfolios", published as "The Properties
    of Equally Weighted Risk Contribution Portfolios", Journal of Portfolio
    Management 36(4), 2010, pp. 60-70. DOI 10.3905/jpm.2010.36.4.060.
Griveau-Billion, T., Richard, J.-C. and Roncalli, T. (2013), "A Fast Algorithm for
    Computing High-dimensional Risk Parity Portfolios", arXiv:1311.4057.
"""
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Default absolute gate, in percentage *points* of risk-contribution share.
DEFAULT_MAX_ABSOLUTE_ERROR_PCT = 5.0

#: Default relative gate, |RC_i - target| / target as a percentage. The absolute
#: gate alone is vacuous for a large book: with 20 strategies the equal share is
#: 5.00pp, so a strategy contributing *zero* risk sits exactly 5.00pp from target
#: and passes. The relative gate is scale-free and catches that.
DEFAULT_MAX_RELATIVE_ERROR_PCT = 5.0

#: Convergence tolerance for the CCD solver: max absolute weight change per sweep.
DEFAULT_SOLVER_TOLERANCE = 1e-10

#: Sweep budget for the CCD solver. Griveau-Billion et al. (2013) report
#: convergence in tens of sweeps even for n > 500; this is a generous ceiling.
DEFAULT_SOLVER_MAX_ITERATIONS = 1000

#: Tolerance for Sigma symmetry and for the Sigma-diagonal vs declared-volatility
#: cross-check, as a relative deviation.
COVARIANCE_CONSISTENCY_TOLERANCE = 1e-6
VOLATILITY_CONSISTENCY_TOLERANCE = 0.01

#: Minimum Cholesky pivot, relative to the corresponding diagonal entry. A strict
#: ``pivot > 0`` test does not detect singularity in floating point: two perfectly
#: correlated strategies produce a final pivot of ~2e-18 rather than exactly zero,
#: which then reaches the solver as a near-singular system.
CHOLESKY_PIVOT_TOLERANCE = 1e-12


class AllocationMethod(str, Enum):
    """Weighting scheme. See module docstring for when they coincide."""

    INVERSE_VOLATILITY = "INVERSE_VOLATILITY"
    EQUAL_RISK_CONTRIBUTION = "EQUAL_RISK_CONTRIBUTION"


@dataclass
class RiskParityAllocationAcrossStrategiesConfig:
    """
    Legacy config class for backward compatibility.

    ``target_portfolio_volatility`` is *not* consumed by
    :class:`RiskParityAllocationEngine`. This module allocates a fixed capital pool
    (weights sum to 1.0); it does not lever the portfolio to a volatility target.
    See ``dynamic-position-sizing-based-on-realized-volatility`` for that.
    """

    enabled: bool = True
    target_portfolio_volatility: float = 0.12  # Unused by the allocation engine.


class RiskParityAllocationAcrossStrategies:
    """Legacy class retained for backward compatibility."""

    def __init__(self, config: RiskParityAllocationAcrossStrategiesConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


@dataclass
class StrategyRiskData:
    """
    One strategy's risk input.

    ``annualized_volatility`` must be strictly positive and finite -- it is a
    standard deviation, so 0.20 means 20% annualized. ``daily_returns`` is
    caller-supplied context only; this engine does not estimate volatility or
    covariance from it. Estimate those upstream and pass the results in.
    """

    strategy_id: str
    annualized_volatility: float          # e.g., 0.20 for 20% vol
    daily_returns: Optional[List[float]] = None


@dataclass
class StrategyAllocation:
    strategy_id: str
    weight: float                        # Portfolio capital weight (0.0 to 1.0)
    allocated_capital_usd: float
    annualized_volatility: float
    risk_contribution_pct: float         # % of total portfolio risk contributed
    target_risk_contribution_pct: float  # Equal share (e.g. 33.33% for 3 strategies)
    risk_parity_error_pct: float         # |actual - target|, in percentage POINTS
    marginal_contribution_to_risk: float = 0.0   # MCR_i = (Sigma w)_i / sigma(w)
    relative_risk_error_pct: float = 0.0         # |actual - target| / target, in %


@dataclass
class RiskParityReport:
    total_capital_usd: float
    portfolio_annualized_volatility: float
    allocations: List[StrategyAllocation]
    is_risk_balanced: bool
    max_risk_parity_error_pct: float
    status: str                          # 'RISK_PARITY_BALANCED', 'RISK_PARITY_UNBALANCED'
    audit_notes: str
    method: str = AllocationMethod.INVERSE_VOLATILITY.value
    max_relative_risk_error_pct: float = 0.0
    solver_iterations: int = 0           # CCD sweeps used; 0 for the closed form
    covariance_supplied: bool = False    # False => correlations assumed zero


def _validate_strategies(strategies: Sequence[StrategyRiskData]) -> None:
    """
    Rejects inputs that would otherwise produce a confidently wrong allocation.

    Each of these was a live failure mode before validation existed: a NaN
    volatility propagated to NaN weights that compared False against the error
    limit and were reported as ``RISK_PARITY_BALANCED``; a negative volatility
    handed 99.9% of the book to one strategy; a zero volatility was clamped to
    1e-4 and did the same. Corrupt risk inputs must stop the allocation, not
    quietly concentrate it.
    """
    if not strategies:
        raise ValueError("At least 1 strategy is required for risk parity allocation.")

    seen = set()
    for strat in strategies:
        if not strat.strategy_id:
            raise ValueError("Every strategy requires a non-empty strategy_id.")
        if strat.strategy_id in seen:
            raise ValueError(
                f"Duplicate strategy_id {strat.strategy_id!r}: allocations would be "
                "ambiguous and could be double-funded."
            )
        seen.add(strat.strategy_id)

        vol = strat.annualized_volatility
        if not math.isfinite(vol):
            raise ValueError(
                f"{strat.strategy_id}: annualized_volatility must be finite, got {vol!r}."
            )
        if vol <= 0.0:
            raise ValueError(
                f"{strat.strategy_id}: annualized_volatility must be strictly positive, "
                f"got {vol}. A zero or negative standard deviation is not a risk estimate."
            )


def _cholesky(matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    """
    Lower-triangular Cholesky factor, used purely as a positive-definiteness test.

    Raises ``ValueError`` if the matrix is not positive definite. This replaces a
    ``max(port_variance, 1e-8)`` floor that silently absorbed negative variances:
    a correlation of 5.0 encoded into the matrix produced a plausible-looking
    portfolio volatility and a "balanced" verdict instead of an error.
    """
    n = len(matrix)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            acc = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if acc <= CHOLESKY_PIVOT_TOLERANCE * abs(matrix[i][i]):
                    raise ValueError(
                        "covariance_matrix is not positive definite (Cholesky failed at "
                        f"index {i}). Check for an invalid correlation, a duplicated or "
                        "perfectly correlated strategy, or more strategies than return "
                        "observations."
                    )
                lower[i][j] = math.sqrt(acc)
            else:
                lower[i][j] = acc / lower[j][j]
    return lower


def _validate_covariance(
    covariance_matrix: Sequence[Sequence[float]],
    strategies: Sequence[StrategyRiskData],
) -> None:
    """
    Checks shape, finiteness, symmetry, positive definiteness, and that the
    diagonal agrees with the declared volatilities.

    The last check matters because the two inputs are used for different things:
    an inconsistent pair silently produces a report whose weights come from one
    risk estimate and whose risk decomposition comes from another.
    """
    n = len(strategies)
    if len(covariance_matrix) != n:
        raise ValueError(
            f"covariance_matrix has {len(covariance_matrix)} rows but {n} strategies "
            "were supplied."
        )
    for i, row in enumerate(covariance_matrix):
        if len(row) != n:
            raise ValueError(
                f"covariance_matrix row {i} has {len(row)} entries, expected {n}."
            )
        for j, value in enumerate(row):
            if not math.isfinite(value):
                raise ValueError(
                    f"covariance_matrix[{i}][{j}] is not finite ({value!r})."
                )

    for i in range(n):
        for j in range(i + 1, n):
            a, b = covariance_matrix[i][j], covariance_matrix[j][i]
            scale = max(abs(a), abs(b), 1e-12)
            if abs(a - b) / scale > COVARIANCE_CONSISTENCY_TOLERANCE:
                raise ValueError(
                    f"covariance_matrix is not symmetric at [{i}][{j}]: {a} != {b}."
                )

    _cholesky(covariance_matrix)

    for i, strat in enumerate(strategies):
        implied_vol = math.sqrt(covariance_matrix[i][i])
        declared = strat.annualized_volatility
        if abs(implied_vol - declared) / declared > VOLATILITY_CONSISTENCY_TOLERANCE:
            raise ValueError(
                f"{strat.strategy_id}: covariance_matrix diagonal implies a volatility "
                f"of {implied_vol:.4%} but annualized_volatility is {declared:.4%}. "
                "Both must come from the same estimate, or the report mixes two "
                "inconsistent risk models."
            )


def _portfolio_risk(
    weights: Sequence[float],
    covariance_matrix: Sequence[Sequence[float]],
) -> Tuple[float, List[float], List[float]]:
    """
    Euler risk decomposition: returns (sigma_p, MCR, risk contribution shares).

    ``sum(RC_i) == sigma_p`` because volatility is homogeneous of degree 1 in the
    weights, so the shares returned here sum to 1.0.
    """
    n = len(weights)
    sigma_w = [
        sum(covariance_matrix[i][j] * weights[j] for j in range(n)) for i in range(n)
    ]
    variance = sum(weights[i] * sigma_w[i] for i in range(n))
    if variance <= 0.0:
        raise ValueError(
            f"Portfolio variance is non-positive ({variance}); covariance_matrix "
            "cannot be a valid risk model for these weights."
        )
    port_vol = math.sqrt(variance)
    mcr = [sigma_w[i] / port_vol for i in range(n)]
    shares = [weights[i] * mcr[i] / port_vol for i in range(n)]
    return port_vol, mcr, shares


def _diagonal_covariance(strategies: Sequence[StrategyRiskData]) -> List[List[float]]:
    """Zero-correlation covariance matrix implied by the declared volatilities."""
    n = len(strategies)
    matrix = [[0.0] * n for _ in range(n)]
    for i, strat in enumerate(strategies):
        matrix[i][i] = strat.annualized_volatility ** 2
    return matrix


def solve_equal_risk_contribution(
    covariance_matrix: Sequence[Sequence[float]],
    risk_budgets: Optional[Sequence[float]] = None,
    tolerance: float = DEFAULT_SOLVER_TOLERANCE,
    max_iterations: int = DEFAULT_SOLVER_MAX_ITERATIONS,
) -> Tuple[List[float], int]:
    """
    Long-only risk budgeting weights by cyclical coordinate descent.

    Implements Eq. 4 of Griveau-Billion, Richard and Roncalli (2013): each sweep
    updates one weight to the positive root of

        w_i^2 * Sigma_ii + w_i * sum_{j != i} Sigma_ij * w_j - b_i * sigma(w) = 0

    holding the others fixed. ``risk_budgets`` defaults to equal budgets (the ERC
    portfolio). Returns ``(weights summing to 1.0, sweeps used)``.

    Raises ``ValueError`` if the matrix is not positive definite, if a budget is
    not strictly positive (Eq. 4 is undefined at b_i = 0 -- ibid. Remark 3), or if
    the iteration does not converge within ``max_iterations``.
    """
    n = len(covariance_matrix)
    if n == 0:
        raise ValueError("covariance_matrix must be non-empty.")
    if risk_budgets is None:
        budgets = [1.0 / n] * n
    else:
        if len(risk_budgets) != n:
            raise ValueError(
                f"risk_budgets has {len(risk_budgets)} entries, expected {n}."
            )
        if any(not math.isfinite(b) or b <= 0.0 for b in risk_budgets):
            raise ValueError("Every risk budget must be finite and strictly positive.")
        total = sum(risk_budgets)
        budgets = [b / total for b in risk_budgets]

    _cholesky(covariance_matrix)

    # Inverse-volatility start: already the exact answer under equal correlations,
    # so the solver converges immediately in that case instead of drifting to it.
    vols = [math.sqrt(covariance_matrix[i][i]) for i in range(n)]
    weights = [budgets[i] / vols[i] for i in range(n)]
    scale = sum(weights)
    weights = [w / scale for w in weights]

    # Sigma @ w, maintained incrementally: updating one weight touches it in O(n).
    sigma_w = [
        sum(covariance_matrix[i][j] * weights[j] for j in range(n)) for i in range(n)
    ]

    for sweep in range(1, max_iterations + 1):
        max_shift = 0.0
        for i in range(n):
            port_vol = math.sqrt(max(sum(weights[k] * sigma_w[k] for k in range(n)), 0.0))
            variance_i = covariance_matrix[i][i]
            # beta_i excludes the i-th term of (Sigma w)_i.
            beta_i = sigma_w[i] - variance_i * weights[i]
            discriminant = beta_i * beta_i + 4.0 * variance_i * budgets[i] * port_vol
            updated = (-beta_i + math.sqrt(discriminant)) / (2.0 * variance_i)

            shift = updated - weights[i]
            if shift != 0.0:
                weights[i] = updated
                for k in range(n):
                    sigma_w[k] += covariance_matrix[k][i] * shift
            max_shift = max(max_shift, abs(shift))

        if max_shift < tolerance:
            scale = sum(weights)
            return [w / scale for w in weights], sweep

    raise ValueError(
        f"ERC solver did not converge within {max_iterations} sweeps "
        f"(last max weight shift {max_shift:.3e}). The covariance matrix is likely "
        "near-singular; check for duplicated or near-duplicated strategies."
    )


class RiskParityAllocationEngine:
    """
    Allocates capital across trading strategies so each contributes an equal share
    of portfolio volatility, and audits how close the result actually got.

    ``max_allowed_risk_error_pct`` is an absolute gate in percentage points of risk
    share; ``max_allowed_relative_error_pct`` is a scale-free gate on the same
    deviation expressed as a fraction of the target share. Both must pass for
    ``is_risk_balanced``. Neither is a regulatory threshold -- they are operating
    tolerances, and the caller should set them to the drift the book can carry.
    """

    def __init__(
        self,
        max_allowed_risk_error_pct: float = DEFAULT_MAX_ABSOLUTE_ERROR_PCT,
        max_allowed_relative_error_pct: float = DEFAULT_MAX_RELATIVE_ERROR_PCT,
    ):
        if max_allowed_risk_error_pct <= 0.0 or max_allowed_relative_error_pct <= 0.0:
            raise ValueError("Error tolerances must be strictly positive.")
        self.max_allowed_risk_error_pct = max_allowed_risk_error_pct
        self.max_allowed_relative_error_pct = max_allowed_relative_error_pct

    def compute_inverse_vol_weights(
        self, strategies: Sequence[StrategyRiskData]
    ) -> List[float]:
        """
        Inverse-volatility weights, normalized to 1.0.

        Exactly the ERC portfolio when all pairwise correlations are equal (MRT
        2009, Eq. 3), and a heuristic otherwise -- see the module docstring.
        """
        _validate_strategies(strategies)
        inv_vols = [1.0 / s.annualized_volatility for s in strategies]
        sum_inv = sum(inv_vols)
        return [inv / sum_inv for inv in inv_vols]

    def compute_risk_parity_allocation(
        self,
        strategies: List[StrategyRiskData],
        total_capital_usd: float = 1000000.0,
        covariance_matrix: Optional[List[List[float]]] = None,
        method: AllocationMethod = AllocationMethod.EQUAL_RISK_CONTRIBUTION,
    ) -> RiskParityReport:
        """
        Computes capital allocations and audits realized risk-contribution equality.

        With no ``covariance_matrix`` the strategies are treated as uncorrelated, and
        both methods return the same closed-form inverse-volatility weights (equal
        correlations, MRT 2009 Eq. 3). That assumption is almost always optimistic:
        strategy correlations are rarely zero and rise under stress, so the reported
        portfolio volatility is a floor, not a forecast.

        With a ``covariance_matrix``, ``EQUAL_RISK_CONTRIBUTION`` (the default) solves
        for genuinely equal risk contributions, while ``INVERSE_VOLATILITY`` returns
        the closed form and lets the audit report how far from parity it lands.
        """
        _validate_strategies(strategies)
        if not math.isfinite(total_capital_usd) or total_capital_usd <= 0.0:
            raise ValueError(
                f"total_capital_usd must be finite and strictly positive, got "
                f"{total_capital_usd!r}."
            )
        method = AllocationMethod(method)

        n = len(strategies)
        covariance_supplied = covariance_matrix is not None
        if covariance_supplied:
            _validate_covariance(covariance_matrix, strategies)
            risk_model: Sequence[Sequence[float]] = covariance_matrix
        else:
            risk_model = _diagonal_covariance(strategies)

        solver_iterations = 0
        if method is AllocationMethod.EQUAL_RISK_CONTRIBUTION and covariance_supplied:
            weights, solver_iterations = solve_equal_risk_contribution(risk_model)
        else:
            # Diagonal Sigma is the equal-correlation case, where the closed form is
            # the exact ERC solution -- no iteration needed.
            weights = self.compute_inverse_vol_weights(strategies)

        port_vol, mcr, risk_shares = _portfolio_risk(weights, risk_model)

        target_share_pct = 100.0 / n
        allocations: List[StrategyAllocation] = []
        max_error = 0.0
        max_relative_error = 0.0

        for i, strat in enumerate(strategies):
            rc_pct = risk_shares[i] * 100.0
            err_pct = abs(rc_pct - target_share_pct)
            relative_err_pct = err_pct / target_share_pct * 100.0
            max_error = max(max_error, err_pct)
            max_relative_error = max(max_relative_error, relative_err_pct)

            allocations.append(StrategyAllocation(
                strategy_id=strat.strategy_id,
                weight=round(weights[i], 6),
                # Capital comes from the unrounded weight so the allocations still
                # sum to the capital pool.
                allocated_capital_usd=round(total_capital_usd * weights[i], 2),
                annualized_volatility=strat.annualized_volatility,
                risk_contribution_pct=round(rc_pct, 4),
                target_risk_contribution_pct=round(target_share_pct, 4),
                risk_parity_error_pct=round(err_pct, 4),
                marginal_contribution_to_risk=round(mcr[i], 6),
                relative_risk_error_pct=round(relative_err_pct, 4),
            ))

        is_balanced = (
            max_error <= self.max_allowed_risk_error_pct
            and max_relative_error <= self.max_allowed_relative_error_pct
        )
        status = "RISK_PARITY_BALANCED" if is_balanced else "RISK_PARITY_UNBALANCED"

        correlation_note = (
            "full covariance" if covariance_supplied
            else "ZERO-CORRELATION ASSUMPTION (no covariance matrix supplied)"
        )
        notes = (
            f"RISK PARITY [{status}]: Method = {method.value}, Strategies = {n}, "
            f"Total Capital = ${total_capital_usd:,.2f}, Risk model = {correlation_note}, "
            f"Portfolio Vol = {port_vol * 100:.2f}%, "
            f"Max Risk Error = {max_error:.2f}pp (Limit: {self.max_allowed_risk_error_pct}pp), "
            f"Max Relative Error = {max_relative_error:.2f}% "
            f"(Limit: {self.max_allowed_relative_error_pct}%)."
        )

        if not is_balanced:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskParityReport(
            total_capital_usd=total_capital_usd,
            portfolio_annualized_volatility=round(port_vol * 100.0, 4),
            allocations=allocations,
            is_risk_balanced=is_balanced,
            max_risk_parity_error_pct=round(max_error, 4),
            status=status,
            audit_notes=notes,
            method=method.value,
            max_relative_risk_error_pct=round(max_relative_error, 4),
            solver_iterations=solver_iterations,
            covariance_supplied=covariance_supplied,
        )
