"""
app-download-and-usage-data-for-consumer-companies:
Signal generation engine deriving stickiness (DAU/MAU) and churn warnings from mobile app alternative data.
"""
import dataclasses
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AppUsageDataPoint:
    ticker: str
    date: datetime
    downloads: int
    dau: int  # Daily Active Users
    mau: int  # Monthly Active Users


@dataclasses.dataclass
class AppUsageSignal:
    ticker: str
    stickiness_ratio: float
    is_world_class: bool
    churn_risk_warning: bool
    signal_summary: str


class AppUsageSignalEngine:
    """
    Evaluates raw app metrics to detect true fundamental engagement vs "vanity" download metrics.
    """

    def process(self, data: AppUsageDataPoint) -> AppUsageSignal:
        if data.mau <= 0:
            raise ValueError("MAU must be strictly positive to calculate stickiness.")
        if data.dau > data.mau:
            logger.warning(f"Data anomaly: DAU ({data.dau}) exceeds MAU ({data.mau}) for {data.ticker}. Truncating.")
            data.dau = data.mau

        # Stickiness = DAU / MAU
        stickiness = data.dau / data.mau
        
        # A stickiness ratio > 50% is widely considered world-class (e.g., top-tier social media)
        is_world_class = stickiness >= 0.50
        
        # A stickiness ratio < 20% indicates low daily engagement.
        # If accompanied by massive downloads, it signals a "Leaky Bucket" (high churn).
        # We define a heuristic: if downloads > 10% of MAU, but stickiness is < 0.20, churn risk is severe.
        is_high_acquisition = data.downloads > (data.mau * 0.10)
        churn_risk_warning = (stickiness < 0.20) and is_high_acquisition
        
        if is_world_class:
            summary = f"Strong structural engagement (Stickiness: {stickiness:.1%}). Sustainable growth."
        elif churn_risk_warning:
            summary = f"LEAKY BUCKET: High downloads but terrible retention (Stickiness: {stickiness:.1%}). High churn risk."
        else:
            summary = f"Average engagement (Stickiness: {stickiness:.1%})."
            
        logger.info(f"[{data.ticker}] {summary}")
        
        return AppUsageSignal(
            ticker=data.ticker,
            stickiness_ratio=stickiness,
            is_world_class=is_world_class,
            churn_risk_warning=churn_risk_warning,
            signal_summary=summary
        )
