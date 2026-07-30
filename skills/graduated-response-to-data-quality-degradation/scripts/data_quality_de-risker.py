import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Result:
    success: bool
    message: str

@dataclass
class DataQualityMetrics:
    symbol: str
    stale_time_seconds: float = 0.0
    missing_sequence_count: int = 0
    price_spike_anomaly_detected: bool = False
    crossed_book_detected: bool = False
    bid_ask_spread_multiplier: float = 1.0

@dataclass
class DataQualityDeRiskReport:
    symbol: str
    data_quality_score_pct: float      # 0.0 to 100.0%
    de_risking_tier: int                # 0 (Normal), 1 (Minor), 2 (Moderate), 3 (Severe)
    tier_name: str                      # 'TIER_0_NORMAL', 'TIER_1_MINOR_DEGRADATION', 'TIER_2_MODERATE_DEGRADATION', 'TIER_3_SEVERE_OUTAGE'
    action_mandate: str                 # 'ALLOW_FULL_TRADING', 'REDUCE_SIZE_50_PCT', 'BLOCK_NEW_ENTRIES', 'EMERGENCY_HALT_AND_FLATTEN'
    position_sizing_factor: float       # 1.0, 0.50, 0.0, 0.0
    audit_notes: str

class DataQualityDeRiskerEngine:
    """
    Real-time risk management engine for monitoring market data quality degradation (stale ticks, sequence gaps,
    price spikes, crossed books) and executing graduated de-risking tiers.
    """
    def __init__(self):
        pass

    def execute(self, param: bool) -> Result:
        """Backward-compatible helper method for basic test stubs."""
        if param:
            return Result(True, "Success")
        return Result(False, "Failure")

    def audit_and_de_risk(self, metrics: DataQualityMetrics) -> DataQualityDeRiskReport:
        """
        Calculates Data Quality Score (0-100%) and returns graduated de-risking action mandate.
        """
        score = 100.0

        # Penalties
        if metrics.stale_time_seconds > 1.0:
            score -= (metrics.stale_time_seconds - 1.0) * 10.0

        if metrics.missing_sequence_count > 0:
            score -= metrics.missing_sequence_count * 2.0

        if metrics.price_spike_anomaly_detected:
            score -= 25.0

        if metrics.crossed_book_detected:
            score -= 50.0

        if metrics.bid_ask_spread_multiplier > 2.0:
            score -= (metrics.bid_ask_spread_multiplier - 2.0) * 15.0

        quality_score = max(0.0, min(100.0, round(score, 2)))

        if quality_score >= 90.0:
            tier = 0
            tier_name = "TIER_0_NORMAL"
            action = "ALLOW_FULL_TRADING"
            factor = 1.0
            notes = f"DATA QUALITY NORMAL [{metrics.symbol}]: Score = {quality_score:.1f}%. Full trading permitted."
            logger.info(notes)
        elif quality_score >= 70.0:
            tier = 1
            tier_name = "TIER_1_MINOR_DEGRADATION"
            action = "REDUCE_SIZE_50_PCT"
            factor = 0.50
            notes = f"DATA QUALITY MINOR DEGRADATION [{metrics.symbol}]: Score = {quality_score:.1f}%. 50% Position Size Haircut applied."
            logger.warning(notes)
        elif quality_score >= 40.0:
            tier = 2
            tier_name = "TIER_2_MODERATE_DEGRADATION"
            action = "BLOCK_NEW_ENTRIES"
            factor = 0.0
            notes = f"DATA QUALITY MODERATE DEGRADATION [{metrics.symbol}]: Score = {quality_score:.1f}%. New position entries BLOCKED."
            logger.warning(notes)
        else:
            tier = 3
            tier_name = "TIER_3_SEVERE_OUTAGE"
            action = "EMERGENCY_HALT_AND_FLATTEN"
            factor = 0.0
            notes = f"DATA QUALITY SEVERE OUTAGE [{metrics.symbol}]: Score = {quality_score:.1f}%. EMERGENCY HALT & FLATTEN TRIGGERED."
            logger.critical(notes)

        return DataQualityDeRiskReport(
            symbol=metrics.symbol,
            data_quality_score_pct=quality_score,
            de_risking_tier=tier,
            tier_name=tier_name,
            action_mandate=action,
            position_sizing_factor=factor,
            audit_notes=notes
        )
