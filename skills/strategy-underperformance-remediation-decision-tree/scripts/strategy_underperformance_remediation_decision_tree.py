import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "strategy-underperformance-remediation-decision-tree"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class RemediationAction(str, Enum):
    MAINTAIN_TRADING = "MAINTAIN_TRADING"
    RECALIBRATE_MODEL_PARAMETERS = "RECALIBRATE_MODEL_PARAMETERS"
    OPTIMIZE_EXECUTION_AND_DATA = "OPTIMIZE_EXECUTION_AND_DATA"
    TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL = "TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL"
    MANDATORY_STRATEGY_DECOMMISSION = "MANDATORY_STRATEGY_DECOMMISSION"

@dataclass
class UnderperformanceTriageMetrics:
    strategy_id: str
    live_sharpe: float
    backtest_sharpe: float
    peer_benchmark_sharpe: float
    realized_slippage_bps: float
    expected_alpha_bps: float
    is_data_feed_healthy: bool
    is_alpha_hypothesis_valid: bool       # Does the underlying economic inefficiency still exist?

@dataclass
class StrategyRemediationReport:
    strategy_id: str
    recommended_action: RemediationAction
    triage_path: List[str]
    is_capital_reduced: bool
    is_decommissioned: bool
    remediation_instructions: str
    audit_notes: str

class StrategyUnderperformanceRemediationEngine:
    """
    Production-grade Strategy Underperformance Remediation Engine evaluating quantitative triage decision trees
    to separate execution failure, market regime shifts, parameter drift, and structural alpha decay.
    """
    def __init__(
        self,
        min_healthy_sharpe: float = 1.0,
        max_slippage_alpha_ratio: float = 0.50, # Slippage eating > 50% of alpha
        min_peer_sharpe: float = 0.50
    ):
        self.min_sharpe = min_healthy_sharpe
        self.max_slippage_ratio = max_slippage_alpha_ratio
        self.min_peer_sharpe = min_peer_sharpe

    def evaluate_remediation(self, metrics: UnderperformanceTriageMetrics) -> StrategyRemediationReport:
        """
        Executes 4-node Quantitative Triage Decision Tree:
        Node 1: Hypothesis Validity & Alpha Decay Check.
        Node 2: Execution & Data Quality Audit (Slippage vs Alpha).
        Node 3: Market-Wide Regime Shift Audit (Peer Sharpe).
        Node 4: Parameter Recalibration vs Healthy Maintenance.
        """
        path: List[str] = []

        # Node 1: Fundamental Hypothesis Failure
        if not metrics.is_alpha_hypothesis_valid:
            path.append("NODE_1_HYPOTHESIS_FAILURE: Economic edge no longer exists.")
            action = RemediationAction.MANDATORY_STRATEGY_DECOMMISSION
            instructions = "DECOMMISSION_IMMEDIATELY: Cancel open orders, liquidate positions, and retire strategy codebase."
            cap_reduced = True
            decommissioned = True

        # Node 2: Data & Execution Dysfunction
        elif not metrics.is_data_feed_healthy or (metrics.expected_alpha_bps > 0 and (metrics.realized_slippage_bps / metrics.expected_alpha_bps) > self.max_slippage_ratio):
            path.append("NODE_2_EXECUTION_DYSFUNCTION: Excessive TCA slippage or corrupt/stale data feeds.")
            action = RemediationAction.OPTIMIZE_EXECUTION_AND_DATA
            instructions = "PAUSE_OR_REPAIR_EXECUTION: Fix data pipeline SLAs, tune execution algorithm order slicing, or switch brokers."
            cap_reduced = True
            decommissioned = False

        # Node 3: Market-Wide Regime Shift (Peer Underperformance)
        elif metrics.live_sharpe < self.min_sharpe and metrics.peer_benchmark_sharpe < self.min_peer_sharpe:
            path.append("NODE_3_REGIME_SHIFT: Peer benchmark strategies also suffering from market-wide regime shift.")
            action = RemediationAction.TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL
            instructions = "REDUCE_ALLOCATION_50%: Reduce strategy allocation by 50% while retaining signal execution until regime shifts."
            cap_reduced = True
            decommissioned = False

        # Node 4: Parameter Drift (Idiosyncratic underperformance while peers healthy)
        elif metrics.live_sharpe < self.min_sharpe and metrics.peer_benchmark_sharpe >= self.min_peer_sharpe:
            path.append("NODE_4_PARAMETER_DRIFT: Strategy underperforming relative to healthy peers; signal parameters stale.")
            action = RemediationAction.RECALIBRATE_MODEL_PARAMETERS
            instructions = "RECALIBRATE_PARAMETERS: Run walk-forward parameter re-optimization and out-of-sample validation."
            cap_reduced = False
            decommissioned = False

        # Healthy Performance
        else:
            path.append("HEALTHY_NODE: Strategy operating within healthy quantitative thresholds.")
            action = RemediationAction.MAINTAIN_TRADING
            instructions = "MAINTAIN_TRADING: No remediation required."
            cap_reduced = False
            decommissioned = False

        notes = f"REMEDIATION REPORT [{action.value}] ({metrics.strategy_id}): Path = {' -> '.join(path)}. Action = {instructions}"

        if decommissioned:
            logger.error(notes)
        elif cap_reduced:
            logger.warning(notes)
        else:
            logger.info(notes)

        return StrategyRemediationReport(
            strategy_id=metrics.strategy_id,
            recommended_action=action,
            triage_path=path,
            is_capital_reduced=cap_reduced,
            is_decommissioned=decommissioned,
            remediation_instructions=instructions,
            audit_notes=notes
        )
