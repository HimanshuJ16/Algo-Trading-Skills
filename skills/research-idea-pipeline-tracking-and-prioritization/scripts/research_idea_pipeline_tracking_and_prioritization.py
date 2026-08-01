import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

@dataclass
class ResearchIdeaPipelineTrackingAndPrioritizationConfig:
    """Legacy config class for backward compatibility."""
    data_path: str = "default.csv"
    min_priority_score: float = 1.0

@dataclass
class ResearchIdea:
    idea_id: str
    title: str
    author: str
    expected_sharpe: float               # e.g. 1.8
    estimated_capacity_usd: float        # e.g. 10,000,000.0
    implementation_complexity: int       # 1 (low) to 5 (high)
    data_cost_tier: int                  # 1 (free/existing) to 5 (expensive alt data)
    stage: str = "PROPOSED"              # 'PROPOSED', 'BACKTESTING', 'PAPER_TRADING', 'PRODUCTION_READY', 'REJECTED'

@dataclass
class PrioritizedIdea:
    idea_id: str
    title: str
    stage: str
    priority_score: float
    rank: int

@dataclass
class ResearchPipelineReport:
    total_ideas: int
    active_ideas: int
    top_priority_ideas: List[PrioritizedIdea]
    stage_breakdown: Dict[str, int]
    status: str                          # 'PIPELINE_ACTIVE', 'NO_IDEAS'
    audit_notes: str

class ResearchIdeaPipelineTrackingAndPrioritization:
    """Legacy class retained for backward compatibility."""
    def __init__(self, config: ResearchIdeaPipelineTrackingAndPrioritizationConfig):
        self.config = config

    def process(self) -> bool:
        return True

class ResearchIdeaPipelineTrackingAndPrioritizationEngine:
    """
    Quantitative research idea pipeline tracking and prioritization engine scoring alpha hypotheses
    by expected Sharpe ratio, capacity scaling, implementation complexity, and data acquisition cost.
    """
    def __init__(self, config: Optional[ResearchIdeaPipelineTrackingAndPrioritizationConfig] = None):
        self.config = config or ResearchIdeaPipelineTrackingAndPrioritizationConfig()
        self.ideas: Dict[str, ResearchIdea] = {}

    def add_idea(self, idea: ResearchIdea) -> None:
        """Registers a new quantitative research idea in the pipeline."""
        self.ideas[idea.idea_id] = idea

    def update_stage(self, idea_id: str, new_stage: str) -> bool:
        """Updates the lifecycle stage of an existing research idea."""
        if idea_id in self.ideas:
            self.ideas[idea_id].stage = new_stage.upper()
            return True
        return False

    def calculate_priority_score(self, idea: ResearchIdea) -> float:
        """
        Calculates priority score: (Sharpe * log10(Capacity)) / (Complexity * DataCost)
        """
        cap_factor = max(math.log10(max(idea.estimated_capacity_usd, 1.0)), 1.0)
        complexity = max(idea.implementation_complexity, 1)
        cost = max(idea.data_cost_tier, 1)

        raw_score = (idea.expected_sharpe * cap_factor) / (complexity * cost)
        return round(raw_score, 4)

    def generate_pipeline_report(self) -> ResearchPipelineReport:
        """
        Scores all active ideas, ranks them by priority score, and generates pipeline breakdown.
        """
        stage_counts: Dict[str, int] = {}
        scored_list: List[PrioritizedIdea] = []

        for idea_id, idea in self.ideas.items():
            st = idea.stage.upper()
            stage_counts[st] = stage_counts.get(st, 0) + 1

            if st != "REJECTED":
                score = self.calculate_priority_score(idea)
                scored_list.append(PrioritizedIdea(
                    idea_id=idea.idea_id,
                    title=idea.title,
                    stage=st,
                    priority_score=score,
                    rank=0
                ))

        # Sort descending by priority score
        scored_list.sort(key=lambda x: x.priority_score, reverse=True)
        for rank_idx, p_idea in enumerate(scored_list, 1):
            p_idea.rank = rank_idx

        total = len(self.ideas)
        active = len(scored_list)
        status = "PIPELINE_ACTIVE" if total > 0 else "NO_IDEAS"

        notes = (
            f"RESEARCH PIPELINE [{status}]: Total Ideas = {total}, Active = {active}, "
            f"Top Idea = '{scored_list[0].title}' (Score: {scored_list[0].priority_score})" if active > 0 else f"RESEARCH PIPELINE [{status}]: No active ideas."
        )

        logger.info(notes)

        return ResearchPipelineReport(
            total_ideas=total,
            active_ideas=active,
            top_priority_ideas=scored_list,
            stage_breakdown=stage_counts,
            status=status,
            audit_notes=notes
        )
