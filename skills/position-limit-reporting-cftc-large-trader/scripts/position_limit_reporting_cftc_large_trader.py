import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    enabled: bool = True

@dataclass
class ComplianceRecord:
    trade_id: str
    is_compliant: bool
    notes: str

@dataclass
class TraderAccountPosition:
    account_id: str
    entity_name: str
    commodity_code: str                  # e.g. 'CL' for Crude Oil, 'ES' for E-mini S&P
    net_position: float                  # Net contract holdings (+ Long, - Short)
    long_position: float = 0.0
    short_position: float = 0.0

@dataclass
class CFTCLimitSpec:
    commodity_code: str
    reporting_threshold_contracts: float  # e.g. 350 for CL (Form 102A trigger)
    federal_speculative_limit: float      # e.g. 10000 contracts limit

@dataclass
class CFTCLargeTraderReport:
    entity_name: str
    commodity_code: str
    aggregated_net_position: float
    aggregated_gross_position: float
    reporting_threshold: float
    federal_limit: float
    is_reportable: bool                   # Form 102A filing required
    is_limit_breached: bool              # CFTC Part 150 limit violation
    status: str                           # 'FORM_102A_REPORTABLE', 'BELOW_REPORTING_LEVEL', 'SPECULATIVE_LIMIT_BREACHED'
    audit_notes: str

class ComplianceChecker:
    """
    Legacy ComplianceChecker retained for backward compatibility.
    """
    def __init__(self):
        self.rules = ["rule1", "rule2"]

    def check_compliance(self, trade_id: str) -> ComplianceRecord:
        return ComplianceRecord(trade_id=trade_id, is_compliant=True, notes="Compliant")

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        return [self.check_compliance(tid) for tid in trade_ids]

class PositionLimitReportingCFTCLargeTraderEngine:
    """
    CFTC Form 102 Large Trader Reporting (LTR) and speculative position limit compliance engine
    aggregating account holdings per entity across futures and options contracts.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def evaluate_entity_cftc_compliance(
        self,
        entity_name: str,
        commodity_positions: List[TraderAccountPosition],
        limit_spec: CFTCLimitSpec
    ) -> CFTCLargeTraderReport:
        """
        Aggregates position across accounts for entity_name and evaluates Form 102A LTR reportability & speculative limits.
        """
        if not self.config.enabled:
            return CFTCLargeTraderReport(
                entity_name=entity_name,
                commodity_code=limit_spec.commodity_code,
                aggregated_net_position=0.0,
                aggregated_gross_position=0.0,
                reporting_threshold=limit_spec.reporting_threshold_contracts,
                federal_limit=limit_spec.federal_speculative_limit,
                is_reportable=False,
                is_limit_breached=False,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        # Aggregate positions per entity
        total_net = sum(p.net_position for p in commodity_positions)
        total_long = sum(p.long_position for p in commodity_positions)
        total_short = sum(p.short_position for p in commodity_positions)
        total_gross = max(abs(total_net), total_long + total_short)

        is_reportable = total_gross >= limit_spec.reporting_threshold_contracts
        is_limit_breached = abs(total_net) > limit_spec.federal_speculative_limit

        if is_limit_breached:
            status = "SPECULATIVE_LIMIT_BREACHED"
        elif is_reportable:
            status = "FORM_102A_REPORTABLE"
        else:
            status = "BELOW_REPORTING_LEVEL"

        notes = (
            f"CFTC LTR AUDIT [{entity_name} '{limit_spec.commodity_code}' - {status}]: "
            f"Aggregated Net Pos = {total_net:,.0f} contracts, Gross = {total_gross:,.0f} contracts. "
            f"LTR Threshold = {limit_spec.reporting_threshold_contracts:,.0f} (Form 102A Required = {is_reportable}), "
            f"Federal Speculative Limit = {limit_spec.federal_speculative_limit:,.0f} (Breach = {is_limit_breached})."
        )

        if is_limit_breached:
            logger.critical(f"CFTC POSITION LIMIT VIOLATION: {notes}")
        elif is_reportable:
            logger.info(f"CFTC FORM 102A FILING TRIGGERED: {notes}")
        else:
            logger.info(notes)

        return CFTCLargeTraderReport(
            entity_name=entity_name,
            commodity_code=limit_spec.commodity_code,
            aggregated_net_position=total_net,
            aggregated_gross_position=total_gross,
            reporting_threshold=limit_spec.reporting_threshold_contracts,
            federal_limit=limit_spec.federal_speculative_limit,
            is_reportable=is_reportable,
            is_limit_breached=is_limit_breached,
            status=status,
            audit_notes=notes
        )
