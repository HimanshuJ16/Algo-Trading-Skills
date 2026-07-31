import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class InputData:
    data: str
    value: float

@dataclass
class BreachIncidentSpec:
    incident_id: str
    incident_date: str                   # e.g. '2026-07-31'
    strategy_id: str
    breach_type: str                     # 'POSITION_LIMIT_EXCEEDED', 'DRAWDOWN_KILL_SWITCH', 'RUNAWAY_ALGO'
    severity: str                        # 'CRITICAL', 'MAJOR', 'MINOR'
    financial_loss_usd: float
    five_whys: List[str]                 # 5-Whys drill down
    timeline_events: List[Tuple[str, str]] # List of (timestamp_str, event_description)
    action_items: List[str]              # CAPA tasks

@dataclass
class RCAReport:
    incident_id: str
    strategy_id: str
    severity: str
    financial_loss_usd: float
    five_whys_depth: int
    is_valid_rca: bool
    markdown_document: str
    status: str                          # 'RCA_GENERATED_SUCCESS', 'INSUFFICIENT_5_WHYS_DEPTH', 'MISSING_ACTION_ITEMS'
    audit_notes: str

class BreachRcaGenerator:
    """
    Standardized post-incident Root Cause Analysis (RCA) generator incorporating 5-Whys analysis,
    chronological event timelines, financial impact audits, and CAPA action items.
    """
    def __init__(self):
        self.history: List[InputData] = []

    def process(self, data: InputData) -> bool:
        """
        Legacy process method for backward compatibility.
        """
        self.history.append(data)
        if data.value < 0:
            return False
        return True

    def generate_rca_report(self, spec: BreachIncidentSpec) -> RCAReport:
        """
        Validates incident data and renders standardized Markdown RCA document and JSON report payload.
        """
        five_whys_depth = len(spec.five_whys)
        if five_whys_depth < 3:
            notes = f"INSUFFICIENT 5-WHYS DEPTH: RCA requires at least 3 levels of root cause analysis (provided {five_whys_depth})."
            logger.warning(notes)
            return RCAReport(
                incident_id=spec.incident_id,
                strategy_id=spec.strategy_id,
                severity=spec.severity,
                financial_loss_usd=spec.financial_loss_usd,
                five_whys_depth=five_whys_depth,
                is_valid_rca=False,
                markdown_document="",
                status="INSUFFICIENT_5_WHYS_DEPTH",
                audit_notes=notes
            )

        if not spec.action_items:
            notes = "MISSING ACTION ITEMS: RCA requires at least one CAPA action item."
            logger.warning(notes)
            return RCAReport(
                incident_id=spec.incident_id,
                strategy_id=spec.strategy_id,
                severity=spec.severity,
                financial_loss_usd=spec.financial_loss_usd,
                five_whys_depth=five_whys_depth,
                is_valid_rca=False,
                markdown_document="",
                status="MISSING_ACTION_ITEMS",
                audit_notes=notes
            )

        # Build Markdown Document
        md_lines = [
            f"# ROOT CAUSE ANALYSIS (RCA) REPORT: {spec.incident_id}",
            f"**Date**: {spec.incident_date} | **Strategy**: `{spec.strategy_id}` | **Severity**: `{spec.severity}`",
            f"**Breach Type**: `{spec.breach_type}` | **Financial Loss**: `${spec.financial_loss_usd:,.2f}`",
            "",
            "## 1. Executive Summary",
            f"Incident `{spec.incident_id}` occurred on `{spec.incident_date}` due to a `{spec.breach_type}` breach.",
            f"Total P&L impact: `${spec.financial_loss_usd:,.2f}`.",
            "",
            "## 2. Chronological Timeline",
        ]
        for ts, desc in spec.timeline_events:
            md_lines.append(f"- **{ts}**: {desc}")

        md_lines.extend(["", "## 3. 5-Whys Analysis"])
        for idx, why in enumerate(spec.five_whys, 1):
            md_lines.append(f"{idx}. **Why?**: {why}")

        md_lines.extend(["", "## 4. Corrective and Preventive Actions (CAPA)"])
        for item in spec.action_items:
            md_lines.append(f"- [ ] {item}")

        markdown_doc = "\n".join(md_lines)
        notes = f"RCA GENERATED SUCCESSFUL [{spec.incident_id}]: 5-Whys Depth = {five_whys_depth}, Action Items = {len(spec.action_items)}."
        logger.info(notes)

        return RCAReport(
            incident_id=spec.incident_id,
            strategy_id=spec.strategy_id,
            severity=spec.severity,
            financial_loss_usd=spec.financial_loss_usd,
            five_whys_depth=five_whys_depth,
            is_valid_rca=True,
            markdown_document=markdown_doc,
            status="RCA_GENERATED_SUCCESS",
            audit_notes=notes
        )
