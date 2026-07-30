import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CandidateFactorTest:
    factor_id: str
    factor_name: str
    raw_t_stat: float
    raw_p_value: float
    sample_size: int
    is_raw_significant: bool = False
    is_bonferroni_significant: bool = False
    is_holm_significant: bool = False
    is_bh_fdr_significant: bool = False
    is_hlz_t3_significant: bool = False
    adjusted_p_value_bh: float = 1.0

@dataclass
class FactorMultipleTestingAuditReport:
    total_factors_tested: int
    alpha_target: float
    fdr_q_target: float
    raw_significant_count: int
    bonferroni_significant_count: int
    holm_significant_count: int
    bh_fdr_significant_count: int
    hlz_t3_significant_count: int
    false_discoveries_filtered_count: int  # raw_sig - bh_fdr_sig
    audited_factors: List[CandidateFactorTest]
    audit_notes: str

class FactorMultipleTestingCorrectionEngine:
    """
    Quantitative research engine for applying multiple hypothesis testing corrections
    (Bonferroni, Holm-Bonferroni, Benjamini-Hochberg FDR, and Harvey-Liu-Zhu t>=3.0 haircut)
    to eliminate p-hacking and false alpha factor discoveries.
    """
    def __init__(self, alpha_target: float = 0.05, fdr_q_target: float = 0.05, hlz_t_threshold: float = 3.0):
        self.alpha_target = alpha_target
        self.fdr_q_target = fdr_q_target
        self.hlz_t_threshold = hlz_t_threshold

    def audit_and_correct_factors(self, factors: List[CandidateFactorTest]) -> FactorMultipleTestingAuditReport:
        """
        Applies Bonferroni, Holm-Bonferroni, Benjamini-Hochberg (BH FDR), and Harvey-Liu-Zhu corrections.
        """
        if not factors:
            raise ValueError("Candidate factors list cannot be empty.")

        M = len(factors)
        bonferroni_alpha = self.alpha_target / float(M)

        # 1. Raw & Bonferroni & HLZ t>=3.0 evaluation
        for f in factors:
            f.is_raw_significant = (f.raw_p_value <= self.alpha_target)
            f.is_bonferroni_significant = (f.raw_p_value <= bonferroni_alpha)
            f.is_hlz_t3_significant = (abs(f.raw_t_stat) >= self.hlz_t_threshold)

        # 2. Benjamini-Hochberg (BH) FDR Procedure
        # Sort factors ascending by raw_p_value
        sorted_factors = sorted(factors, key=lambda x: x.raw_p_value)

        # Find largest index k where p_(k) <= (k / M) * fdr_q_target
        max_k = 0
        for i, f in enumerate(sorted_factors, start=1):
            threshold_k = (i / float(M)) * self.fdr_q_target
            if f.raw_p_value <= threshold_k:
                max_k = i

        for i, f in enumerate(sorted_factors, start=1):
            f.is_bh_fdr_significant = (i <= max_k)
            # Adjusted BH p-value approximation: min(1.0, p * M / i)
            f.adjusted_p_value_bh = round(min(1.0, (f.raw_p_value * M) / float(i)), 5)

        # 3. Holm-Bonferroni Procedure (Sequential step-down)
        # Compare p_(i) <= alpha / (M - i + 1)
        holm_passed = True
        for i, f in enumerate(sorted_factors, start=1):
            if holm_passed and (f.raw_p_value <= self.alpha_target / float(M - i + 1)):
                f.is_holm_significant = True
            else:
                holm_passed = False
                f.is_holm_significant = False

        raw_cnt = sum(1 for f in factors if f.is_raw_significant)
        bonf_cnt = sum(1 for f in factors if f.is_bonferroni_significant)
        holm_cnt = sum(1 for f in factors if f.is_holm_significant)
        bh_cnt = sum(1 for f in factors if f.is_bh_fdr_significant)
        hlz_cnt = sum(1 for f in factors if f.is_hlz_t3_significant)
        false_disc_cnt = max(0, raw_cnt - bh_cnt)

        notes = (
            f"MULTIPLE TESTING CORRECTION AUDIT (M={M} factors tested): "
            f"Raw significant (t>=1.96) = {raw_cnt}. "
            f"BH FDR (q*={self.fdr_q_target}) = {bh_cnt} true discoveries. "
            f"Harvey-Liu-Zhu (t>={self.hlz_t_threshold}) = {hlz_cnt} robust factors. "
            f"Bonferroni = {bonf_cnt}, Holm = {holm_cnt}. "
            f"Filtered Out {false_disc_cnt} FALSE DISCOVERIES."
        )
        logger.info(notes)

        return FactorMultipleTestingAuditReport(
            total_factors_tested=M,
            alpha_target=self.alpha_target,
            fdr_q_target=self.fdr_q_target,
            raw_significant_count=raw_cnt,
            bonferroni_significant_count=bonf_cnt,
            holm_significant_count=holm_cnt,
            bh_fdr_significant_count=bh_cnt,
            hlz_t3_significant_count=hlz_cnt,
            false_discoveries_filtered_count=false_disc_cnt,
            audited_factors=sorted_factors,
            audit_notes=notes
        )
