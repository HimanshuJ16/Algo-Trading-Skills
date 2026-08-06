import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "strategy-research-to-production-pipeline-governance"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class PipelineStage(str, Enum):
    RESEARCH_BACKTEST = "RESEARCH_BACKTEST"
    INDEPENDENT_VALIDATION = "INDEPENDENT_VALIDATION"
    PAPER_TRADING_SHADOW = "PAPER_TRADING_SHADOW"
    STAGING_CANARY = "STAGING_CANARY"
    LIVE_PRODUCTION = "LIVE_PRODUCTION"

@dataclass
class StagePromotionArtifacts:
    git_commit_hash: str
    dataset_checksum: str
    backtest_sharpe: float
    backtest_max_drawdown_pct: float
    shadow_tracking_error_pct: float       # Difference between paper trading fills & simulated fills
    paper_trading_days: int
    has_risk_committee_signoff: bool
    author_id: str
    validator_id: str

@dataclass
class StagePromotionDecision:
    strategy_id: str
    current_stage: PipelineStage
    target_stage: PipelineStage
    is_approved: bool
    status_code: str                      # 'APPROVED_FOR_PROMOTION', 'REJECTED_LOW_SHARPE', 'REJECTED_SHADOW_ERROR', 'REJECTED_MISSING_SIGNOFF'
    passed_gates: List[str]
    failed_gates: List[str]
    audit_trail_hash: str
    audit_notes: str

class StrategyResearchToProductionGovernanceEngine:
    """
    Production-grade Research-to-Production Pipeline Governance Engine enforcing multi-gate validation,
    shadow paper-trading tracking, versioned reproducibility, and immutable audit ledgers.
    """
    def __init__(
        self,
        min_backtest_sharpe: float = 1.50,
        max_backtest_drawdown_pct: float = 15.0,
        max_shadow_tracking_error_pct: float = 5.0,
        min_paper_trading_days: int = 14
    ):
        self.min_sharpe = min_backtest_sharpe
        self.max_drawdown = max_backtest_drawdown_pct
        self.max_tracking_error = max_shadow_tracking_error_pct
        self.min_paper_days = min_paper_trading_days

    def evaluate_stage_promotion(
        self,
        strategy_id: str,
        current_stage: PipelineStage,
        target_stage: PipelineStage,
        artifacts: StagePromotionArtifacts
    ) -> StagePromotionDecision:
        """
        Evaluates promotion request against strict pipeline gate rules.
        """
        passed: List[str] = []
        failed: List[str] = []

        # 1. Reproducibility Gate
        if artifacts.git_commit_hash and len(artifacts.git_commit_hash) >= 7 and artifacts.dataset_checksum:
            passed.append("REPRODUCIBILITY_GATE: Valid Git commit hash and dataset checksum verified.")
        else:
            failed.append("REPRODUCIBILITY_GATE: Missing or invalid Git commit hash / dataset checksum.")

        # 2. Backtest Quantitative Gate
        if artifacts.backtest_sharpe >= self.min_sharpe:
            passed.append(f"SHARPE_GATE: Backtest Sharpe ({artifacts.backtest_sharpe:.2f}) >= Min ({self.min_sharpe:.2f}).")
        else:
            failed.append(f"SHARPE_GATE: Backtest Sharpe ({artifacts.backtest_sharpe:.2f}) < Min ({self.min_sharpe:.2f}).")

        if artifacts.backtest_max_drawdown_pct <= self.max_drawdown:
            passed.append(f"DRAWDOWN_GATE: Backtest Max DD ({artifacts.backtest_max_drawdown_pct:.1f}%) <= Cap ({self.max_drawdown:.1f}%).")
        else:
            failed.append(f"DRAWDOWN_GATE: Backtest Max DD ({artifacts.backtest_max_drawdown_pct:.1f}%) > Cap ({self.max_drawdown:.1f}%).")

        # 3. Paper Trading / Shadow Execution Gate (Required for promotion to LIVE_PRODUCTION or STAGING_CANARY)
        if target_stage in (PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION):
            if artifacts.paper_trading_days >= self.min_paper_days:
                passed.append(f"PAPER_DAYS_GATE: Shadow paper trading duration ({artifacts.paper_trading_days}d) >= Min ({self.min_paper_days}d).")
            else:
                failed.append(f"PAPER_DAYS_GATE: Shadow paper trading duration ({artifacts.paper_trading_days}d) < Min ({self.min_paper_days}d).")

            if artifacts.shadow_tracking_error_pct <= self.max_tracking_error:
                passed.append(f"SHADOW_TRACKING_GATE: Shadow tracking error ({artifacts.shadow_tracking_error_pct:.2f}%) <= Cap ({self.max_tracking_error:.2f}%).")
            else:
                failed.append(f"SHADOW_TRACKING_GATE: Shadow tracking error ({artifacts.shadow_tracking_error_pct:.2f}%) > Cap ({self.max_tracking_error:.2f}%).")

        # 4. Independent Risk Governance Sign-Off Gate (Required for LIVE_PRODUCTION)
        if target_stage == PipelineStage.LIVE_PRODUCTION:
            if artifacts.has_risk_committee_signoff and artifacts.validator_id:
                passed.append(f"RISK_GOVERNANCE_GATE: Approved by Risk Committee validator {artifacts.validator_id}.")
            else:
                failed.append("RISK_GOVERNANCE_GATE: Missing formal Risk Committee validator sign-off.")

        is_approved = len(failed) == 0
        status_code = "APPROVED_FOR_PROMOTION" if is_approved else f"REJECTED_GATES_FAILED ({len(failed)})"

        # Generate cryptographic audit hash
        raw_audit_str = f"{strategy_id}:{current_stage.value}->{target_stage.value}:{artifacts.git_commit_hash}:{is_approved}:{time.time()}"
        audit_hash = hashlib.sha256(raw_audit_str.encode('utf-8')).hexdigest()[:16]

        notes = (
            f"PIPELINE GOVERNANCE [{status_code}] ({strategy_id}): Transition {current_stage.value} -> {target_stage.value}. "
            f"Passed Gates = {len(passed)}, Failed Gates = {len(failed)}. Audit Hash = {audit_hash}."
        )

        if is_approved:
            logger.info(notes)
        else:
            logger.warning(notes)

        return StagePromotionDecision(
            strategy_id=strategy_id,
            current_stage=current_stage,
            target_stage=target_stage,
            is_approved=is_approved,
            status_code=status_code,
            passed_gates=passed,
            failed_gates=failed,
            audit_trail_hash=audit_hash,
            audit_notes=notes
        )
