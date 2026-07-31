import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class CompanyJobPostingSnapshot:
    ticker: str                         # e.g. 'NVDA'
    company_name: str                   # e.g. 'NVIDIA Corporation'
    current_active_postings_count: int  # Current active requisitions (e.g. 300)
    previous_active_postings_count: int # Previous quarter active requisitions (e.g. 150)
    engineering_postings_pct: float     # % of postings in R&D / Engineering / AI (0.0 to 1.0)
    sales_postings_pct: float           # % of postings in Sales / BD (0.0 to 1.0)
    avg_posting_duration_days: float    # Average duration postings have been open (e.g. 35.0 days)

@dataclass
class JobPostingSignalReport:
    ticker: str
    company_name: str
    qoq_postings_growth_pct: float
    role_weighting_factor: float
    has_ghost_postings_penalty: bool
    corporate_growth_score: float       # Normalized -1.0 to +1.0
    signal_classification: str          # 'EXPANSION_BULLISH', 'CONTRACTION_BEARISH', 'STABLE_NEUTRAL'
    audit_notes: str

class JobPostingSignalEngine:
    """
    Quantitative alternative data signal engine analyzing web-scraped corporate job postings,
    measuring QoQ hiring velocity, engineering/R&D role mix, ghost job penalties, and corporate expansion scores.
    """
    def __init__(self, ghost_job_stale_days_threshold: float = 120.0):
        self.ghost_job_stale_days_threshold = ghost_job_stale_days_threshold

    def calculate_growth_score(self, snapshot: CompanyJobPostingSnapshot) -> JobPostingSignalReport:
        """
        Calculates role-weighted QoQ job postings growth rate and applies ghost job stale listing penalties.
        """
        curr = snapshot.current_active_postings_count
        prev = max(1, snapshot.previous_active_postings_count)

        # 1. QoQ Growth Rate
        growth_pct = round(((curr - prev) / float(prev)) * 100.0, 2)

        # 2. Role Weighting Factor (Higher R&D/Sales -> Higher expansion score)
        eng_pct = min(1.0, max(0.0, snapshot.engineering_postings_pct))
        sales_pct = min(1.0, max(0.0, snapshot.sales_postings_pct))
        role_factor = round(1.0 + (eng_pct * 0.5) + (sales_pct * 0.3), 3)

        # 3. Ghost Job Stale Listing Penalty (Avg duration > 120 days)
        has_ghost_penalty = (snapshot.avg_posting_duration_days > self.ghost_job_stale_days_threshold)
        stale_haircut = 0.5 if has_ghost_penalty else 1.0

        # 4. Normalized Growth Score Calculation (-1.0 to +1.0)
        raw_score = (growth_pct / 100.0) * role_factor * stale_haircut
        norm_score = round(min(1.0, max(-1.0, raw_score)), 4)

        # Signal Classification
        if norm_score >= 0.25:
            sig = "EXPANSION_BULLISH"
        elif norm_score <= -0.25:
            sig = "CONTRACTION_BEARISH"
        else:
            sig = "STABLE_NEUTRAL"

        ghost_str = " (50% Ghost Penalty Applied)" if has_ghost_penalty else ""
        notes = (
            f"JOB POSTING SIGNAL [{snapshot.ticker} - {snapshot.company_name}]: "
            f"QoQ Growth = {growth_pct:+.1f}% ({curr:,} vs {prev:,} active listings). "
            f"Role Factor = {role_factor:.2f} (Eng: {eng_pct*100:.0f}%, Sales: {sales_pct*100:.0f}%). "
            f"Growth Score = {norm_score:+.2f}{ghost_str} -> {sig}."
        )
        logger.info(notes)

        return JobPostingSignalReport(
            ticker=snapshot.ticker,
            company_name=snapshot.company_name,
            qoq_postings_growth_pct=growth_pct,
            role_weighting_factor=role_factor,
            has_ghost_postings_penalty=has_ghost_penalty,
            corporate_growth_score=norm_score,
            signal_classification=sig,
            audit_notes=notes
        )
