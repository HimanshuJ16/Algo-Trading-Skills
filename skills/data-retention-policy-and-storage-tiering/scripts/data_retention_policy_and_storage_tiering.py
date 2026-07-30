import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Storage Pricing per GB per Month (USD)
TIER_PRICING_USD_PER_GB = {
    "HOT_NVME": 0.20,                  # $0.20/GB/mo (High speed NVMe/in-memory)
    "WARM_PARQUET_S3": 0.023,           # $0.023/GB/mo (S3 Standard / Parquet)
    "COLD_GLACIER_INSTANT": 0.004,      # $0.004/GB/mo (Glacier Instant Retrieval)
    "DEEP_ARCHIVE": 0.00099,            # $0.00099/GB/mo (Glacier Deep Archive)
    "PURGE": 0.00                       # $0.00/GB/mo (Deleted)
}

@dataclass
class MarketDatasetInfo:
    dataset_id: str
    data_type: str                      # 'L3_TICK', 'L2_ORDER_BOOK', 'TRADE_AUDIT_LOG', 'FEATURE_MATRIX'
    size_gb: float
    age_days: int
    current_tier: str                   # 'HOT_NVME', 'WARM_PARQUET_S3', 'COLD_GLACIER_INSTANT', 'DEEP_ARCHIVE'
    regulatory_retention_years: float   # e.g. 6.0 years under SEC 17a-4

@dataclass
class TierTransitionRecommendation:
    dataset_id: str
    current_tier: str
    recommended_tier: str
    size_gb: float
    current_monthly_cost_usd: float
    recommended_monthly_cost_usd: float
    monthly_cost_savings_usd: float
    action_required: str               # 'NO_CHANGE', 'TRANSITION_TO_WARM', 'TRANSITION_TO_COLD', 'DEEP_ARCHIVE', 'PURGE'

@dataclass
class DataRetentionAuditReport:
    total_datasets_audited: int
    total_size_tb: float
    total_current_monthly_cost_usd: float
    total_recommended_monthly_cost_usd: float
    total_monthly_savings_usd: float
    recommendations: List[TierTransitionRecommendation]

class DataRetentionPolicyEngine:
    """
    Quantitative storage tiering and lifecycle engine for transitioning market data
    (L2/L3 ticks, Parquet backtests, trade logs) across HOT (NVMe), WARM (S3), COLD (Glacier), and DEEP ARCHIVE tiers.
    """
    def __init__(self, pricing_map: Optional[Dict[str, float]] = None):
        self.pricing_map = pricing_map or TIER_PRICING_USD_PER_GB

    def evaluate_dataset_lifecycle(self, dataset: MarketDatasetInfo) -> TierTransitionRecommendation:
        """
        Determines target storage tier based on dataset age and regulatory retention requirements.
        """
        age = dataset.age_days
        retention_days = int(dataset.regulatory_retention_years * 365.0)

        # Lifecycle logic:
        # Age <= 30 days -> HOT_NVME
        # 31 <= Age <= 365 -> WARM_PARQUET_S3
        # 366 <= Age <= retention_days -> COLD_GLACIER_INSTANT
        # Age > retention_days: If regulatory requirement exists -> DEEP_ARCHIVE; else PURGE
        if age <= 30:
            target_tier = "HOT_NVME"
            action = "NO_CHANGE" if dataset.current_tier == "HOT_NVME" else "TRANSITION_TO_HOT"
        elif age <= 365:
            target_tier = "WARM_PARQUET_S3"
            action = "NO_CHANGE" if dataset.current_tier == "WARM_PARQUET_S3" else "TRANSITION_TO_WARM"
        elif age <= retention_days:
            target_tier = "COLD_GLACIER_INSTANT"
            action = "NO_CHANGE" if dataset.current_tier == "COLD_GLACIER_INSTANT" else "TRANSITION_TO_COLD"
        else:
            if dataset.regulatory_retention_years > 0:
                target_tier = "DEEP_ARCHIVE"
                action = "TRANSITION_TO_DEEP_ARCHIVE"
            else:
                target_tier = "PURGE"
                action = "PURGE"

        curr_price = self.pricing_map.get(dataset.current_tier, 0.20)
        target_price = self.pricing_map.get(target_tier, 0.0)

        curr_cost = round(dataset.size_gb * curr_price, 2)
        target_cost = round(dataset.size_gb * target_price, 2)
        savings = round(curr_cost - target_cost, 2)

        if savings > 0:
            logger.info(
                f"STORAGE TIER OPTIMIZATION [{dataset.dataset_id}]: {dataset.current_tier} -> {target_tier}. "
                f"Monthly Cost: ${curr_cost:,.2f} -> ${target_cost:,.2f} (Save ${savings:,.2f}/mo)"
            )

        return TierTransitionRecommendation(
            dataset_id=dataset.dataset_id,
            current_tier=dataset.current_tier,
            recommended_tier=target_tier,
            size_gb=dataset.size_gb,
            current_monthly_cost_usd=curr_cost,
            recommended_monthly_cost_usd=target_cost,
            monthly_cost_savings_usd=savings,
            action_required=action
        )

    def audit_storage_fleet(self, datasets: List[MarketDatasetInfo]) -> DataRetentionAuditReport:
        recs = [self.evaluate_dataset_lifecycle(d) for d in datasets]

        total_gb = sum(d.size_gb for d in datasets)
        total_tb = round(total_gb / 1000.0, 2)
        total_curr_cost = round(sum(r.current_monthly_cost_usd for r in recs), 2)
        total_rec_cost = round(sum(r.recommended_monthly_cost_usd for r in recs), 2)
        total_savings = round(sum(r.monthly_cost_savings_usd for r in recs), 2)

        return DataRetentionAuditReport(
            total_datasets_audited=len(datasets),
            total_size_tb=total_tb,
            total_current_monthly_cost_usd=total_curr_cost,
            total_recommended_monthly_cost_usd=total_rec_cost,
            total_monthly_savings_usd=total_savings,
            recommendations=recs
        )
