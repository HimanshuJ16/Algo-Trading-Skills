import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str = "default"
    strict_blame_check: bool = True

BLAME_KEYWORDS = [
    "forgot", "careless", "human error", "blame", "fault",
    "stupid", "lazy", "negligent", "trader error", "developer error"
]

@dataclass
class BlamelessPostmortemInput:
    incident_id: str
    incident_date: str
    summary: str
    systemic_factors: List[str]          # Process/Tooling/Architecture gaps
    narrative: str
    proposed_actions: List[str]

@dataclass
class BlamelessPostmortemReport:
    incident_id: str
    blame_detected: bool
    detected_blame_terms: List[str]
    is_approved: bool
    markdown_document: str
    status: str                          # 'BLAMELESS_POSTMORTEM_APPROVED', 'BLAME_LANGUAGE_DETECTED'
    audit_notes: str

class BlamelessPostmortemGenerator:
    """
    Site Reliability Engineering (SRE) blameless post-mortem review engine enforcing psychological safety,
    systemic root cause reframing, and language scanning to eliminate personal blame.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config("default")

    def process() -> bool:
        """
        Legacy process method for backward compatibility.
        """
        return True

    def generate_blameless_postmortem(
        self, inp: BlamelessPostmortemInput
    ) -> BlamelessPostmortemReport:
        """
        Scans narrative for blame language and generates SRE-compliant blameless post-mortem document.
        """
        detected_terms: List[str] = []
        narrative_lower = inp.narrative.lower()

        for kw in BLAME_KEYWORDS:
            if re.search(r'\b' + re.escape(kw) + r'\b', narrative_lower):
                detected_terms.append(kw)

        blame_detected = len(detected_terms) > 0 and self.config.strict_blame_check

        if blame_detected:
            notes = (
                f"BLAME LANGUAGE DETECTED in incident narrative [{inp.incident_id}]: "
                f"Offending terms = {detected_terms}. Reframing required! "
                f"Replace personal fault phrases with systemic factor explanations."
            )
            logger.warning(notes)
            return BlamelessPostmortemReport(
                incident_id=inp.incident_id,
                blame_detected=True,
                detected_blame_terms=detected_terms,
                is_approved=False,
                markdown_document="",
                status="BLAME_LANGUAGE_DETECTED",
                audit_notes=notes
            )

        # Generate Blameless Markdown Document
        md_lines = [
            f"# BLAMELESS POST-MORTEM REPORT: {inp.incident_id}",
            f"**Date**: {inp.incident_date} | **Governance**: `Google SRE Blameless Standard`",
            "",
            "## 1. Executive Summary",
            inp.summary,
            "",
            "## 2. Systemic & Architectural Factors",
        ]
        for factor in inp.systemic_factors:
            md_lines.append(f"- {factor}")

        md_lines.extend(["", "## 3. Incident Narrative (Blameless)", inp.narrative])

        md_lines.extend(["", "## 4. Corrective & Preventative Actions (CAPA)"])
        for act in inp.proposed_actions:
            md_lines.append(f"- [ ] {act}")

        markdown_doc = "\n".join(md_lines)
        notes = f"BLAMELESS POST-MORTEM APPROVED [{inp.incident_id}]: Systemic Factors = {len(inp.systemic_factors)}, Actions = {len(inp.proposed_actions)}."
        logger.info(notes)

        return BlamelessPostmortemReport(
            incident_id=inp.incident_id,
            blame_detected=False,
            detected_blame_terms=[],
            is_approved=True,
            markdown_document=markdown_doc,
            status="BLAMELESS_POSTMORTEM_APPROVED",
            audit_notes=notes
        )
