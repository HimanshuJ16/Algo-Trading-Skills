"""
incremental-capital-deployment-for-new-strategies: stage-gated capital ramp engine.

Governs how much of a strategy's *target* capital is actually at risk while its live
track record is still too short to trust, using a 4-tier ladder:

    Tier 0 Sandbox (0%) -> Tier 1 Seed (10%) -> Tier 2 Scale (50%) -> Tier 3 Full (100%)

Each evaluation resolves, in this fixed order:

    1. Emergency drawdown breach  -> demote to Tier 0, freeze ($0 allocation)
    2. Maintenance breach          -> step down exactly one tier
    3. Promotion gate              -> step up at most one tier
    4. Otherwise                   -> retain the current tier

Conventions the caller MUST honour (the engine cannot verify them and they change
the meaning of every gate):

- ``realized_max_drawdown_pct`` is a **positive magnitude in percent** measured over
  the **current tier's window only** — the same window ``days_in_tier`` counts. It is
  NOT signed (-4.5 is rejected, not read as 4.5) and NOT since-inception. A
  since-inception drawdown would ratchet: once breached, the strategy could never be
  re-promoted, because a running maximum never decreases.
- ``days_in_tier`` resets to 0 on **every** tier change. The report returns
  ``next_days_in_tier`` for exactly this reason — persist it rather than carrying the
  old counter forward. Carrying it forward lets a paper-tier track record satisfy a
  live-tier gate and promotes a strategy two tiers in two evaluations.
- ``realized_sharpe`` at Tier 1+ is measured on **live** fills, not paper fills.

Statistical limitation (this is the single most important caveat in this module):

    The Sharpe gates are not statistically conclusive at the tier durations used.

Lo (2002), "The Statistics of Sharpe Ratios", Financial Analysts Journal 58(4),
gives the asymptotic standard error of an estimated Sharpe ratio under IID returns
as SE(SR) = sqrt((1 + SR^2 / 2) / T), and Eq. 17-18 give SR(q) = sqrt(q) * SR for
time aggregation. Composing the two yields, for an annualized Sharpe estimated from
T observations at q periods per year:

    SE(SR_annual) = sqrt((q + SR_annual^2 / 2) / T)

At the Tier 1 -> Tier 2 gate (T = 30 daily observations, q = 252, threshold 1.0) that
standard error is ~2.90. A realized Sharpe of 1.4 therefore sits ~0.14 standard
errors above the 1.0 threshold: the gate is very nearly a coin flip on 30 days of
data. Reaching SE = 0.5 at SR = 1.0 needs T = (252 + 0.5)/0.25 ~ 1010 daily
observations, roughly four years. The gates are still worth enforcing as *floors* —
they exclude visibly broken strategies — but ``sharpe_gate_conclusive`` in the report
reports whether the observed Sharpe clears its threshold by more than 1.96 standard
errors, and it will normally be False. Treat a passing Sharpe gate as "not obviously
broken", never as "edge demonstrated".

For the same reason the engine **demotes on drawdown and slippage but never on
Sharpe**: a realized drawdown and a realized slippage ratio are facts about fills
that already happened, whereas a short-window Sharpe is dominated by estimation
error, and de-risking on noise would thrash capital between tiers.

Max drawdown carries the mirror-image bias: for a driftless process the expected
running maximum drawdown grows roughly with the square root of the observation
window, so a 30-day Tier 1 window systematically observes a *smaller* max drawdown
than a 60-day Tier 2 window on the identical strategy. A fixed emergency limit
applied across tiers is therefore more permissive early in the ramp, exactly when the
track record is weakest. This is a deliberate, documented conservatism trade-off, not
a calibration the engine can fix on its own.

Scope: this module decides *how much capital a strategy is entitled to*. It does not
place, size, or route orders, does not flatten positions, and is not a kill switch —
"EMERGENCY_DEACTIVATED" sets the entitlement to $0 and it is the caller's job to act
on that (see `kill-switch-and-drawdown-circuit-breakers`). Under MiFID II RTS 6
Article 5 the deployment or substantial update of an algorithmic trading strategy
must be authorised by a person designated by senior management, so in an EU/UK
regulated firm this engine's output is a *recommendation for authorisation*, not an
auto-executing capital change.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: Observations per year for daily returns, used for Sharpe standard errors.
TRADING_DAYS_PER_YEAR = 252.0

#: Two-sided 95% normal critical value, used for the Sharpe conclusiveness check.
Z_95 = 1.959963984540054

TIER_SANDBOX = 0
TIER_SEED = 1
TIER_SCALE = 2
TIER_FULL = 3

VALID_TIERS: Tuple[int, ...] = (TIER_SANDBOX, TIER_SEED, TIER_SCALE, TIER_FULL)

TIER_ALLOCATION_PCTS: Dict[int, float] = {
    TIER_SANDBOX: 0.0,    # Sandbox / paper
    TIER_SEED: 0.10,      # Seed 10%
    TIER_SCALE: 0.50,     # Scale 50%
    TIER_FULL: 1.00,      # Full 100%
}

TIER_NAMES: Dict[int, str] = {
    TIER_SANDBOX: "TIER_0_SANDBOX",
    TIER_SEED: "TIER_1_SEED",
    TIER_SCALE: "TIER_2_SCALE",
    TIER_FULL: "TIER_3_FULL",
}

EMERGENCY_TIER_NAME = "EMERGENCY_DEACTIVATED"


def annualized_sharpe_standard_error(
    sharpe_annualized: float,
    n_observations: int,
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Asymptotic standard error of an *annualized* Sharpe ratio estimate under IID returns.

    Lo (2002), Financial Analysts Journal 58(4), gives the per-period result
    SE(SR) = sqrt((1 + SR^2 / 2) / T) and the IID time-aggregation rule
    SR(q) = sqrt(q) * SR (Eq. 17), whose asymptotic distribution scales by the same
    factor (Eq. 18). Substituting SR_period = SR_annual / sqrt(q) and multiplying the
    per-period standard error by sqrt(q) collapses to:

        SE(SR_annual) = sqrt((q + SR_annual^2 / 2) / T)

    Passing ``periods_per_year=1`` recovers Lo's per-period Table 1 directly.

    Args:
        sharpe_annualized: Annualized Sharpe ratio estimate.
        n_observations: Number of return observations T the estimate was built from.
        periods_per_year: Observations per year q (252 for daily returns).

    Returns:
        The standard error, in annualized Sharpe units.

    Raises:
        ValueError: If inputs are non-finite, or T < 1, or q <= 0.
    """
    if not math.isfinite(sharpe_annualized):
        raise ValueError(f"sharpe_annualized must be finite, got {sharpe_annualized!r}")
    if not math.isfinite(periods_per_year) or periods_per_year <= 0.0:
        raise ValueError(f"periods_per_year must be finite and > 0, got {periods_per_year!r}")
    if n_observations < 1:
        raise ValueError(f"n_observations must be >= 1, got {n_observations!r}")
    return math.sqrt(
        (periods_per_year + 0.5 * sharpe_annualized ** 2) / float(n_observations)
    )


def required_observations_for_sharpe_precision(
    target_standard_error: float,
    sharpe_annualized: float,
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> int:
    """
    Observations needed for an annualized Sharpe estimate to reach a target precision.

    Inverts :func:`annualized_sharpe_standard_error`:

        T = ceil((q + SR_annual^2 / 2) / SE_target^2)

    At SR = 1.0, q = 252 and SE_target = 0.5 this returns 1010 daily observations —
    about four years — which is the honest sample size behind a "Sharpe above 1.0"
    claim stated to within +/- 1.0 at 95% confidence.

    Raises:
        ValueError: If inputs are non-finite, or the target standard error is <= 0.
    """
    if not math.isfinite(sharpe_annualized):
        raise ValueError(f"sharpe_annualized must be finite, got {sharpe_annualized!r}")
    if not math.isfinite(target_standard_error) or target_standard_error <= 0.0:
        raise ValueError(
            f"target_standard_error must be finite and > 0, got {target_standard_error!r}"
        )
    if not math.isfinite(periods_per_year) or periods_per_year <= 0.0:
        raise ValueError(f"periods_per_year must be finite and > 0, got {periods_per_year!r}")
    return math.ceil(
        (periods_per_year + 0.5 * sharpe_annualized ** 2) / (target_standard_error ** 2)
    )


def _require_finite(value: float, name: str) -> float:
    """
    Reject NaN/Inf on any field a gate compares.

    This is a capital-protection control, not a convenience check. Every gate in this
    module is a comparison, and every comparison against NaN evaluates False — so a
    NaN drawdown makes ``dd >= emergency_limit`` False and silently *bypasses the
    emergency demotion*, leaving a strategy at full allocation on unusable data.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be a finite number, got {value!r}. Non-finite values silently "
            f"defeat every threshold comparison in this engine, including the emergency "
            f"drawdown gate."
        )
    return numeric


def _require_tier(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int in {VALID_TIERS}, got {value!r}")
    if value not in VALID_TIERS:
        raise ValueError(f"{name} must be one of {VALID_TIERS}, got {value!r}")
    return value


@dataclass(frozen=True)
class TierPromotionGate:
    """
    Entry conditions for promotion out of ``from_tier`` into ``from_tier + 1``.

    A threshold of ``None`` means "not evaluated at this transition". Every threshold
    is an entry condition only; staying in a tier is governed separately by the
    maintenance limits on the engine, which are deliberately looser so a strategy does
    not oscillate across the boundary it just cleared.
    """
    from_tier: int
    min_days_in_tier: int
    max_drawdown_pct: Optional[float] = None
    min_realized_sharpe: Optional[float] = None
    max_slippage_ratio: Optional[float] = None
    max_execution_errors: Optional[int] = None


#: Default promotion ladder. Tier 0 -> 1 is the transition that first commits *real*
#: capital, so it screens paper drawdown and paper execution errors rather than being
#: a pure elapsed-time check.
DEFAULT_PROMOTION_GATES: Dict[int, TierPromotionGate] = {
    TIER_SANDBOX: TierPromotionGate(
        from_tier=TIER_SANDBOX,
        min_days_in_tier=14,
        max_drawdown_pct=5.0,
        min_realized_sharpe=None,      # paper Sharpe is not evidence of live edge
        max_slippage_ratio=None,       # no live fills exist yet to compare against
        max_execution_errors=0,
    ),
    TIER_SEED: TierPromotionGate(
        from_tier=TIER_SEED,
        min_days_in_tier=30,
        max_drawdown_pct=5.0,
        min_realized_sharpe=1.0,
        max_slippage_ratio=1.5,
        max_execution_errors=None,
    ),
    TIER_SCALE: TierPromotionGate(
        from_tier=TIER_SCALE,
        min_days_in_tier=60,
        max_drawdown_pct=8.0,
        min_realized_sharpe=1.2,
        max_slippage_ratio=1.5,
        max_execution_errors=None,
    ),
}

#: Drawdown a strategy may carry *while remaining* at a tier, before stepping down one
#: tier. Looser than the entry gate for the tier above it, giving a hysteresis band.
DEFAULT_MAINTENANCE_MAX_DRAWDOWN_PCT: Dict[int, float] = {
    TIER_SEED: 8.0,
    TIER_SCALE: 10.0,
    TIER_FULL: 10.0,
}

#: Live-vs-backtest slippage ratio a strategy may carry while remaining at a tier.
DEFAULT_MAINTENANCE_MAX_SLIPPAGE_RATIO = 2.0


@dataclass
class StrategyDeploymentState:
    """
    Observed state of one strategy at one evaluation point.

    All performance fields describe the **current tier's window only** (the same
    window ``days_in_tier`` counts), not the strategy's whole history.
    """
    strategy_id: str
    current_tier: int                    # 0, 1, 2, 3
    days_in_tier: int
    realized_sharpe: float               # annualized; live fills at Tier 1+
    realized_max_drawdown_pct: float     # POSITIVE magnitude in percent, e.g. 4.5
    slippage_vs_backtest_ratio: float    # e.g. 1.1x (actual vs backtest slippage)
    target_full_capital_usd: float       # e.g. $1,000,000
    execution_errors_in_tier: int = 0    # crashes/critical execution faults in tier

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError(f"strategy_id must be a non-empty string, got {self.strategy_id!r}")

        self.current_tier = _require_tier(self.current_tier, "current_tier")

        if isinstance(self.days_in_tier, bool) or not isinstance(self.days_in_tier, int):
            raise ValueError(f"days_in_tier must be an int, got {self.days_in_tier!r}")
        if self.days_in_tier < 0:
            raise ValueError(f"days_in_tier must be >= 0, got {self.days_in_tier!r}")

        if (isinstance(self.execution_errors_in_tier, bool)
                or not isinstance(self.execution_errors_in_tier, int)):
            raise ValueError(
                f"execution_errors_in_tier must be an int, got {self.execution_errors_in_tier!r}"
            )
        if self.execution_errors_in_tier < 0:
            raise ValueError(
                f"execution_errors_in_tier must be >= 0, got {self.execution_errors_in_tier!r}"
            )

        self.realized_sharpe = _require_finite(self.realized_sharpe, "realized_sharpe")

        self.realized_max_drawdown_pct = _require_finite(
            self.realized_max_drawdown_pct, "realized_max_drawdown_pct")
        if self.realized_max_drawdown_pct < 0.0:
            raise ValueError(
                f"realized_max_drawdown_pct must be a positive magnitude in percent, got "
                f"{self.realized_max_drawdown_pct!r}. A signed drawdown passes every "
                f"'<= limit' promotion gate and fails the '>= limit' emergency gate, so a "
                f"strategy in a deep drawdown would be promoted instead of deactivated."
            )
        if self.realized_max_drawdown_pct > 100.0:
            raise ValueError(
                f"realized_max_drawdown_pct must be <= 100 (percent, not a fraction), got "
                f"{self.realized_max_drawdown_pct!r}"
            )

        self.slippage_vs_backtest_ratio = _require_finite(
            self.slippage_vs_backtest_ratio, "slippage_vs_backtest_ratio")
        if self.slippage_vs_backtest_ratio <= 0.0:
            raise ValueError(
                f"slippage_vs_backtest_ratio must be > 0 (1.0 = matches backtest), got "
                f"{self.slippage_vs_backtest_ratio!r}"
            )

        self.target_full_capital_usd = _require_finite(
            self.target_full_capital_usd, "target_full_capital_usd")
        if self.target_full_capital_usd < 0.0:
            raise ValueError(
                f"target_full_capital_usd must be >= 0, got {self.target_full_capital_usd!r}"
            )


@dataclass
class IncrementalDeploymentReport:
    """Auditable outcome of one deployment evaluation."""
    strategy_id: str
    previous_tier: int
    new_tier: int
    tier_name: str                      # TIER_0_SANDBOX .. TIER_3_FULL, EMERGENCY_DEACTIVATED
    capital_allocation_pct: float       # 0.0, 0.10, 0.50, 1.00
    allocated_capital_usd: float
    promotion_status: str               # PROMOTED, RETAINED_CURRENT_TIER,
                                        # DEMOTED_DRAWDOWN_BREACH, DEMOTED_MAINTENANCE_BREACH
    audit_notes: str
    #: Named gate conditions that blocked promotion, e.g. "min_days_in_tier: 15 < 30".
    #: Empty when the strategy was promoted, demoted, or is already at Tier 3.
    failed_gates: Tuple[str, ...] = ()
    #: Value the caller must persist as ``days_in_tier`` for the next evaluation:
    #: 0 whenever the tier changed, otherwise the current count unchanged.
    next_days_in_tier: int = 0
    #: Standard error of the annualized Sharpe over ``days_in_tier`` daily observations
    #: (Lo 2002). None when no Sharpe gate applies at this transition.
    sharpe_standard_error: Optional[float] = None
    #: True only if realized Sharpe exceeds its gate by more than 1.96 standard errors.
    #: Normally False at these tier durations — a passing Sharpe gate is a floor, not
    #: evidence of edge.
    sharpe_gate_conclusive: bool = False


class IncrementalCapitalDeploymentEngine:
    """
    Portfolio risk management engine implementing 4-tier stage-gated capital ramp-up
    (Paper -> 10% Seed -> 50% Scale -> 100% Full) with realized Sharpe and drawdown
    promotion gates, maintenance demotion, and an emergency drawdown kill gate.

    Stateless and deterministic: the same state always yields the same report. The
    engine holds policy, never strategy state, so one instance may serve many
    strategies.
    """

    def __init__(
        self,
        emergency_max_drawdown_limit_pct: float = 12.0,  # >= this triggers demotion to Tier 0
        *,
        promotion_gates: Optional[Mapping[int, TierPromotionGate]] = None,
        maintenance_max_drawdown_pct: Optional[Mapping[int, float]] = None,
        maintenance_max_slippage_ratio: float = DEFAULT_MAINTENANCE_MAX_SLIPPAGE_RATIO,
        enable_maintenance_demotion: bool = True,
    ) -> None:
        self.emergency_max_drawdown_limit_pct = _require_finite(
            emergency_max_drawdown_limit_pct, "emergency_max_drawdown_limit_pct")
        if not 0.0 < self.emergency_max_drawdown_limit_pct <= 100.0:
            raise ValueError(
                f"emergency_max_drawdown_limit_pct must be in (0, 100], got "
                f"{emergency_max_drawdown_limit_pct!r}"
            )

        self.maintenance_max_slippage_ratio = _require_finite(
            maintenance_max_slippage_ratio, "maintenance_max_slippage_ratio")
        if self.maintenance_max_slippage_ratio <= 0.0:
            raise ValueError(
                f"maintenance_max_slippage_ratio must be > 0, got "
                f"{maintenance_max_slippage_ratio!r}"
            )

        self.enable_maintenance_demotion = bool(enable_maintenance_demotion)
        self.promotion_gates: Dict[int, TierPromotionGate] = dict(
            DEFAULT_PROMOTION_GATES if promotion_gates is None else promotion_gates)
        self.maintenance_max_drawdown_pct: Dict[int, float] = dict(
            DEFAULT_MAINTENANCE_MAX_DRAWDOWN_PCT
            if maintenance_max_drawdown_pct is None else maintenance_max_drawdown_pct)

        for tier, limit in self.maintenance_max_drawdown_pct.items():
            _require_tier(tier, "maintenance_max_drawdown_pct key")
            _require_finite(limit, f"maintenance_max_drawdown_pct[{tier}]")
            if limit >= self.emergency_max_drawdown_limit_pct:
                raise ValueError(
                    f"maintenance_max_drawdown_pct[{tier}] ({limit}) must be below the "
                    f"emergency limit ({self.emergency_max_drawdown_limit_pct}); otherwise "
                    f"the emergency gate fires first and the maintenance step-down is dead code."
                )

        for from_tier, gate in self.promotion_gates.items():
            _require_tier(from_tier, "promotion_gates key")
            if gate.from_tier != from_tier:
                raise ValueError(
                    f"promotion_gates[{from_tier}] declares from_tier={gate.from_tier}"
                )
            if from_tier == TIER_FULL:
                raise ValueError("no promotion gate can exist above Tier 3")

        self.tier_allocation_pcts: Dict[int, float] = dict(TIER_ALLOCATION_PCTS)
        self.tier_names: Dict[int, str] = dict(TIER_NAMES)

    def evaluate_strategy_deployment(
        self,
        state: StrategyDeploymentState,
    ) -> IncrementalDeploymentReport:
        """
        Evaluate stage-gated promotion, retention, or demotion for one strategy.

        Resolution order is fixed and each branch is terminal: emergency demotion,
        then maintenance demotion, then promotion, then retention. A strategy never
        moves more than one tier per evaluation in either direction, except the
        emergency branch which goes straight to Tier 0.

        Args:
            state: Validated observation of the strategy in its current tier.

        Returns:
            An :class:`IncrementalDeploymentReport`. ``allocated_capital_usd`` is the
            strategy's capital *entitlement*, not an instruction to trade it.
        """
        if not isinstance(state, StrategyDeploymentState):
            raise TypeError(
                f"state must be a StrategyDeploymentState, got {type(state).__name__}")

        # Defence in depth: StrategyDeploymentState is a mutable dataclass, so a caller
        # can assign an out-of-range tier *after* __post_init__ validated it. Re-check
        # here rather than let an invalid tier reach the allocation lookup as a KeyError.
        curr = _require_tier(state.current_tier, "state.current_tier")

        # 1. Emergency drawdown breach -> Tier 0, frozen.
        if state.realized_max_drawdown_pct >= self.emergency_max_drawdown_limit_pct:
            notes = (
                f"EMERGENCY DEMOTION [{state.strategy_id}]: Realized Max Drawdown "
                f"{state.realized_max_drawdown_pct:.1f}% exceeds limit "
                f"({self.emergency_max_drawdown_limit_pct:.1f}%). Demoting from Tier {curr} "
                f"to Tier 0 ($0 allocation)."
            )
            logger.critical(notes)
            return IncrementalDeploymentReport(
                strategy_id=state.strategy_id,
                previous_tier=curr,
                new_tier=TIER_SANDBOX,
                tier_name=EMERGENCY_TIER_NAME,
                capital_allocation_pct=0.0,
                allocated_capital_usd=0.0,
                promotion_status="DEMOTED_DRAWDOWN_BREACH",
                audit_notes=notes,
                next_days_in_tier=0,
            )

        # 2. Maintenance breach -> step down exactly one tier.
        #    Drawdown and slippage only: both are facts about fills that already
        #    happened. Sharpe is deliberately excluded (see module docstring) because
        #    its standard error at these tier durations exceeds the thresholds
        #    themselves, and de-risking on noise thrashes capital.
        if self.enable_maintenance_demotion and curr > TIER_SANDBOX:
            breach = self._maintenance_breach(state)
            if breach is not None:
                return self._build_transition_report(
                    state,
                    new_tier=curr - 1,
                    status="DEMOTED_MAINTENANCE_BREACH",
                    reason=breach,
                )

        # 3. Promotion gate -> step up at most one tier.
        gate = self.promotion_gates.get(curr)
        failed_gates = self._failed_promotion_gates(state, gate)

        if gate is not None and not failed_gates:
            return self._build_transition_report(
                state,
                new_tier=gate.from_tier + 1,
                status="PROMOTED",
                reason=None,
            )

        # 4. Retain.
        alloc_pct = self.tier_allocation_pcts[curr]
        alloc_usd = round(state.target_full_capital_usd * alloc_pct, 2)
        tier_name = self.tier_names[curr]
        blocking = "; ".join(failed_gates) if failed_gates else "at maximum tier"
        notes = (
            f"STAGE-GATED RETAINED [{state.strategy_id}]: Retained at Tier {curr} "
            f"({tier_name}). Capital allocation = {alloc_pct * 100:.0f}% "
            f"(${alloc_usd:,.2f} USD). Days in Tier = {state.days_in_tier}. "
            f"Blocking: {blocking}."
        )
        logger.info(notes)

        se, conclusive = self._sharpe_confidence(state, gate)
        return IncrementalDeploymentReport(
            strategy_id=state.strategy_id,
            previous_tier=curr,
            new_tier=curr,
            tier_name=tier_name,
            capital_allocation_pct=alloc_pct,
            allocated_capital_usd=alloc_usd,
            promotion_status="RETAINED_CURRENT_TIER",
            audit_notes=notes,
            failed_gates=failed_gates,
            next_days_in_tier=state.days_in_tier,
            sharpe_standard_error=se,
            sharpe_gate_conclusive=conclusive,
        )

    def _maintenance_breach(self, state: StrategyDeploymentState) -> Optional[str]:
        """Return a description of the maintenance condition breached, or None."""
        dd_limit = self.maintenance_max_drawdown_pct.get(state.current_tier)
        if dd_limit is not None and state.realized_max_drawdown_pct > dd_limit:
            return (
                f"maintenance drawdown {state.realized_max_drawdown_pct:.1f}% > "
                f"{dd_limit:.1f}% for Tier {state.current_tier}"
            )
        if state.slippage_vs_backtest_ratio > self.maintenance_max_slippage_ratio:
            return (
                f"maintenance slippage {state.slippage_vs_backtest_ratio:.2f}x > "
                f"{self.maintenance_max_slippage_ratio:.2f}x"
            )
        return None

    @staticmethod
    def _failed_promotion_gates(
        state: StrategyDeploymentState,
        gate: Optional[TierPromotionGate],
    ) -> Tuple[str, ...]:
        """Name every promotion condition the strategy fails, for the audit trail."""
        if gate is None:
            return ()
        failed = []
        if state.days_in_tier < gate.min_days_in_tier:
            failed.append(
                f"min_days_in_tier: {state.days_in_tier} < {gate.min_days_in_tier}")
        if (gate.max_drawdown_pct is not None
                and state.realized_max_drawdown_pct > gate.max_drawdown_pct):
            failed.append(
                f"max_drawdown_pct: {state.realized_max_drawdown_pct:.1f} > "
                f"{gate.max_drawdown_pct:.1f}")
        if (gate.min_realized_sharpe is not None
                and state.realized_sharpe < gate.min_realized_sharpe):
            failed.append(
                f"min_realized_sharpe: {state.realized_sharpe:.2f} < "
                f"{gate.min_realized_sharpe:.2f}")
        if (gate.max_slippage_ratio is not None
                and state.slippage_vs_backtest_ratio > gate.max_slippage_ratio):
            failed.append(
                f"max_slippage_ratio: {state.slippage_vs_backtest_ratio:.2f} > "
                f"{gate.max_slippage_ratio:.2f}")
        if (gate.max_execution_errors is not None
                and state.execution_errors_in_tier > gate.max_execution_errors):
            failed.append(
                f"max_execution_errors: {state.execution_errors_in_tier} > "
                f"{gate.max_execution_errors}")
        return tuple(failed)

    @staticmethod
    def _sharpe_confidence(
        state: StrategyDeploymentState,
        gate: Optional[TierPromotionGate],
    ) -> Tuple[Optional[float], bool]:
        """
        Standard error of the realized Sharpe, and whether it clears its gate decisively.

        Returns ``(None, False)`` when no Sharpe gate applies or the tier window is
        empty. "Decisive" means the realized Sharpe exceeds the threshold by more than
        1.96 standard errors — see the module docstring for why this is almost never
        true at 30-60 daily observations.
        """
        if gate is None or gate.min_realized_sharpe is None or state.days_in_tier < 1:
            return None, False
        se = annualized_sharpe_standard_error(state.realized_sharpe, state.days_in_tier)
        conclusive = (state.realized_sharpe - gate.min_realized_sharpe) > Z_95 * se
        return se, conclusive

    def _build_transition_report(
        self,
        state: StrategyDeploymentState,
        new_tier: int,
        status: str,
        reason: Optional[str],
    ) -> IncrementalDeploymentReport:
        """Build the report for a promotion or a one-step maintenance demotion."""
        curr = state.current_tier
        alloc_pct = self.tier_allocation_pcts[new_tier]
        alloc_usd = round(state.target_full_capital_usd * alloc_pct, 2)
        tier_name = self.tier_names[new_tier]

        if status == "PROMOTED":
            gate = self.promotion_gates.get(curr)
            se, conclusive = self._sharpe_confidence(state, gate)
            confidence = ""
            if se is not None:
                confidence = (
                    f" Sharpe SE over {state.days_in_tier} daily obs = {se:.2f} "
                    f"({'decisive' if conclusive else 'NOT statistically decisive'} at 95%)."
                )
            notes = (
                f"STAGE-GATED PROMOTION [{state.strategy_id}]: Promoted from Tier {curr} to "
                f"Tier {new_tier} ({tier_name}). Capital allocation increased to "
                f"{alloc_pct * 100:.0f}% (${alloc_usd:,.2f} USD). Realized Sharpe = "
                f"{state.realized_sharpe:.2f}, Max DD = {state.realized_max_drawdown_pct:.1f}%."
                f"{confidence} Reset days_in_tier to 0."
            )
            logger.info(notes)
        else:
            se, conclusive = None, False
            notes = (
                f"MAINTENANCE DEMOTION [{state.strategy_id}]: Stepped down from Tier {curr} to "
                f"Tier {new_tier} ({tier_name}) — {reason}. Capital allocation reduced to "
                f"{alloc_pct * 100:.0f}% (${alloc_usd:,.2f} USD). Reset days_in_tier to 0."
            )
            logger.warning(notes)

        return IncrementalDeploymentReport(
            strategy_id=state.strategy_id,
            previous_tier=curr,
            new_tier=new_tier,
            tier_name=tier_name,
            capital_allocation_pct=alloc_pct,
            allocated_capital_usd=alloc_usd,
            promotion_status=status,
            audit_notes=notes,
            failed_gates=(),
            next_days_in_tier=0,
            sharpe_standard_error=se,
            sharpe_gate_conclusive=conclusive,
        )
