"""
strategy-lifecycle-retirement-criteria: deterministic, auditable classifier that
turns a live-vs-backtest performance payload into one of five lifecycle
decisions, so a decaying strategy is retired on a pre-agreed rule rather than on
the conviction of the person who built it.

Purpose
-------
The engine does not measure anything. It *adjudicates*: the caller supplies
already-computed live and backtest statistics, and the engine applies four
pre-declared guardrails and a fixed escalation ladder. Its entire value is that
the rule is written down before the drawdown, is applied identically to every
strategy, and produces a record of which criteria fired, which were skipped, and
which thresholds were in force.

Criteria (all four are house thresholds -- see ``references/standards.md``)
--------------------------------------------------------------------------
1. ``live_information_ratio  <  min_live_information_ratio``   -> ALPHA_DECAY_IR
2. ``live_max_drawdown_pct   >  max_drawdown_multiplier x backtest_max_drawdown_pct``
                                                                -> DRAWDOWN_BREACH
3. ``live_ic_t_stat          <  min_ic_t_stat``                  -> IC_STATISTICAL_DECAY
4. ``drift                   <  max_allowed_performance_drift_pct``
                                                                -> PERFORMANCE_DRIFT

    drift = (live_realized_annual_return_pct - backtest_annual_return_pct)
            / backtest_annual_return_pct * 100

Comparisons are strict. A value sitting *exactly* on a threshold does not
breach: an IR of exactly 0.50 and an IC t-stat of exactly 1.96 both pass.

Escalation ladder
-----------------
    0 breaches, all 4 criteria evaluated        -> ACTIVE_HEALTHY
    0 breaches, any criterion not evaluated     -> NEEDS_REVIEW
    1 breach                                    -> NEEDS_REVIEW
    2 breaches                                  -> REDUCE_ALLOCATION
    >= mandatory_retirement_breach_count (3)    -> MANDATORY_RETIREMENT
    drawdown breach AND live IR < escalation_ir_floor (0.0)
                                                -> MANDATORY_RETIREMENT (override)

The override fires at *two* breaches and is deliberate: a strategy that is both
losing money against its benchmark and drawing down beyond its backtested worst
case has failed on the two axes that matter simultaneously, and waiting for a
third confirmation costs capital. It is reported explicitly in
``escalation_reason`` so a two-breach retirement is never unexplained.

Sign conventions (enforced, not assumed)
----------------------------------------
Both drawdown fields are **positive magnitudes**: a 20% peak-to-trough decline is
``20.0``, never ``-20.0``. This is enforced by validation rather than inferred,
because the negative convention is equally common in the wild and silently
inverts criterion 2 -- under ``-10.0`` backtest / ``-30.0`` live, a drawdown
three times worse than backtested compares as *within* limits and the strategy
is certified healthy. Returns and the IR/t-stat carry their natural signs.

Not-measurable vs. healthy
--------------------------
Criterion 4 is a ratio to the backtested return and is undefined when that
denominator is non-positive, and numerically meaningless when it is close to
zero (a 0.1% backtest return against a 0.05% live return is a 5 basis point
miss, but reads as -50% drift). In those cases ``performance_drift_pct`` is
``None`` and the criterion is recorded in ``skipped_criteria``. It is never
substituted with ``0.0``: a missing measurement must not present as a passing
one. ``return_gap_pct_points`` -- the plain difference in percentage points --
is always defined and is reported alongside as the fallback diagnostic.

A payload with an un-evaluated criterion and no breach is downgraded from
``ACTIVE_HEALTHY`` to ``NEEDS_REVIEW``. ``ACTIVE_HEALTHY`` asserts that all four
guardrails passed, and a consumer reading only ``decision`` must never be told
that on the strength of three.

For the same reason every input is checked for finiteness before use. A ``NaN``
information ratio silently satisfies ``nan < 0.50 -> False`` and every other
comparison, so a fully corrupt payload would otherwise be certified
``ACTIVE_HEALTHY`` with zero breaches. Non-finite input raises.

Limitations (documented, deliberate)
------------------------------------
- **Sample size is the caller's responsibility.** The engine cannot tell a
  10-day live track record from a 10-year one. An IR or IC t-stat over a few
  weeks is noise, and retiring on it destroys good strategies as reliably as
  never retiring destroys capital. Supply ``live_observation_count`` and set
  ``min_live_observations`` to gate the decision; if you do not, no gate exists.
- **The four criteria are not independent.** IR, IC t-stat and return drift all
  degrade together when a signal stops working. "3 of 4 breached" is closer to
  one finding confirmed three ways than to three independent findings; the
  ladder is a severity heuristic, not a statistical test.
- **Equal weighting.** A drawdown breach and an IR breach count the same. If
  your firm considers one materially graver, that judgement belongs in the
  committee review, not in this count.
- **No causal attribution.** The engine says a strategy decayed, never why. A
  market-wide regime shift and a strategy-specific alpha decay look identical
  here -- see ``strategy-performance-decay-detection-vs-market-wide-decay``.
- **The Sharpe fields are diagnostics, not gates.** ``backtest_sharpe`` and
  ``live_sharpe`` are carried into the report for context and are deliberately
  not part of any criterion; the IR criterion already covers risk-adjusted
  performance and double-counting it would distort the breach count.
- **Retirement is a governance decision, not a regulatory one.** No regulator
  prescribes an IR floor, a drawdown multiple, or a t-stat cut-off for
  withdrawing a strategy. See ``references/standards.md`` for what the EU and US
  regimes actually require, and what they do not.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Default minimum live information ratio. Grinold & Kahn's manager-percentile
#: table (Active Portfolio Management, 2nd ed., p. 114) places IR = 0.50 at the
#: 75th percentile of active managers -- i.e. this default retires anything
#: below top-quartile. Aggressive by construction; calibrate per mandate.
DEFAULT_MIN_LIVE_INFORMATION_RATIO = 0.50

#: Default multiple of the backtested max drawdown that live drawdown may reach.
#: House heuristic; no regulator or standards body publishes such a multiple.
DEFAULT_MAX_DRAWDOWN_MULTIPLIER = 1.50

#: Default IC t-statistic floor. 1.96 is the *two-tailed* 5% critical value of
#: the standard normal (the one-sided 5% value is 1.645). See
#: ``references/standards.md`` before describing it as "95% confidence".
DEFAULT_MIN_IC_T_STAT = 1.96

#: Default floor on live-vs-backtest return drift, in percent of the backtested
#: return. -40.0 means "live may retain no less than 60% of backtested return".
DEFAULT_MAX_ALLOWED_PERFORMANCE_DRIFT_PCT = -40.0

#: Backtested annual returns smaller than this magnitude make the ratio-based
#: drift metric numerically meaningless, so the criterion is skipped and
#: disclosed rather than evaluated. House heuristic, in percentage points.
DEFAULT_MIN_BACKTEST_RETURN_FOR_DRIFT_PCT = 1.0

#: Breach count at which retirement becomes mandatory.
DEFAULT_MANDATORY_RETIREMENT_BREACH_COUNT = 3

#: Live IR at or below which a concurrent drawdown breach escalates straight to
#: mandatory retirement, bypassing the breach-count ladder.
DEFAULT_ESCALATION_IR_FLOOR = 0.0


@dataclass
class StrategyLifecycleRetirementCriteriaConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True


class StrategyLifecycleRetirementCriteria:
    """Legacy class retained for 100% backward compatibility."""

    def __init__(self, config: StrategyLifecycleRetirementCriteriaConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


class RetirementDecision(str, Enum):
    ACTIVE_HEALTHY = "ACTIVE_HEALTHY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REDUCE_ALLOCATION = "REDUCE_ALLOCATION"
    MANDATORY_RETIREMENT = "MANDATORY_RETIREMENT"
    #: The live track record is too short for any of the criteria to mean
    #: anything. Emitted only when ``min_live_observations`` is configured.
    INSUFFICIENT_LIVE_HISTORY = "INSUFFICIENT_LIVE_HISTORY"


@dataclass
class StrategyPerformanceMetrics:
    """
    Already-computed live and backtest statistics for one strategy.

    Drawdowns are **positive magnitudes** (a 20% decline is ``20.0``). Returns
    are in percent per annum. All numeric fields must be finite.

    ``live_observation_count`` is the number of live observations (typically
    trading days) behind the live statistics. Optional, but supplying it is the
    only way to activate the ``min_live_observations`` gate -- without it the
    engine cannot distinguish a decayed strategy from a young one.
    """
    strategy_id: str
    backtest_sharpe: float
    backtest_max_drawdown_pct: float
    live_sharpe: float
    live_max_drawdown_pct: float
    live_information_ratio: float
    live_ic_t_stat: float                  # Information Coefficient t-statistic
    live_realized_annual_return_pct: float
    backtest_annual_return_pct: float
    live_observation_count: Optional[int] = None


@dataclass
class StrategyRetirementReport:
    """
    Outcome of one evaluation.

    ``performance_drift_pct`` is ``None`` when the drift criterion could not be
    evaluated -- render it as "not measurable", never coerce it to ``0.0``. Any
    criterion that could not be evaluated is named in ``skipped_criteria``, so
    ``breached_criteria == []`` alone never means "all four criteria passed";
    read it against ``evaluated_criteria_count``. Only a report with zero
    breaches *and* four evaluated criteria carries ``ACTIVE_HEALTHY``.
    """
    strategy_id: str
    decision: RetirementDecision
    is_retired: bool
    breached_criteria: List[str]
    performance_drift_pct: Optional[float]  # (Live - Backtest) / Backtest * 100
    recommended_action: str
    audit_notes: str
    skipped_criteria: List[str] = field(default_factory=list)
    evaluated_criteria_count: int = 0
    return_gap_pct_points: Optional[float] = None
    escalation_reason: Optional[str] = None
    thresholds_applied: Dict[str, float] = field(default_factory=dict)


def _require_finite(value: float, field_name: str, strategy_id: str) -> float:
    """Reject NaN/Inf, which would otherwise pass every threshold comparison."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(
            f"{strategy_id}: '{field_name}' must be a real number, got "
            f"{type(value).__name__}."
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{strategy_id}: '{field_name}' is {value!r}. Non-finite metrics "
            f"silently satisfy every threshold comparison and would certify "
            f"this strategy as healthy on corrupt data."
        )
    return numeric


class StrategyLifecycleRetirementEngine:
    """
    Applies four pre-declared retirement criteria and a fixed escalation ladder
    to a strategy's live-vs-backtest performance payload.

    Every threshold is a house parameter, not an industry or regulatory
    standard. Changing one changes which strategies get retired, so the values
    in force are echoed into ``StrategyRetirementReport.thresholds_applied`` --
    a decision is not reproducible without them.
    """

    def __init__(
        self,
        min_live_information_ratio: float = DEFAULT_MIN_LIVE_INFORMATION_RATIO,
        max_drawdown_multiplier: float = DEFAULT_MAX_DRAWDOWN_MULTIPLIER,
        min_ic_t_stat: float = DEFAULT_MIN_IC_T_STAT,
        max_allowed_performance_drift_pct: float = DEFAULT_MAX_ALLOWED_PERFORMANCE_DRIFT_PCT,
        min_backtest_return_for_drift_pct: float = DEFAULT_MIN_BACKTEST_RETURN_FOR_DRIFT_PCT,
        mandatory_retirement_breach_count: int = DEFAULT_MANDATORY_RETIREMENT_BREACH_COUNT,
        escalation_ir_floor: float = DEFAULT_ESCALATION_IR_FLOOR,
        escalate_on_negative_ir_with_drawdown_breach: bool = True,
        min_live_observations: Optional[int] = None,
    ):
        for name, value in (
            ("min_live_information_ratio", min_live_information_ratio),
            ("max_drawdown_multiplier", max_drawdown_multiplier),
            ("min_ic_t_stat", min_ic_t_stat),
            ("max_allowed_performance_drift_pct", max_allowed_performance_drift_pct),
            ("min_backtest_return_for_drift_pct", min_backtest_return_for_drift_pct),
            ("escalation_ir_floor", escalation_ir_floor),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not math.isfinite(float(value)):
                raise ValueError(f"'{name}' must be a finite number, got {value!r}.")

        if max_drawdown_multiplier <= 0:
            raise ValueError(
                f"'max_drawdown_multiplier' must be > 0, got "
                f"{max_drawdown_multiplier!r}. A non-positive multiplier inverts "
                f"the drawdown gate."
            )
        if min_backtest_return_for_drift_pct <= 0:
            raise ValueError(
                f"'min_backtest_return_for_drift_pct' must be > 0, got "
                f"{min_backtest_return_for_drift_pct!r}."
            )
        if not isinstance(mandatory_retirement_breach_count, int) \
                or isinstance(mandatory_retirement_breach_count, bool) \
                or not 1 <= mandatory_retirement_breach_count <= 4:
            raise ValueError(
                f"'mandatory_retirement_breach_count' must be an int in 1..4 "
                f"(only four criteria exist), got "
                f"{mandatory_retirement_breach_count!r}."
            )
        if min_live_observations is not None:
            if not isinstance(min_live_observations, int) \
                    or isinstance(min_live_observations, bool) \
                    or min_live_observations < 1:
                raise ValueError(
                    f"'min_live_observations' must be a positive int or None, "
                    f"got {min_live_observations!r}."
                )

        self.min_live_ir = float(min_live_information_ratio)
        self.max_dd_mult = float(max_drawdown_multiplier)
        self.min_ic_t_stat = float(min_ic_t_stat)
        self.max_allowed_drift = float(max_allowed_performance_drift_pct)
        self.min_backtest_return_for_drift = float(min_backtest_return_for_drift_pct)
        self.mandatory_retirement_breach_count = mandatory_retirement_breach_count
        self.escalation_ir_floor = float(escalation_ir_floor)
        self.escalate_on_negative_ir_with_dd = bool(
            escalate_on_negative_ir_with_drawdown_breach
        )
        self.min_live_observations = min_live_observations

    def _validate(self, metrics: StrategyPerformanceMetrics) -> None:
        """Reject payloads whose defects would produce a false ACTIVE_HEALTHY."""
        if not isinstance(metrics.strategy_id, str) or not metrics.strategy_id.strip():
            raise ValueError("'strategy_id' must be a non-empty string.")
        sid = metrics.strategy_id

        for name in (
            "backtest_sharpe",
            "backtest_max_drawdown_pct",
            "live_sharpe",
            "live_max_drawdown_pct",
            "live_information_ratio",
            "live_ic_t_stat",
            "live_realized_annual_return_pct",
            "backtest_annual_return_pct",
        ):
            _require_finite(getattr(metrics, name), name, sid)

        # Drawdowns are positive magnitudes. The negative convention is common
        # enough that guessing is unsafe: under it, criterion 2 inverts and a
        # live drawdown far worse than backtested compares as within limits.
        for name in ("backtest_max_drawdown_pct", "live_max_drawdown_pct"):
            value = float(getattr(metrics, name))
            if value < 0:
                raise ValueError(
                    f"{sid}: '{name}' is {value}. Drawdowns must be supplied as "
                    f"positive magnitudes (a 20% decline is 20.0). The negative "
                    f"convention inverts the drawdown criterion and would let a "
                    f"breach pass as healthy."
                )
        if float(metrics.backtest_max_drawdown_pct) == 0.0:
            raise ValueError(
                f"{sid}: 'backtest_max_drawdown_pct' is 0.0, which is almost "
                f"always an unpopulated field. It would set the allowed live "
                f"drawdown to 0.0% and make the criterion fire on any drawdown "
                f"at all. Supply the real backtested maximum drawdown."
            )

        if metrics.live_observation_count is not None:
            count = metrics.live_observation_count
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(
                    f"{sid}: 'live_observation_count' must be a non-negative "
                    f"int or None, got {count!r}."
                )

    def _compute_drift(
        self, metrics: StrategyPerformanceMetrics
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Return ``(drift_pct_or_None, skip_reason_or_None)``.

        The ratio is undefined for a non-positive backtested return and
        numerically meaningless for a near-zero one. Both cases yield ``None``
        plus a reason -- never a fabricated ``0.0``.
        """
        backtest_return = float(metrics.backtest_annual_return_pct)

        if backtest_return <= 0:
            return None, (
                f"PERFORMANCE_DRIFT: not evaluated -- backtest annual return is "
                f"{backtest_return:.2f}%, so drift relative to it is undefined. "
                f"Compare the {metrics.live_realized_annual_return_pct - backtest_return:+.2f} "
                f"pct-point return gap manually."
            )
        if backtest_return < self.min_backtest_return_for_drift:
            return None, (
                f"PERFORMANCE_DRIFT: not evaluated -- backtest annual return "
                f"({backtest_return:.2f}%) is below the "
                f"{self.min_backtest_return_for_drift:.2f}% floor at which a "
                f"ratio-based drift becomes numerically meaningless."
            )

        drift = (
            (float(metrics.live_realized_annual_return_pct) - backtest_return)
            / backtest_return
        ) * 100.0
        return drift, None

    def evaluate_strategy(
        self, metrics: StrategyPerformanceMetrics
    ) -> StrategyRetirementReport:
        """
        Evaluate one strategy against the four retirement criteria.

        Raises ``ValueError``/``TypeError`` on any payload that could produce a
        false ``ACTIVE_HEALTHY``: non-finite metrics, negative drawdowns, a zero
        backtested drawdown, or a bad observation count.
        """
        self._validate(metrics)

        breaches: List[str] = []
        skipped: List[str] = []
        evaluated = 0

        # 1. Information Ratio.
        evaluated += 1
        if metrics.live_information_ratio < self.min_live_ir:
            breaches.append(
                f"ALPHA_DECAY_IR: Live Information Ratio "
                f"({metrics.live_information_ratio:.2f}) < Min Threshold "
                f"({self.min_live_ir:.2f})."
            )

        # 2. Drawdown multiplier.
        evaluated += 1
        allowed_max_dd = metrics.backtest_max_drawdown_pct * self.max_dd_mult
        dd_breached = metrics.live_max_drawdown_pct > allowed_max_dd
        if dd_breached:
            breaches.append(
                f"DRAWDOWN_BREACH: Live Max Drawdown "
                f"({metrics.live_max_drawdown_pct:.1f}%) > Allowed Limit "
                f"({allowed_max_dd:.1f}% = {self.max_dd_mult}x backtest DD)."
            )

        # 3. Information Coefficient t-statistic.
        evaluated += 1
        if metrics.live_ic_t_stat < self.min_ic_t_stat:
            breaches.append(
                f"IC_STATISTICAL_DECAY: Live IC t-stat "
                f"({metrics.live_ic_t_stat:.2f}) < Required Threshold "
                f"({self.min_ic_t_stat})."
            )

        # 4. Live-vs-backtest return drift.
        drift, drift_skip_reason = self._compute_drift(metrics)
        return_gap = round(
            float(metrics.live_realized_annual_return_pct)
            - float(metrics.backtest_annual_return_pct),
            4,
        )
        if drift_skip_reason is not None:
            skipped.append(drift_skip_reason)
        else:
            evaluated += 1
            if drift < self.max_allowed_drift:
                breaches.append(
                    f"PERFORMANCE_DRIFT: Live vs Backtest return drift "
                    f"({drift:.1f}%) < Max Allowed Limit "
                    f"({self.max_allowed_drift}%)."
                )

        thresholds = {
            "min_live_information_ratio": self.min_live_ir,
            "max_drawdown_multiplier": self.max_dd_mult,
            "min_ic_t_stat": self.min_ic_t_stat,
            "max_allowed_performance_drift_pct": self.max_allowed_drift,
            "min_backtest_return_for_drift_pct": self.min_backtest_return_for_drift,
            "mandatory_retirement_breach_count": float(
                self.mandatory_retirement_breach_count
            ),
            "escalation_ir_floor": self.escalation_ir_floor,
        }

        # Sample-size gate. A strategy cannot be retired on evidence that does
        # not exist yet; breaches are still reported, but the ladder is not run.
        if (
            self.min_live_observations is not None
            and metrics.live_observation_count is not None
            and metrics.live_observation_count < self.min_live_observations
        ):
            action = (
                f"EXTEND_OBSERVATION: {metrics.live_observation_count} live "
                f"observations is below the {self.min_live_observations} "
                f"required for these criteria to carry signal. Do not retire on "
                f"this evidence; continue monitoring at reduced size."
            )
            notes = (
                f"RETIREMENT REPORT [{RetirementDecision.INSUFFICIENT_LIVE_HISTORY.value}] "
                f"({metrics.strategy_id}): Breaches (informational) = "
                f"{len(breaches)}/{evaluated}, live observations = "
                f"{metrics.live_observation_count} < "
                f"{self.min_live_observations}, Action = {action}"
            )
            logger.warning(notes)
            return StrategyRetirementReport(
                strategy_id=metrics.strategy_id,
                decision=RetirementDecision.INSUFFICIENT_LIVE_HISTORY,
                is_retired=False,
                breached_criteria=breaches,
                performance_drift_pct=None if drift is None else round(drift, 2),
                recommended_action=action,
                audit_notes=notes,
                skipped_criteria=skipped,
                evaluated_criteria_count=evaluated,
                return_gap_pct_points=return_gap,
                escalation_reason=None,
                thresholds_applied=thresholds,
            )

        escalation_reason: Optional[str] = None
        escalated = (
            self.escalate_on_negative_ir_with_dd
            and dd_breached
            and metrics.live_information_ratio < self.escalation_ir_floor
        )

        if len(breaches) >= self.mandatory_retirement_breach_count or escalated:
            decision = RetirementDecision.MANDATORY_RETIREMENT
            is_retired = True
            action = (
                "DECOMMISSION_IMMEDIATELY: Initiate hard order entry block and "
                "position unwind."
            )
            if escalated and len(breaches) < self.mandatory_retirement_breach_count:
                escalation_reason = (
                    f"OVERRIDE_DD_AND_NEGATIVE_IR: drawdown breach with live IR "
                    f"({metrics.live_information_ratio:.2f}) below "
                    f"{self.escalation_ir_floor:.2f} escalates to mandatory "
                    f"retirement at {len(breaches)} breaches, ahead of the "
                    f"{self.mandatory_retirement_breach_count}-breach ladder."
                )
        elif len(breaches) == 2:
            decision = RetirementDecision.REDUCE_ALLOCATION
            is_retired = False
            action = (
                "CUT_CAPITAL_50%: Reduce strategy allocation by 50% pending "
                "committee review."
            )
        elif len(breaches) == 1:
            decision = RetirementDecision.NEEDS_REVIEW
            is_retired = False
            action = "FLAG_FOR_REVIEW: Place strategy on high-frequency watch list."
        elif skipped:
            # No breach, but at least one guardrail could not be evaluated.
            # ACTIVE_HEALTHY asserts that every criterion passed, and that
            # assertion is not supported here -- a consumer reading only
            # ``decision`` would be told a strategy is clean when one of its
            # four checks never ran. Refuse to certify without inventing a
            # breach the data does not show.
            decision = RetirementDecision.NEEDS_REVIEW
            is_retired = False
            action = (
                f"FLAG_FOR_REVIEW: No criterion breached, but "
                f"{len(skipped)} of 4 could not be evaluated on this payload. "
                f"Assess the un-evaluated criterion manually before treating "
                f"this strategy as healthy."
            )
        else:
            decision = RetirementDecision.ACTIVE_HEALTHY
            is_retired = False
            action = (
                "MAINTAIN_ALLOCATION: Strategy operating within healthy "
                "quantitative guardrails."
            )

        notes = (
            f"RETIREMENT REPORT [{decision.value}] ({metrics.strategy_id}): "
            f"Breaches = {len(breaches)}/{evaluated} evaluated criteria, "
            f"Live IR = {metrics.live_information_ratio:.2f}, "
            f"Live DD = {metrics.live_max_drawdown_pct:.1f}%, "
            f"Action = {action}"
        )
        if skipped:
            notes += f" | NOT EVALUATED: {len(skipped)} criterion/criteria -- {'; '.join(skipped)}"
        if escalation_reason:
            notes += f" | ESCALATION: {escalation_reason}"

        if is_retired:
            logger.error(notes)
        elif decision in (
            RetirementDecision.REDUCE_ALLOCATION,
            RetirementDecision.NEEDS_REVIEW,
        ):
            logger.warning(notes)
        else:
            logger.info(notes)

        return StrategyRetirementReport(
            strategy_id=metrics.strategy_id,
            decision=decision,
            is_retired=is_retired,
            breached_criteria=breaches,
            performance_drift_pct=None if drift is None else round(drift, 2),
            recommended_action=action,
            audit_notes=notes,
            skipped_criteria=skipped,
            evaluated_criteria_count=evaluated,
            return_gap_pct_points=return_gap,
            escalation_reason=escalation_reason,
            thresholds_applied=thresholds,
        )
