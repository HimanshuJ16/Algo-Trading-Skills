"""
feature-engineering-cost-benefit-tracking: Feature ROI calculator,
cost-benefit auditor, and feature pruning advisor.
"""
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FeatureCostBenefitRecord:
    feature_name: str
    importance_score: float      # e.g. 0.05 = 5% marginal accuracy gain
    monthly_cost_usd: float      # e.g. $250/mo
    compute_latency_ms: float   # e.g. 1.2 ms
    roi_ratio: float             # importance / max(1, cost)
    recommendation: str          # KEEP, REVIEW, PRUNE
    rationale: str


@dataclass
class FeatureCostBenefitReport:
    total_features_analyzed: int
    total_monthly_cost_usd: float
    kept_features_count: int
    pruned_features_count: int
    potential_monthly_savings_usd: float
    feature_records: List[FeatureCostBenefitRecord]
    message: str


class FeatureCostBenefitTracker:
    """
    Evaluates marginal predictive accuracy gain vs compute/licensing cost
    to prune low-ROI expensive features from ML pipelines.
    """

    def __init__(
        self,
        min_importance_threshold: float = 0.01,  # 1% accuracy contribution min
        max_acceptable_cost_usd: float = 50.0,
    ):
        self.min_importance = min_importance_threshold
        self.max_cost = max_acceptable_cost_usd

    def evaluate_feature(
        self,
        feature_name: str,
        importance_score: float,
        monthly_cost_usd: float,
        compute_latency_ms: float = 0.0,
    ) -> FeatureCostBenefitRecord:
        roi = (importance_score * 100.0) / max(1.0, monthly_cost_usd)

        if importance_score < self.min_importance and monthly_cost_usd > self.max_cost:
            rec = "PRUNE"
            rat = f"Low importance ({importance_score:.3f}) and high cost (${monthly_cost_usd:.0f}/mo)."
            logger.warning(f"PRUNE RECOMMENDED for '{feature_name}': {rat}")
        elif importance_score < self.min_importance:
            rec = "REVIEW"
            rat = f"Low importance ({importance_score:.3f}) but low cost (${monthly_cost_usd:.0f}/mo)."
        elif monthly_cost_usd > 500.0:
            rec = "REVIEW"
            rat = f"High importance ({importance_score:.3f}) but very expensive (${monthly_cost_usd:.0f}/mo)."
        else:
            rec = "KEEP"
            rat = f"Good ROI: importance={importance_score:.3f}, cost=${monthly_cost_usd:.0f}/mo."
            logger.info(f"KEEP RECOMMENDED for '{feature_name}': {rat}")

        return FeatureCostBenefitRecord(
            feature_name=feature_name,
            importance_score=round(importance_score, 4),
            monthly_cost_usd=round(monthly_cost_usd, 2),
            compute_latency_ms=round(compute_latency_ms, 2),
            roi_ratio=round(roi, 4),
            recommendation=rec,
            rationale=rat,
        )

    def audit_pipeline(
        self,
        features: List[Dict[str, Any]],
    ) -> FeatureCostBenefitReport:
        records: List[FeatureCostBenefitRecord] = []
        tot_cost = 0.0
        pruned_cost = 0.0
        kept_cnt = 0
        pruned_cnt = 0

        for f in features:
            rec = self.evaluate_feature(
                f["name"],
                f["importance"],
                f["cost_usd"],
                f.get("latency_ms", 0.0),
            )
            records.append(rec)
            tot_cost += rec.monthly_cost_usd

            if rec.recommendation == "PRUNE":
                pruned_cnt += 1
                pruned_cost += rec.monthly_cost_usd
            else:
                kept_cnt += 1

        msg = (
            f"Feature Cost-Benefit Audit ({len(features)} features): "
            f"Kept {kept_cnt}, Pruned {pruned_cnt}. Potential savings: ${pruned_cost:.2f}/mo out of ${tot_cost:.2f}/mo total."
        )
        logger.info(msg)

        return FeatureCostBenefitReport(
            total_features_analyzed=len(features),
            total_monthly_cost_usd=round(tot_cost, 2),
            kept_features_count=kept_cnt,
            pruned_features_count=pruned_cnt,
            potential_monthly_savings_usd=round(pruned_cost, 2),
            feature_records=records,
            message=msg,
        )
