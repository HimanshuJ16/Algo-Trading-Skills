import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FeedTelemetryBatch:
    vendor_id: str
    symbol: str
    total_records: int
    null_records_count: int
    duplicate_records_count: int
    outlier_records_count: int
    avg_latency_ms: float
    ticks_per_second: float

@dataclass
class DataQualityDimensionScore:
    dimension_name: str                 # 'COMPLETENESS', 'TIMELINESS', 'ACCURACY', 'UNIQUENESS', 'LIVENESS'
    score: float                        # 0.0 to 100.0
    weight: float
    status_msg: str

@dataclass
class DataQualityMonitoringReport:
    vendor_id: str
    symbol: str
    composite_dq_score: float           # 0.0 to 100.0
    status: str                         # 'HEALTHY', 'WARNING', 'CRITICAL'
    is_failover_recommended: bool
    dimensions: List[DataQualityDimensionScore]
    alerts: List[str]

class DataQualityMonitoringEngine:
    """
    Real-time market data quality monitoring dashboard engine for auditing completeness,
    timeliness (latency), accuracy (outliers), uniqueness, and feed liveness across data vendors.
    """
    def __init__(self, min_healthy_score: float = 85.0, critical_failover_score: float = 70.0):
        self.min_healthy_score = min_healthy_score
        self.critical_failover_score = critical_failover_score

    def audit_feed_quality(self, batch: FeedTelemetryBatch) -> DataQualityMonitoringReport:
        """
        Calculates composite Data Quality Score across 5 core dimensions.
        """
        if batch.total_records <= 0:
            return DataQualityMonitoringReport(
                vendor_id=batch.vendor_id, symbol=batch.symbol, composite_dq_score=0.0,
                status="CRITICAL", is_failover_recommended=True, dimensions=[],
                alerts=[f"DEAD FEED [{batch.vendor_id}]: Zero records received for symbol {batch.symbol}!"]
            )

        n_total = float(batch.total_records)

        # 1. Completeness (25%)
        null_pct = (batch.null_records_count / n_total) * 100.0
        comp_score = max(0.0, round(100.0 - null_pct * 2.0, 2))
        comp_msg = f"Null count: {batch.null_records_count}/{batch.total_records} ({null_pct:.2f}%)"

        # 2. Timeliness / Latency (25%)
        # Latency score: 100 at 0ms, drops linearly to 0 at 500ms
        time_score = max(0.0, round(100.0 - (batch.avg_latency_ms / 5.0), 2))
        time_msg = f"Average Ingestion Latency: {batch.avg_latency_ms:.1f}ms"

        # 3. Accuracy / Outlier Index (25%)
        outlier_pct = (batch.outlier_records_count / n_total) * 100.0
        acc_score = max(0.0, round(100.0 - outlier_pct * 5.0, 2))
        acc_msg = f"Outlier count: {batch.outlier_records_count}/{batch.total_records} ({outlier_pct:.2f}%)"

        # 4. Uniqueness (15%)
        dup_pct = (batch.duplicate_records_count / n_total) * 100.0
        uniq_score = max(0.0, round(100.0 - dup_pct * 2.0, 2))
        uniq_msg = f"Duplicate count: {batch.duplicate_records_count}/{batch.total_records} ({dup_pct:.2f}%)"

        # 5. Liveness (10%)
        live_score = 100.0 if batch.ticks_per_second > 0 else 0.0
        live_msg = f"Ticks per Second: {batch.ticks_per_second:.1f} TPS"

        dimensions = [
            DataQualityDimensionScore("COMPLETENESS", comp_score, 0.25, comp_msg),
            DataQualityDimensionScore("TIMELINESS", time_score, 0.25, time_msg),
            DataQualityDimensionScore("ACCURACY", acc_score, 0.25, acc_msg),
            DataQualityDimensionScore("UNIQUENESS", uniq_score, 0.15, uniq_msg),
            DataQualityDimensionScore("LIVENESS", live_score, 0.10, live_msg)
        ]

        composite_score = round(sum(d.score * d.weight for d in dimensions), 2)

        alerts = []
        failover = False

        if batch.ticks_per_second <= 0:
            status = "CRITICAL"
            failover = True
            msg = f"DEAD FEED [{batch.vendor_id}]: Zero ticks per second detected!"
            alerts.append(msg)
            logger.critical(msg)
        elif composite_score < self.critical_failover_score:
            status = "CRITICAL"
            failover = True
            msg = f"CRITICAL DATA QUALITY BREACH [{batch.vendor_id}]: DQ Score {composite_score} < {self.critical_failover_score}. Triggering fallback failover!"
            alerts.append(msg)
            logger.critical(msg)
        elif composite_score < self.min_healthy_score:
            status = "WARNING"
            msg = f"DEGRADED DATA QUALITY [{batch.vendor_id}]: DQ Score {composite_score} < {self.min_healthy_score}."
            alerts.append(msg)
            logger.warning(msg)
        else:
            status = "HEALTHY"
            alerts.append("Data feed quality within operational parameters.")

        return DataQualityMonitoringReport(
            vendor_id=batch.vendor_id,
            symbol=batch.symbol,
            composite_dq_score=composite_score,
            status=status,
            is_failover_recommended=failover,
            dimensions=dimensions,
            alerts=alerts
        )
