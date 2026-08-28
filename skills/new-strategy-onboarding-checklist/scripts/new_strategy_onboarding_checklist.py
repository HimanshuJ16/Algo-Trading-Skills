"""
new-strategy-onboarding-checklist: four-gate governance evaluator deciding whether
a newly researched quantitative strategy may be placed in front of live capital.

Purpose
-------
Replace "does everyone feel good about this strategy?" with a conjunctive,
recorded, reproducible decision across four gates:

    BACKTEST_GATE     walk-forward score, market-regime coverage, backtest Sharpe
    OPERATIONAL_GATE  paper-trading duration, clean-run requirement, kill switch
    MODEL_RISK_GATE   a completed model card exists
    COMPLIANCE_GATE   compliance sign-off is recorded

All four must pass. There is no weighting, no partial credit, no waiver path and no
override flag. ``total_gates_passed`` exists to diagnose *why* a package was
refused; 3/4 is exactly as rejected as 0/4.

What this engine actually checks
--------------------------------
**Attestations, not artifacts.** ``model_card_completed=True`` means somebody
asserted True. The engine does not open the model card, re-run the backtest, query
the paper-trading logs, or contact the compliance officer. Its value is that the
claims are captured together, against named and recorded thresholds, in one
reproducible record -- not that any individual claim has been verified.

Three consequences that must be designed around rather than assumed away:

- **Segregation of duties is the caller's responsibility.** If the strategy author
  populates every field, this gate certifies the author's own opinion of their own
  strategy. Each flag should originate from whoever owns that control -- risk for
  the kill switch, model risk for the card, compliance for the sign-off. The
  ``author`` field records who submitted the package, not who attested to it.
- **A clean report is not an authorisation.** For an EU or UK investment firm,
  MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) Article 5(2)
  requires that "[a] person designated by the senior management of the investment
  firm shall authorise the deployment or substantial update of an algorithmic
  trading system, trading algorithm or algorithmic trading strategy." This report
  is an input to that human decision, never a substitute for it.
- **This is not a pre-trade control.** ``ONBOARDING_PASSED`` grants no capital,
  sets no limit and blocks no order. The controls that actually bound a live
  strategy are separate: RTS 6 Article 8 predefined deployment limits, and for a
  US broker-dealer with market access the pre-set credit/capital thresholds of
  SEC Rule 15c3-5(c)(1)(i).

Threshold provenance
--------------------
Every default in ``OnboardingPolicyConfig`` is a **house heuristic**, not a
published, regulatory or industry standard. RTS 6 prescribes *that* an algorithm
be tested before deployment and *what* the testing must establish (Article 5(4));
it sets no minimum testing duration and names no performance metric. Nothing in
RTS 6 or SEC Rule 15c3-5 mandates fourteen days of paper trading, three market
regimes, a walk-forward score of 0.70, or a Sharpe ratio of 1.5. Calibrate the
numbers to your asset class and holding period, and record the ones you used --
which is why ``policy_applied`` is embedded in every report.

The Sharpe floor deserves particular suspicion. Selecting strategies on an
*in-sample* backtest Sharpe rewards the most heavily over-fitted candidate in a
research pipeline: the maximum over many trials exceeds the mean by construction,
and the overstatement grows with the number of trials. Treat ``min_backtest_sharpe``
as a floor that excludes visibly broken strategies, never as evidence of edge, and
correct for trial count downstream -- see Bailey and Lopez de Prado (2014), "The
Deflated Sharpe Ratio", and this repository's
``factor-research-multiple-testing-correction``.

Limitations (documented, deliberate)
------------------------------------
- **The engine defines no metric.** ``walk_forward_score`` has no scale imposed
  here; whatever convention produced the number must be the same convention that
  set the threshold. The same applies to the annualisation behind
  ``backtest_sharpe`` and to whether ``paper_trading_days`` counts calendar or
  trading days -- 14 calendar days is roughly 10 trading days.
- **Duration is not coverage.** Fourteen quiet days of paper trading demonstrate
  that the plumbing works, not that the strategy survives stress. Regime coverage
  of the *backtest* is Gate 1's ``regimes_covered``; regime coverage of the *paper
  period* is not measured by anything here.
- **A zero error count is only as good as the error detection behind it.**
  ``paper_trading_errors=0`` from a system that never logged errors is
  indistinguishable from a genuinely clean run.
- **Model card contents are not inspected.** Gate 3 records that a model card
  exists. Whether it documents parameter limits, decay conditions and known
  failure modes is a review question -- see
  ``model-card-documentation-for-trading-models``.
- **Stateless and single-strategy.** No history, no re-audit tracking, no portfolio
  view. A strategy that failed yesterday and passes today looks identical to one
  that passed first time; persist the reports yourself.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: House default: minimum walk-forward score. Scale is caller-defined -- the
#: threshold and the score must come from the same convention. Not a standard.
DEFAULT_MIN_WALK_FORWARD_SCORE = 0.70

#: House default: distinct market regimes the backtest must have covered. Pairs
#: with ``multi-year-regime-coverage-requirement``, which counts them. Not a standard.
DEFAULT_MIN_REGIMES_COVERED = 3

#: House default: backtest Sharpe floor. A screen against visibly broken
#: strategies, NOT evidence of edge -- see "Threshold provenance" above.
DEFAULT_MIN_BACKTEST_SHARPE = 1.5

#: House default: minimum paper-trading days. Calendar-vs-trading days is the
#: caller's convention. No regulator mandates a paper-trading period.
DEFAULT_MIN_PAPER_TRADING_DAYS = 14

#: House default: tolerated critical execution errors during the paper period.
#: Zero is the only defensible default for a control-integrity gate.
DEFAULT_MAX_PAPER_TRADING_ERRORS = 0

STATUS_PASSED = "ONBOARDING_PASSED"
STATUS_REJECTED = "ONBOARDING_REJECTED"

GATE_BACKTEST = "BACKTEST_GATE"
GATE_OPERATIONAL = "OPERATIONAL_GATE"
GATE_MODEL_RISK = "MODEL_RISK_GATE"
GATE_COMPLIANCE = "COMPLIANCE_GATE"


def _require_non_empty_str(value: Any, name: str) -> str:
    """A governance record keyed by a blank identifier is not an audit record."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_finite(value: Any, name: str) -> float:
    """
    Reject NaN and Inf on any field a gate compares.

    ``NaN`` fails every comparison, so a corrupt walk-forward score would be
    reported as a *strategy* failure rather than a *data* failure -- the two demand
    different responses from different teams. ``Inf`` is worse: an infinite Sharpe
    ratio is what a zero-variance return series produces, i.e. a degenerate or
    broken backtest, and it clears any finite floor.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be finite, got {value!r}. A non-finite input is corrupt "
            f"upstream data, not a strategy that failed the gate."
        )
    return numeric


def _require_non_negative_int(value: Any, name: str) -> int:
    """``bool`` is a subclass of ``int``; ``True`` must not silently become 1 day."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return value


def _require_bool(value: Any, name: str) -> bool:
    """
    Require a real ``bool`` -- never coerce by truthiness.

    This is the single most important validation in the module. Every non-empty
    string is truthy, so a payload assembled from CSV, JSON, YAML or by an LLM
    agent can carry ``"false"``, ``"NO"`` or ``"pending"`` and pass the very gate
    that exists to stop that strategy. Truthiness would also leak a non-``bool``
    into ``GateEvaluationResult.passed``, corrupting the serialised audit record.
    """
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} must be a bool, got {value!r}. Attestation flags are never "
            f"coerced by truthiness -- a string such as 'false' is truthy and "
            f"would pass this gate."
        )
    return value


@dataclass
class StrategyOnboardingPayload:
    """
    One strategy's onboarding package. Every field is an **attestation** supplied by
    the caller; nothing here is independently verified by this module.
    """
    strategy_id: str
    strategy_name: str
    author: str                          # who submitted the package, not who attested
    walk_forward_score: float            # caller-defined scale; must match the threshold
    regimes_covered: int                 # distinct market regimes in the backtest
    backtest_sharpe: float               # in-sample; a floor, never evidence of edge
    paper_trading_days: int              # calendar or trading days -- caller's convention
    paper_trading_errors: int            # critical execution errors during the paper run
    kill_switch_integrated: bool
    model_card_completed: bool
    compliance_approved: bool

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """
        Validate and normalise in place.

        Called at construction and again by the engine, because a dataclass field
        can be reassigned after ``__post_init__`` has run and the gate, not the
        constructor, is the enforcement point.
        """
        self.strategy_id = _require_non_empty_str(self.strategy_id, "strategy_id")
        self.strategy_name = _require_non_empty_str(self.strategy_name, "strategy_name")
        self.author = _require_non_empty_str(self.author, "author")
        self.walk_forward_score = _require_finite(
            self.walk_forward_score, "walk_forward_score")
        self.regimes_covered = _require_non_negative_int(
            self.regimes_covered, "regimes_covered")
        self.backtest_sharpe = _require_finite(self.backtest_sharpe, "backtest_sharpe")
        self.paper_trading_days = _require_non_negative_int(
            self.paper_trading_days, "paper_trading_days")
        self.paper_trading_errors = _require_non_negative_int(
            self.paper_trading_errors, "paper_trading_errors")
        self.kill_switch_integrated = _require_bool(
            self.kill_switch_integrated, "kill_switch_integrated")
        self.model_card_completed = _require_bool(
            self.model_card_completed, "model_card_completed")
        self.compliance_approved = _require_bool(
            self.compliance_approved, "compliance_approved")


@dataclass
class OnboardingPolicyConfig:
    """
    Thresholds applied by the gate. All defaults are house heuristics -- see the
    module docstring. Whatever is set here is recorded in the report's
    ``policy_applied`` so a reviewer can see which bar was actually cleared.
    """
    min_walk_forward_score: float = DEFAULT_MIN_WALK_FORWARD_SCORE
    min_regimes_covered: int = DEFAULT_MIN_REGIMES_COVERED
    min_backtest_sharpe: float = DEFAULT_MIN_BACKTEST_SHARPE
    min_paper_trading_days: int = DEFAULT_MIN_PAPER_TRADING_DAYS
    max_paper_trading_errors: int = DEFAULT_MAX_PAPER_TRADING_ERRORS

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate and normalise in place; re-run by the engine before each audit."""
        self.min_walk_forward_score = _require_finite(
            self.min_walk_forward_score, "min_walk_forward_score")
        self.min_backtest_sharpe = _require_finite(
            self.min_backtest_sharpe, "min_backtest_sharpe")
        self.min_regimes_covered = _require_non_negative_int(
            self.min_regimes_covered, "min_regimes_covered")
        self.min_paper_trading_days = _require_non_negative_int(
            self.min_paper_trading_days, "min_paper_trading_days")
        self.max_paper_trading_errors = _require_non_negative_int(
            self.max_paper_trading_errors, "max_paper_trading_errors")

    def as_dict(self) -> Dict[str, Any]:
        """Threshold snapshot embedded in every report, so an audit is reproducible."""
        return {
            "min_walk_forward_score": self.min_walk_forward_score,
            "min_regimes_covered": self.min_regimes_covered,
            "min_backtest_sharpe": self.min_backtest_sharpe,
            "min_paper_trading_days": self.min_paper_trading_days,
            "max_paper_trading_errors": self.max_paper_trading_errors,
        }

    def weakened_thresholds(self) -> List[str]:
        """
        Thresholds set more permissively than the shipped defaults.

        A config of all zeros passes every strategy while still emitting
        ``ONBOARDING_PASSED``. Naming the relaxations makes a deliberately loosened
        gate visible in the record instead of indistinguishable from a strict one.
        """
        relaxations = [
            ("min_walk_forward_score", self.min_walk_forward_score,
             DEFAULT_MIN_WALK_FORWARD_SCORE),
            ("min_regimes_covered", self.min_regimes_covered,
             DEFAULT_MIN_REGIMES_COVERED),
            ("min_backtest_sharpe", self.min_backtest_sharpe,
             DEFAULT_MIN_BACKTEST_SHARPE),
            ("min_paper_trading_days", self.min_paper_trading_days,
             DEFAULT_MIN_PAPER_TRADING_DAYS),
        ]
        weakened = [
            f"{name}: {applied} < default {default}"
            for name, applied, default in relaxations
            if applied < default
        ]
        if self.max_paper_trading_errors > DEFAULT_MAX_PAPER_TRADING_ERRORS:
            weakened.append(
                f"max_paper_trading_errors: {self.max_paper_trading_errors} > default "
                f"{DEFAULT_MAX_PAPER_TRADING_ERRORS}"
            )
        return weakened


@dataclass
class GateEvaluationResult:
    gate_name: str                       # 'BACKTEST_GATE', 'OPERATIONAL_GATE', 'MODEL_RISK_GATE', 'COMPLIANCE_GATE'
    passed: bool
    details: str
    failed_criteria: List[str] = field(default_factory=list)


@dataclass
class OnboardingAuditReport:
    strategy_id: str
    strategy_name: str
    gates_evaluated: List[GateEvaluationResult]
    total_gates_passed: int
    total_gates_count: int
    is_onboarding_approved: bool
    status: str                          # 'ONBOARDING_PASSED', 'ONBOARDING_REJECTED'
    audit_notes: str
    policy_applied: Dict[str, Any] = field(default_factory=dict)
    failed_gates: List[str] = field(default_factory=list)
    policy_weakened: List[str] = field(default_factory=list)


class NewStrategyOnboardingEngine:
    """
    New strategy onboarding gatekeeper engine evaluating 4-gate governance standards
    (Backtest Robustness, Operational Runtime, Model Risk, Compliance Approval).

    The gates are conjunctive: ``is_onboarding_approved`` is True only when all four
    pass. Passing certifies that four attestations were made against recorded
    thresholds -- it does not verify them, allocate capital, or authorise deployment.
    """

    def __init__(self, config: Optional[OnboardingPolicyConfig] = None) -> None:
        if config is not None and not isinstance(config, OnboardingPolicyConfig):
            raise ValueError(
                f"config must be an OnboardingPolicyConfig, got {config!r}")
        self.config = config or OnboardingPolicyConfig()
        self.config.validate()
        weakened = self.config.weakened_thresholds()
        if weakened:
            logger.warning(
                "Onboarding policy is weaker than the shipped defaults: %s",
                "; ".join(weakened),
            )

    def audit_strategy_onboarding(
        self, payload: StrategyOnboardingPayload
    ) -> OnboardingAuditReport:
        """
        Evaluate the four governance gates and return an auditable report.

        Raises ``ValueError`` on a malformed payload or policy rather than returning
        a verdict: corrupt input is a data failure, and reporting it as
        ``ONBOARDING_REJECTED`` would send the wrong team to investigate.
        """
        if not isinstance(payload, StrategyOnboardingPayload):
            raise ValueError(
                f"payload must be a StrategyOnboardingPayload, got {payload!r}")
        payload.validate()
        self.config.validate()

        gates: List[GateEvaluationResult] = [
            self._evaluate_backtest_gate(payload),
            self._evaluate_operational_gate(payload),
            self._evaluate_model_risk_gate(payload),
            self._evaluate_compliance_gate(payload),
        ]

        passed_count = sum(1 for g in gates if g.passed)
        total_count = len(gates)

        is_approved = passed_count == total_count
        status = STATUS_PASSED if is_approved else STATUS_REJECTED
        failed_gates = [g.gate_name for g in gates if not g.passed]

        notes = (
            f"ONBOARDING AUDIT [{payload.strategy_id} - {status}]: "
            f"Passed {passed_count}/{total_count} Governance Gates."
        )
        if failed_gates:
            reasons = "; ".join(
                f"{g.gate_name} ({', '.join(g.failed_criteria)})"
                for g in gates if not g.passed
            )
            notes += f" Failed Gates: {reasons}."
            logger.warning(notes)
        else:
            logger.info(notes)

        return OnboardingAuditReport(
            strategy_id=payload.strategy_id,
            strategy_name=payload.strategy_name,
            gates_evaluated=gates,
            total_gates_passed=passed_count,
            total_gates_count=total_count,
            is_onboarding_approved=is_approved,
            status=status,
            audit_notes=notes,
            policy_applied=self.config.as_dict(),
            failed_gates=failed_gates,
            policy_weakened=self.config.weakened_thresholds(),
        )

    def _evaluate_backtest_gate(
        self, payload: StrategyOnboardingPayload
    ) -> GateEvaluationResult:
        """Gate 1 -- backtest robustness. Every criterion must clear its floor."""
        cfg = self.config
        failed: List[str] = []
        if payload.walk_forward_score < cfg.min_walk_forward_score:
            failed.append(
                f"walk_forward_score: {payload.walk_forward_score:.2f} < "
                f"{cfg.min_walk_forward_score:.2f}")
        if payload.regimes_covered < cfg.min_regimes_covered:
            failed.append(
                f"regimes_covered: {payload.regimes_covered} < {cfg.min_regimes_covered}")
        if payload.backtest_sharpe < cfg.min_backtest_sharpe:
            failed.append(
                f"backtest_sharpe: {payload.backtest_sharpe:.2f} < "
                f"{cfg.min_backtest_sharpe:.2f}")
        details = (
            f"WF Score: {payload.walk_forward_score:.2f} (min {cfg.min_walk_forward_score:.2f}), "
            f"Regimes: {payload.regimes_covered} (min {cfg.min_regimes_covered}), "
            f"Sharpe: {payload.backtest_sharpe:.2f} (min {cfg.min_backtest_sharpe:.2f})."
        )
        return GateEvaluationResult(GATE_BACKTEST, not failed, details, failed)

    def _evaluate_operational_gate(
        self, payload: StrategyOnboardingPayload
    ) -> GateEvaluationResult:
        """Gate 2 -- paper-trading runtime and halt capability."""
        cfg = self.config
        failed: List[str] = []
        if payload.paper_trading_days < cfg.min_paper_trading_days:
            failed.append(
                f"paper_trading_days: {payload.paper_trading_days} < "
                f"{cfg.min_paper_trading_days}")
        if payload.paper_trading_errors > cfg.max_paper_trading_errors:
            failed.append(
                f"paper_trading_errors: {payload.paper_trading_errors} > "
                f"{cfg.max_paper_trading_errors}")
        if not payload.kill_switch_integrated:
            failed.append("kill_switch_integrated is False")
        details = (
            f"Paper Days: {payload.paper_trading_days} (min {cfg.min_paper_trading_days}), "
            f"Errors: {payload.paper_trading_errors} (max {cfg.max_paper_trading_errors}), "
            f"Kill Switch: {'YES' if payload.kill_switch_integrated else 'NO'}."
        )
        return GateEvaluationResult(GATE_OPERATIONAL, not failed, details, failed)

    def _evaluate_model_risk_gate(
        self, payload: StrategyOnboardingPayload
    ) -> GateEvaluationResult:
        """
        Gate 3 -- a completed model card exists.

        Existence only. Whether the card documents parameter limits, decay
        conditions and known failure modes is a human review question; this engine
        cannot read the document.
        """
        failed: List[str] = []
        if not payload.model_card_completed:
            failed.append("model_card_completed is False")
        details = f"Model Card Completed: {'YES' if payload.model_card_completed else 'NO'}."
        return GateEvaluationResult(GATE_MODEL_RISK, not failed, details, failed)

    def _evaluate_compliance_gate(
        self, payload: StrategyOnboardingPayload
    ) -> GateEvaluationResult:
        """
        Gate 4 -- compliance sign-off is recorded.

        Recording a sign-off is not the same as obtaining the senior-management
        authorisation MiFID II RTS 6 Article 5(2) requires before deployment.
        """
        failed: List[str] = []
        if not payload.compliance_approved:
            failed.append("compliance_approved is False")
        details = f"Compliance Sign-off: {'YES' if payload.compliance_approved else 'NO'}."
        return GateEvaluationResult(GATE_COMPLIANCE, not failed, details, failed)
