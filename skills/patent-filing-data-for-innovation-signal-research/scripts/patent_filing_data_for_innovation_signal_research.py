import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

@dataclass
class SignalResult:
    timestamp: str
    signal_value: float
    asset_id: str

@dataclass
class PatentFilingRecord:
    asset_id: str
    filing_date: str
    grant_date: str
    patent_id: str
    forward_citations: int = 0
    claim_count: int = 10
    technology_class: str = "TECH"

@dataclass
class PatentSignalConfig:
    velocity_weight: float = 0.50
    citation_weight: float = 0.50
    min_forward_citations: int = 0

@dataclass
class PatentInnovationReport:
    total_assets: int
    asset_scores: Dict[str, float]
    z_scores: Dict[str, float]
    top_innovator: str
    status: str                          # 'SIGNALS_GENERATED', 'EMPTY_UNIVERSE'
    audit_notes: str

class PatentFilingDataForInnovationSignalResearchEngine:
    """
    Patent filing and innovation signal research engine parsing USPTO/EPO patent velocity,
    forward citations, and claim counts to compute Innovation Quality Scores (IQS) for equity alpha factors.
    """
    def __init__(self, config: Optional[PatentSignalConfig] = None):
        self.config = config or PatentSignalConfig()

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        """
        Legacy signal generation method for backward compatibility.
        """
        if raw_data is None or raw_data.empty:
            return []

        results = []
        for i, row in raw_data.iterrows():
            raw_val = float(row.get("raw_val", 0.0))
            asset = str(row.get("asset", "unknown"))
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=round(raw_val * 1.5, 4),
                asset_id=asset
            ))
        return results

    def compute_patent_innovation_signals(
        self, records: List[PatentFilingRecord]
    ) -> PatentInnovationReport:
        """
        Computes Innovation Quality Scores (IQS) and Z-score normalized alpha factors across corporate assets.
        """
        if not records:
            return PatentInnovationReport(
                total_assets=0,
                asset_scores={},
                z_scores={},
                top_innovator="NONE",
                status="EMPTY_UNIVERSE",
                audit_notes="No patent records provided."
            )

        asset_patents: Dict[str, List[PatentFilingRecord]] = {}
        for r in records:
            asset = r.asset_id.upper()
            if asset not in asset_patents:
                asset_patents[asset] = []
            asset_patents[asset].append(r)

        raw_scores: Dict[str, float] = {}

        for asset, p_list in asset_patents.items():
            velocity = float(len(p_list))
            # Logarithmic forward citation impact
            citation_impact = sum(math.log(1.0 + max(0, p.forward_citations)) for p in p_list)

            iqs = (self.config.velocity_weight * velocity) + (self.config.citation_weight * citation_impact)
            raw_scores[asset] = round(iqs, 4)

        # Cross-Sectional Z-Score Normalization
        scores_list = list(raw_scores.values())
        mean_score = sum(scores_list) / float(len(scores_list))
        var_score = sum((s - mean_score) ** 2 for s in scores_list) / float(len(scores_list))
        std_score = math.sqrt(var_score) if var_score > 0 else 1.0

        z_scores: Dict[str, float] = {}
        for asset, s in raw_scores.items():
            z = (s - mean_score) / std_score if std_score > 0 else 0.0
            # Winsorize Z-scores to [-3.0, +3.0]
            z_scores[asset] = round(max(-3.0, min(3.0, z)), 4)

        top_asset = max(raw_scores, key=raw_scores.get)
        notes = (
            f"PATENT INNOVATION SIGNALS GENERATED: Assets = {len(raw_scores)}, Top Innovator = '{top_asset}' "
            f"(IQS Score = {raw_scores[top_asset]}, Z-Score = {z_scores[top_asset]:+.2f})."
        )
        logger.info(notes)

        return PatentInnovationReport(
            total_assets=len(raw_scores),
            asset_scores=raw_scores,
            z_scores=z_scores,
            top_innovator=top_asset,
            status="SIGNALS_GENERATED",
            audit_notes=notes
        )
