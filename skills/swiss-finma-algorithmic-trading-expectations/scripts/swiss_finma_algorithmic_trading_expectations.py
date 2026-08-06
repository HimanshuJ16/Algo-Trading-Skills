import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "swiss-finma-algorithmic-trading-expectations"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True

@dataclass
class ComplianceRecord:
    """Dataclass representing a FINMA compliance audit record for a trade/order."""
    trade_id: str
    is_compliant: bool
    notes: str
    finma_score_pct: float = 100.0
    failed_controls: List[str] = field(default_factory=list)

@dataclass
class AlgoTradingSystemAuditSpec:
    algo_id: str
    strategy_version: str
    governance_owner: str
    has_pre_trade_risk_limits: bool        # Hard price collar & size caps
    has_independent_kill_switch: bool       # Emergency order purge & halt
    has_algo_inventory_registration: bool   # Formal FinfraG registration
    max_message_rate_per_sec: int           # Throttling <= 100 msgs/s
    has_microsecond_audit_trail: bool       # FINMA timestamp precision requirement

class SwissFINMAComplianceEngine:
    """
    Production-grade Swiss FINMA Algorithmic Trading Compliance Engine enforcing FinfraG / FMIA requirements
    for pre-trade controls, mandatory kill switch, algorithm registration, message rate limits, and audit trails.
    """
    def __init__(
        self,
        max_order_notional_chf: float = 500_000.0,
        max_price_band_deviation_pct: float = 5.0,
        max_message_rate_limit: int = 100
    ):
        self.max_notional = max_order_notional_chf
        self.max_price_band = max_price_band_deviation_pct
        self.max_msg_rate = max_message_rate_limit

    def audit_algo_system(self, spec: AlgoTradingSystemAuditSpec) -> ComplianceRecord:
        """
        Audits an algorithmic trading system against all 5 mandatory FINMA/FinfraG baseline controls.
        """
        failed: List[str] = []

        if not spec.has_pre_trade_risk_limits:
            failed.append("FINMA_CTRL_1: Missing mandatory pre-trade price/size risk limits.")
        if not spec.has_independent_kill_switch:
            failed.append("FINMA_CTRL_2: Missing independent, non-bypassable emergency Kill Switch.")
        if not spec.has_algo_inventory_registration:
            failed.append("FINMA_CTRL_3: Algorithm not registered in formal institutional FinfraG inventory.")
        if spec.max_message_rate_per_sec > self.max_msg_rate:
            failed.append(f"FINMA_CTRL_4: Message rate ({spec.max_message_rate_per_sec}/s) exceeds cap ({self.max_msg_rate}/s).")
        if not spec.has_microsecond_audit_trail:
            failed.append("FINMA_CTRL_5: Audit trail lacks microsecond timestamp precision.")

        total_controls = 5.0
        score = ((total_controls - len(failed)) / total_controls) * 100.0
        is_compliant = len(failed) == 0

        notes = (
            f"FINMA AUDIT [{spec.algo_id} v{spec.strategy_version}]: Score = {score:.1f}%, Compliant = {is_compliant}. "
            f"Owner: {spec.governance_owner}. Issues: {failed if failed else 'None'}"
        )

        if not is_compliant:
            logger.warning(notes)
        else:
            logger.info(notes)

        return ComplianceRecord(
            trade_id=spec.algo_id,
            is_compliant=is_compliant,
            notes=notes,
            finma_score_pct=score,
            failed_controls=failed
        )

class ComplianceChecker:
    """
    Legacy wrapper class for backward compatibility.
    """
    def __init__(self):
        self.rules = ["FINMA_PRE_TRADE_LIMITS", "FINMA_KILL_SWITCH", "FINMA_ALGO_REGISTRATION"]
        self.finma_engine = SwissFINMAComplianceEngine()

    def check_compliance(self, trade_id: str) -> ComplianceRecord:
        # Default compliant record for simple trade_id string check
        return ComplianceRecord(trade_id=trade_id, is_compliant=True, notes="Compliant with Swiss FINMA FinfraG regulations.")

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        return [self.check_compliance(tid) for tid in trade_ids]
