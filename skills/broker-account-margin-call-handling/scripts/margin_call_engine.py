"""
broker-account-margin-call-handling:
Margin utilisation monitoring, pre-trade veto gating and liquidity-aware
de-leveraging planning for algorithmic trading on margin accounts.

Two metrics matter and they are not interchangeable:

  - **The house ratio** ``maintenance_margin / net_liquidation_value``, which
    drives the configurable WARNING / CRITICAL / BREACH tiers. This is an early
    warning signal you choose the thresholds for; no regulator or broker defines
    it.
  - **The broker's own cushion**, ``excess_liquidity``. At Interactive Brokers
    this is ``Equity with Loan Value - Maintenance Margin``, and when it goes
    negative the account no longer meets maintenance requirements and positions
    may be liquidated. It is authoritative in a way the house ratio is not.

They can disagree, and the direction of the disagreement is dangerous: Equity
with Loan Value excludes assets that do not count toward margin equity, so it
can be materially lower than NLV. An account holding non-marginable stock can
show ``maintenance_margin / NLV = 0.70`` — comfortably NORMAL on the house ratio
— while ``excess_liquidity`` is already negative and the broker is liquidating.
``evaluate_margin_health`` therefore treats negative excess liquidity as a breach
regardless of what the house ratio says.

Scope limits the caller must respect:

  - **The de-leveraging planner assumes margin is separable per position**
    (``reduction = units x margin_per_unit``). That holds approximately under
    Reg T style fixed-percentage margining. It does **not** hold under Portfolio
    Margin or SPAN, where margin is computed on the portfolio's stressed loss:
    closing one leg of a hedge can *increase* total margin. Under those regimes
    treat the plan as a candidate ordering only, and re-price each slice through
    the broker before acting on it.
  - **Timing is broker-specific.** IBKR does not make margin calls; it liquidates
    in real time, without prior notice, and may do so without the account ever
    displaying a margin warning. Reaching the BREACH tier does not mean you have
    time to act — the pre-breach tiers are where the useful decisions happen.
  - The engine plans; it does not place, cancel or reconcile orders.

See ``references/standards.md`` for per-broker triggers with sources and the
jurisdictional limits of the regulatory figures quoted.
"""
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class MarginCallError(RuntimeError):
    """Raised when a margin breach occurs or new orders are vetoed under margin stress."""
    pass


class MarginDataError(ValueError):
    """
    Raised when account or order inputs are unusable.

    Kept distinct from ``MarginCallError`` so a caller can tell "the risk engine
    refused this order" from "the data feeding the risk engine is broken". Both
    must stop the order; only the second means escalate to whoever owns the feed.
    """
    pass


def _require_finite(name: str, value: float, *, allow_negative: bool = True) -> float:
    """
    Validate a numeric margin input.

    NaN is the specific hazard: every comparison against NaN is False, so a NaN
    ratio walks past every threshold test and lands in the healthy branch. A
    margin engine that reports NORMAL because the feed returned NaN is worse than
    one that fails loudly, so unusable input raises rather than defaulting.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarginDataError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise MarginDataError(
            f"{name} must be finite, got {value!r}. Refusing to evaluate margin health on "
            f"an unusable feed value; treat this as a data outage, not a healthy account."
        )
    if not allow_negative and value < 0:
        raise MarginDataError(f"{name} must be >= 0, got {value!r}")
    return float(value)


class MarginState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"          # Margin Ratio >= warning_threshold
    CRITICAL = "CRITICAL"        # Margin Ratio >= critical_threshold (Cancel Open Orders)
    MARGIN_CALL_BREACH = "BREACH"# Margin Ratio >= breach_threshold (De-leverage Positions)


@dataclass
class AccountMarginSnapshot:
    """
    Real-time snapshot of account margin metrics, as reported by the broker.

    ``excess_liquidity`` and ``available_funds`` are the broker's own cushions and
    are treated as authoritative here. At Interactive Brokers:
    ``Excess Liquidity = Equity with Loan Value - Maintenance Margin`` and
    ``Available Funds = Equity with Loan Value - Initial Margin``. Populate them
    from the broker rather than deriving them from NLV, because Equity with Loan
    Value is not NLV.
    """
    net_liquidation_value: float
    initial_margin: float
    maintenance_margin: float
    excess_liquidity: float
    available_funds: float
    buying_power: float
    currency: str = "USD"

    def validate(self) -> None:
        """Reject unusable values before any threshold comparison is made."""
        _require_finite("net_liquidation_value", self.net_liquidation_value)
        _require_finite("initial_margin", self.initial_margin, allow_negative=False)
        _require_finite("maintenance_margin", self.maintenance_margin, allow_negative=False)
        _require_finite("excess_liquidity", self.excess_liquidity)
        _require_finite("available_funds", self.available_funds)
        _require_finite("buying_power", self.buying_power)


@dataclass
class MarginCallEvaluation:
    """
    Outcome of a margin health check.

    ``maintenance_margin_ratio`` is ``math.inf`` when net liquidation value is
    zero or negative: the ratio is genuinely undefined there, and infinity is the
    honest encoding that also sorts correctly against every threshold.

    ``broker_deficiency`` is True when the broker's own cushion
    (``excess_liquidity``) is negative. That is the condition under which
    positions may actually be liquidated, and it can be True while the house
    ratio still looks healthy.
    """
    state: MarginState
    maintenance_margin_ratio: float
    initial_margin_ratio: float
    maintenance_deficit: float
    action_required: str
    message: str
    broker_deficiency: bool = False


@dataclass
class PositionMarginInfo:
    """Detailed position metrics for de-leveraging algorithms."""
    symbol: str
    asset_class: str  # e.g., 'STK', 'OPT', 'FUT'
    quantity: float
    current_price: float
    maintenance_margin_requirement: float
    # Required, deliberately: this caps how much of the position the plan is
    # willing to sell into the market. A default would silently assert liquidity
    # the position may not have, and the whole point of the cap is to stop a
    # forced sale from crushing the price of a name that cannot absorb it.
    average_daily_volume: float
    is_short_option: bool = False
    beta_to_benchmark: float = 1.0

    def validate(self) -> None:
        _require_finite(f"{self.symbol}.quantity", self.quantity)
        _require_finite(f"{self.symbol}.current_price", self.current_price, allow_negative=False)
        _require_finite(
            f"{self.symbol}.maintenance_margin_requirement",
            self.maintenance_margin_requirement,
            allow_negative=False,
        )
        _require_finite(
            f"{self.symbol}.average_daily_volume",
            self.average_daily_volume,
            allow_negative=False,
        )


class BrokerMarginCallEngine:
    """
    Production-grade margin utilization calculator and automated de-leveraging engine.
    Supports multi-tiered risk gates and sophisticated liquidity-aware position unwinding.
    """

    def __init__(
        self,
        warning_threshold: float = 0.85,
        critical_threshold: float = 0.95,
        breach_threshold: float = 1.00,
        target_post_deleverage_ratio: float = 0.75,
        max_participation_rate: float = 0.10,
        liquidation_buffer_multiplier: float = 1.05
    ):
        """
        :param target_post_deleverage_ratio: Target maintenance margin ratio after forced liquidations.
        :param max_participation_rate: Max % of ADV to trade during a liquidation slice.
        :param liquidation_buffer_multiplier: Multiplier on required reduction to account for slippage.

        Thresholds are house policy, not regulation, but they must be internally
        consistent: unordered thresholds silently make a tier unreachable (with
        warning above critical, an account jumps straight to CRITICAL and the
        WARNING gate never fires), and a de-leverage target at or above the breach
        threshold aims the recovery at a state that is itself a breach.
        """
        for name, value in (
            ("warning_threshold", warning_threshold),
            ("critical_threshold", critical_threshold),
            ("breach_threshold", breach_threshold),
            ("target_post_deleverage_ratio", target_post_deleverage_ratio),
            ("max_participation_rate", max_participation_rate),
            ("liquidation_buffer_multiplier", liquidation_buffer_multiplier),
        ):
            _require_finite(name, value, allow_negative=False)

        if not warning_threshold < critical_threshold <= breach_threshold:
            raise MarginDataError(
                f"Thresholds must satisfy warning < critical <= breach, got "
                f"warning={warning_threshold}, critical={critical_threshold}, "
                f"breach={breach_threshold}. Unordered thresholds make a tier unreachable."
            )
        if not 0 < target_post_deleverage_ratio < breach_threshold:
            raise MarginDataError(
                f"target_post_deleverage_ratio must be > 0 and < breach_threshold "
                f"({breach_threshold}), got {target_post_deleverage_ratio}. De-leveraging "
                f"toward a ratio at or above the breach threshold cannot clear the breach."
            )
        if not 0 < max_participation_rate <= 1.0:
            raise MarginDataError(
                f"max_participation_rate must be in (0, 1], got {max_participation_rate}"
            )
        if liquidation_buffer_multiplier < 1.0:
            raise MarginDataError(
                f"liquidation_buffer_multiplier must be >= 1.0, got "
                f"{liquidation_buffer_multiplier}; a multiplier below 1 under-liquidates "
                f"relative to the computed requirement."
            )

        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.breach_threshold = breach_threshold
        self.target_ratio = target_post_deleverage_ratio
        self.max_participation_rate = max_participation_rate
        self.liquidation_buffer_multiplier = liquidation_buffer_multiplier

    def evaluate_margin_health(self, snapshot: AccountMarginSnapshot) -> MarginCallEvaluation:
        """
        Evaluates margin utilisation and determines risk state.

        Escalates on the worse of two signals: the configurable house ratio
        ``maintenance_margin / NLV``, and the broker's own ``excess_liquidity``
        cushion. The second exists because the first can look healthy while the
        account is already in deficiency at the broker — Equity with Loan Value
        is not NLV, and it is the broker's number that decides whether positions
        get liquidated.

        Raises:
            MarginDataError: any snapshot value is non-finite or negative where it
                must not be. The engine refuses to grade an account it cannot
                measure rather than reporting it healthy.
        """
        snapshot.validate()

        nlv = snapshot.net_liquidation_value
        # The ratio is undefined at or below zero equity. Infinity is the honest
        # encoding and compares correctly against every threshold; the previous
        # 0.01 floor produced a finite ratio and, worse, a deficit understated by
        # exactly the amount of negative equity.
        if nlv > 0:
            maint_ratio = snapshot.maintenance_margin / nlv
            init_ratio = snapshot.initial_margin / nlv
        else:
            maint_ratio = math.inf
            init_ratio = math.inf
        deficit = max(0.0, snapshot.maintenance_margin - nlv)
        broker_deficiency = snapshot.excess_liquidity < 0

        if nlv <= 0:
            msg = (
                f"NEGATIVE/ZERO EQUITY! NLV is ${nlv:,.2f} with maintenance margin "
                f"${snapshot.maintenance_margin:,.2f}. Deficit: ${deficit:,.2f}. De-leveraging "
                f"cannot restore a ratio against non-positive equity — halt trading and escalate."
            )
            logger.critical(msg)
            return MarginCallEvaluation(
                state=MarginState.MARGIN_CALL_BREACH,
                maintenance_margin_ratio=maint_ratio,
                initial_margin_ratio=init_ratio,
                maintenance_deficit=deficit,
                action_required="HALT_AND_ESCALATE",
                message=msg,
                broker_deficiency=broker_deficiency,
            )

        if broker_deficiency and maint_ratio < self.breach_threshold:
            # The broker's cushion is the one that triggers liquidation. Reporting
            # NORMAL here because the house ratio looks fine is the failure this
            # check exists to prevent.
            msg = (
                f"BROKER DEFICIENCY! Excess liquidity is ${snapshot.excess_liquidity:,.2f} "
                f"(negative) even though the house ratio is {maint_ratio:.1%}. The account no "
                f"longer meets maintenance requirements and positions may be liquidated — Equity "
                f"with Loan Value is below NLV. Treating as BREACH."
            )
            logger.critical(msg)
            return MarginCallEvaluation(
                state=MarginState.MARGIN_CALL_BREACH,
                maintenance_margin_ratio=maint_ratio,
                initial_margin_ratio=init_ratio,
                maintenance_deficit=deficit,
                action_required="DE_LEVERAGE_IMMEDIATELY",
                message=msg,
                broker_deficiency=True,
            )

        if maint_ratio >= self.breach_threshold:
            state = MarginState.MARGIN_CALL_BREACH
            action = "DE_LEVERAGE_IMMEDIATELY"
            msg = f"BREACH! Maintenance Margin (${snapshot.maintenance_margin:,.2f}) >= NLV (${nlv:,.2f}). Deficit: ${deficit:,.2f}"
            logger.critical(msg)
        elif maint_ratio >= self.critical_threshold:
            state = MarginState.CRITICAL
            action = "CANCEL_ALL_PENDING_ORDERS"
            msg = f"CRITICAL! Margin ratio {maint_ratio:.1%} >= {self.critical_threshold:.1%}. Cancelling open orders."
            logger.warning(msg)
        elif maint_ratio >= self.warning_threshold:
            state = MarginState.WARNING
            action = "BLOCK_NEW_POSITIONS"
            msg = f"WARNING! Margin ratio {maint_ratio:.1%} >= {self.warning_threshold:.1%}."
            logger.warning(msg)
        else:
            state = MarginState.NORMAL
            action = "NONE"
            msg = f"Healthy: ratio {maint_ratio:.1%} < {self.warning_threshold:.1%}."

        return MarginCallEvaluation(
            state=state,
            maintenance_margin_ratio=maint_ratio,
            initial_margin_ratio=init_ratio,
            maintenance_deficit=deficit,
            action_required=action,
            message=msg,
            broker_deficiency=broker_deficiency,
        )

    def guard_new_order(
        self,
        snapshot: AccountMarginSnapshot,
        margin_impact: float,
        is_deleveraging: bool = False,
        initial_margin_impact: Optional[float] = None,
    ) -> bool:
        """
        Vetoes new orders under margin stress, accounting for the order's estimated margin impact.

        Returns True if the order may proceed, and raises otherwise — it never
        returns False. Callers must treat any exception as a hard veto rather
        than testing a boolean.

        :param margin_impact: Expected increase in **maintenance** margin from this
            order. Obtain it from the broker rather than estimating: at IBKR that
            is a ``whatIf`` order, whose ``OrderState`` returns ``maintMarginChange``
            and ``initMarginChange``.
        :param is_deleveraging: Exempts risk-reducing orders from the gates, since
            blocking them would trap the account in breach. The order must actually
            reduce margin; a positive ``margin_impact`` contradicts the flag and is
            refused.
        :param initial_margin_impact: Expected increase in **initial** margin. New
            positions are opened against initial margin, which is materially higher
            than maintenance margin (Reg T requires 50% initial on a long margin
            equity purchase against FINRA 4210's 25% maintenance minimum), so a
            maintenance-only projection is the weaker of the two constraints and
            can pass an order the broker will not accept. When supplied it is
            checked against ``available_funds``, which is the broker's own
            "room for new positions" figure.

        Raises:
            MarginDataError: unusable inputs, or ``is_deleveraging`` on an order
                that increases margin.
            MarginCallError: the order is vetoed on risk grounds.
        """
        snapshot.validate()
        _require_finite("margin_impact", margin_impact)
        if initial_margin_impact is not None:
            _require_finite("initial_margin_impact", initial_margin_impact)

        if is_deleveraging:
            if margin_impact > 0:
                raise MarginDataError(
                    f"Order flagged is_deleveraging=True but has a positive margin impact of "
                    f"${margin_impact:,.2f}. A de-leveraging order must reduce margin; refusing "
                    f"to bypass the risk gates for an order that increases it."
                )
            return True

        eval_res = self.evaluate_margin_health(snapshot)

        # Initial-margin gate. Checked before the maintenance projection because
        # it is the binding constraint on opening a position.
        if initial_margin_impact is not None and initial_margin_impact > snapshot.available_funds:
            raise MarginCallError(
                f"Order vetoed! Initial margin impact ${initial_margin_impact:,.2f} exceeds "
                f"available funds ${snapshot.available_funds:,.2f}. New positions are opened "
                f"against initial margin, not maintenance margin."
            )

        # Predictive maintenance check. NLV is known positive here: a non-positive
        # NLV returns BREACH from evaluate_margin_health and is caught below.
        if eval_res.state == MarginState.NORMAL:
            projected_maint_margin = snapshot.maintenance_margin + margin_impact
            projected_ratio = projected_maint_margin / snapshot.net_liquidation_value
            if projected_ratio >= self.warning_threshold:
                raise MarginCallError(
                    f"Order vetoed! Projected margin ratio {projected_ratio:.1%} exceeds warning "
                    f"threshold {self.warning_threshold:.1%}."
                )
            return True

        raise MarginCallError(
            f"Order vetoed under margin stress! Account state is '{eval_res.state.value}'. "
            f"{eval_res.message}"
        )

    def plan_deleveraging(
        self, snapshot: AccountMarginSnapshot, positions: List[PositionMarginInfo]
    ) -> List[Tuple[PositionMarginInfo, float]]:
        """
        Calculates a highly optimized, liquidity-aware position reduction plan to restore margin health.
        Prioritizes:
        1. Short options (unlimited tail risk)
        2. High beta / high margin requirement relative to notional
        3. Highly liquid assets (to minimize slippage)

        **This assumes margin is separable per position** — that closing N units of
        a position releases ``N x (position margin / position size)``. That is
        approximately true under Reg T style fixed-percentage margining. It is
        false under Portfolio Margin and SPAN, which margin the portfolio's
        stressed loss: unwinding one leg of a hedge can *raise* total margin. Under
        those regimes use this output as a candidate ordering and re-price every
        slice through the broker (an IBKR ``whatIf`` order, a SPAN evaluation)
        before sending it.

        Returns an empty plan when the account is not in breach, or when the
        maintenance margin is already at or below target.
        """
        eval_res = self.evaluate_margin_health(snapshot)
        if eval_res.state != MarginState.MARGIN_CALL_BREACH:
            return []

        for pos in positions:
            pos.validate()

        nlv = snapshot.net_liquidation_value
        if nlv <= 0:
            # No positive equity to size a target against: any positive target
            # margin is unreachable, so the only coherent plan is to unwind
            # everything, subject to the liquidity caps.
            logger.critical(
                f"De-leveraging with non-positive NLV (${nlv:,.2f}): targeting full unwind. "
                f"Liquidity caps still apply, so the plan may not clear the deficit in one pass."
            )
            target_margin = 0.0
        else:
            target_margin = nlv * self.target_ratio
        base_required_reduction = snapshot.maintenance_margin - target_margin

        if base_required_reduction <= 0:
            return []

        # Add buffer to account for adverse slippage during liquidation
        required_reduction = base_required_reduction * self.liquidation_buffer_multiplier

        # Score positions for liquidation priority
        def liquidation_score(p: PositionMarginInfo) -> float:
            # Higher score = liquidate first
            score = 0.0
            if p.is_short_option:
                score += 1000.0  # Priority 1: Clear tail risk
            
            # Priority 2: Margin density (margin requirement per $ of notional)
            notional = abs(p.quantity * p.current_price)
            margin_density = p.maintenance_margin_requirement / max(notional, 1.0)
            score += margin_density * 100.0
            
            # Priority 3: Liquidity (highly liquid is easier to dump)
            score += min(p.average_daily_volume / 1e6, 50.0) 
            return score

        sorted_pos = sorted(positions, key=liquidation_score, reverse=True)
        liquidation_plan: List[Tuple[PositionMarginInfo, float]] = []
        accumulated_reduction = 0.0

        for pos in sorted_pos:
            if accumulated_reduction >= required_reduction:
                break

            margin_per_unit = pos.maintenance_margin_requirement / max(abs(pos.quantity), 1e-5)
            needed_reduction = required_reduction - accumulated_reduction

            # Calculate max liquidatable units based on required reduction
            units_to_reduce_for_margin = needed_reduction / max(margin_per_unit, 1e-5)
            
            # Liquidity cap: do not exceed max_participation_rate of ADV to avoid crashing the price
            # (In a true breach, we might override this, but in automated tiered deleveraging we slice)
            max_liquidity_units = pos.average_daily_volume * self.max_participation_rate
            
            units_to_reduce = min(
                abs(pos.quantity), 
                units_to_reduce_for_margin,
                max_liquidity_units
            )
            
            if units_to_reduce < 1e-4:
                continue

            reduction_achieved = units_to_reduce * margin_per_unit
            liquidation_plan.append((pos, units_to_reduce))
            accumulated_reduction += reduction_achieved

        logger.info(
            f"De-leveraging plan generated: {len(liquidation_plan)} positions to reduce, targeted margin reduction: ${accumulated_reduction:,.2f}"
        )
        return liquidation_plan
