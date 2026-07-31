import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class StrategyOnboardingPayload:
    strategy_id: str
    strategy_name: str
    author: str
    walk_forward_score: float
    regimes_covered: int
    backtest_sharpe: float
    paper_trading_days: int
    paper_trading_errors: int
    kill_switch_integrated: bool
    model_card_completed: bool
    compliance_approved: bool

@dataclass
class OnboardingPolicyConfig:
    min_walk_forward_score: float = 0.70
    min_regimes_covered: int = 3
    min_backtest_sharpe: float = 1.5
    min_paper_trading_days: int = 14
    max_paper_trading_errors: int = 0

@dataclass
class GateEvaluationResult:
    gate_name: str                       # 'BACKTEST_GATE', 'OPERATIONAL_GATE', 'MODEL_RISK_GATE', 'COMPLIANCE_GATE'
    passed: bool
    details: str

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

class NewStrategyOnboardingEngine:
    """
    New strategy onboarding gatekeeper engine evaluating 4-gate governance standards
    (Backtest Robustness, Operational Runtime, Model Risk, Compliance Approval).
    """
    def __init__(self, config: Optional[OnboardingPolicyConfig] = None):
        self.config = config or OnboardingPolicyConfig()

    def audit_strategy_onboarding(
        self, payload: StrategyOnboardingPayload
    ) -> OnboardingAuditReport:
        """
        Evaluates 4 governance gates: Backtest, Operational, Model Risk, and Compliance.
        """
        gates: List[GateEvaluationResult] = []

        # Gate 1: Backtest & Robustness Gate
        g1_ok = (
            payload.walk_forward_score >= self.config.min_walk_forward_score and
            payload.regimes_covered >= self.config.min_regimes_covered and
            payload.backtest_sharpe >= self.config.min_backtest_sharpe
        )
        g1_details = (
            f"WF Score: {payload.walk_forward_score:.2f} (min {self.config.min_walk_forward_score:.2f}), "
            f"Regimes: {payload.regimes_covered} (min {self.config.min_regimes_covered}), "
            f"Sharpe: {payload.backtest_sharpe:.2f} (min {self.config.min_backtest_sharpe:.2f})."
        )
        gates.append(GateEvaluationResult("BACKTEST_GATE", g1_ok, g1_details))

        # Gate 2: Operational & Paper Trading Gate
        g2_ok = (
            payload.paper_trading_days >= self.config.min_paper_trading_days and
            payload.paper_trading_errors <= self.config.max_paper_trading_errors and
            payload.kill_switch_integrated
        )
        g2_details = (
            f"Paper Days: {payload.paper_trading_days} (min {self.config.min_paper_trading_days}), "
            f"Errors: {payload.paper_trading_errors} (max {self.config.max_paper_trading_errors}), "
            f"Kill Switch: {'YES' if payload.kill_switch_integrated else 'NO'}."
        )
        gates.append(GateEvaluationResult("OPERATIONAL_GATE", g2_ok, g2_details))

        # Gate 3: Model Risk Documentation Gate
        g3_ok = payload.model_card_completed
        g3_details = f"Model Card Completed: {'YES' if payload.model_card_completed else 'NO'}."
        gates.append(GateEvaluationResult("MODEL_RISK_GATE", g3_ok, g3_details))

        # Gate 4: Compliance & Legal Approval Gate
        g4_ok = payload.compliance_approved
        g4_details = f"Compliance Sign-off: {'YES' if payload.compliance_approved else 'NO'}."
        gates.append(GateEvaluationResult("COMPLIANCE_GATE", g4_ok, g4_details))

        passed_count = sum(1 for g in gates if g.passed)
        total_count = len(gates)

        is_approved = passed_count == total_count
        status = "ONBOARDING_PASSED" if is_approved else "ONBOARDING_REJECTED"

        notes = (
            f"ONBOARDING AUDIT [{payload.strategy_id} - {status}]: Passed {passed_count}/{total_count} Governance Gates."
        )
        if not is_approved:
            failed_gates = [g.gate_name for g in gates if not g.passed]
            notes += f" Failed Gates: {failed_gates}."
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
            audit_notes=notes
        )
