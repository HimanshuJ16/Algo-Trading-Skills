import logging
import math
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Default weights per quality dimension. These are operational defaults chosen for
# real-time market data feeds, NOT an external standard -- tune them per venue/vendor.
# They must sum to 1.0 so that the composite score stays on a 0.0-100.0 scale.
DEFAULT_DIMENSION_WEIGHTS: Tuple[float, float, float, float, float] = (
    0.25,   # COMPLETENESS
    0.25,   # TIMELINESS
    0.25,   # ACCURACY
    0.15,   # UNIQUENESS
    0.10,   # LIVENESS
)

# Default penalty factors: a pillar score is 100 - (defect_pct * factor), floored at 0.
# Outliers carry the steepest factor because a single mispriced tick can drive an
# execution algorithm to cross the spread, whereas a null is usually skipped.
# These are tunable engineering defaults, not regulatory or vendor-published values.
DEFAULT_NULL_PENALTY_FACTOR = 2.0
DEFAULT_OUTLIER_PENALTY_FACTOR = 5.0
DEFAULT_DUPLICATE_PENALTY_FACTOR = 2.0

# Latency at which the timeliness pillar reaches 0.0. Scores fall linearly from 100.0
# at 0 ms. Tune to the tick-to-ingest budget of the consuming strategy.
DEFAULT_LATENCY_ZERO_SCORE_MS = 500.0


@dataclass
class FeedTelemetryBatch:
    """
    Observed telemetry for one (vendor, symbol) ingestion batch over a fixed window.

    All counts are absolute record counts within the same batch, so each defect count
    must be <= total_records. A record may fall into more than one defect category
    (e.g. a duplicate that is also an outlier), so the defect counts are not required
    to sum to total_records.
    """
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

    Completeness, timeliness, accuracy and uniqueness follow the DAMA-DMBOK data quality
    dimensions; liveness is an operational feed-health pillar specific to streaming market
    data and is not a DAMA dimension.

    Scoring weights, thresholds and penalty factors are configurable engineering defaults.
    No external standard prescribes a numeric market-data DQ score, so do not present the
    composite as a regulatory metric.
    """

    def __init__(
        self,
        min_healthy_score: float = 85.0,
        critical_failover_score: float = 70.0,
        null_penalty_factor: float = DEFAULT_NULL_PENALTY_FACTOR,
        outlier_penalty_factor: float = DEFAULT_OUTLIER_PENALTY_FACTOR,
        duplicate_penalty_factor: float = DEFAULT_DUPLICATE_PENALTY_FACTOR,
        latency_zero_score_ms: float = DEFAULT_LATENCY_ZERO_SCORE_MS,
        dimension_weights: Tuple[float, float, float, float, float] = DEFAULT_DIMENSION_WEIGHTS,
    ) -> None:
        """
        Raises:
            ValueError: if any threshold, penalty factor or weight is outside its valid
                range, or if critical_failover_score >= min_healthy_score (which would
                make the WARNING band unreachable).
        """
        self._require_finite(min_healthy_score, "min_healthy_score")
        self._require_finite(critical_failover_score, "critical_failover_score")

        if not 0.0 <= critical_failover_score <= 100.0:
            raise ValueError(
                f"critical_failover_score must be within [0.0, 100.0], got {critical_failover_score}"
            )
        if not 0.0 <= min_healthy_score <= 100.0:
            raise ValueError(
                f"min_healthy_score must be within [0.0, 100.0], got {min_healthy_score}"
            )
        if critical_failover_score >= min_healthy_score:
            raise ValueError(
                "critical_failover_score must be strictly below min_healthy_score, got "
                f"{critical_failover_score} >= {min_healthy_score}; otherwise the WARNING "
                "band is unreachable and degraded feeds escalate straight to failover."
            )

        for name, factor in (
            ("null_penalty_factor", null_penalty_factor),
            ("outlier_penalty_factor", outlier_penalty_factor),
            ("duplicate_penalty_factor", duplicate_penalty_factor),
        ):
            self._require_finite(factor, name)
            if factor < 0.0:
                raise ValueError(f"{name} must be >= 0.0, got {factor}")

        self._require_finite(latency_zero_score_ms, "latency_zero_score_ms")
        if latency_zero_score_ms <= 0.0:
            raise ValueError(
                f"latency_zero_score_ms must be > 0.0, got {latency_zero_score_ms}"
            )

        if len(dimension_weights) != 5:
            raise ValueError(
                f"dimension_weights must contain exactly 5 weights, got {len(dimension_weights)}"
            )
        for idx, weight in enumerate(dimension_weights):
            self._require_finite(weight, f"dimension_weights[{idx}]")
            if weight < 0.0:
                raise ValueError(f"dimension_weights[{idx}] must be >= 0.0, got {weight}")
        weight_sum = math.fsum(dimension_weights)
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError(
                f"dimension_weights must sum to 1.0 so the composite stays on a 0-100 "
                f"scale, got {weight_sum}"
            )

        self.min_healthy_score = min_healthy_score
        self.critical_failover_score = critical_failover_score
        self.null_penalty_factor = null_penalty_factor
        self.outlier_penalty_factor = outlier_penalty_factor
        self.duplicate_penalty_factor = duplicate_penalty_factor
        self.latency_zero_score_ms = latency_zero_score_ms
        self.dimension_weights = tuple(dimension_weights)

    @staticmethod
    def _require_finite(value: float, name: str) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be a real number, got {value!r}")
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite, got {value!r}")

    @staticmethod
    def _validate_batch(batch: FeedTelemetryBatch) -> None:
        """
        Rejects structurally impossible telemetry.

        A corrupt batch is a collector/pipeline defect, not a feed condition, and is
        raised rather than scored. Scoring it silently produces misleading verdicts:
        a negative count or latency drives pillar scores above 100, so the composite
        exceeds its documented 0-100 range and a corrupt batch reports as HEALTHY,
        while a non-finite latency is floored to a timeliness of 0.0 and becomes
        indistinguishable from a feed that is merely late.
        """
        if not isinstance(batch.vendor_id, str) or not batch.vendor_id.strip():
            raise ValueError("vendor_id must be a non-empty string")
        if not isinstance(batch.symbol, str) or not batch.symbol.strip():
            raise ValueError("symbol must be a non-empty string")

        for name, count in (
            ("total_records", batch.total_records),
            ("null_records_count", batch.null_records_count),
            ("duplicate_records_count", batch.duplicate_records_count),
            ("outlier_records_count", batch.outlier_records_count),
        ):
            if not isinstance(count, int) or isinstance(count, bool):
                raise ValueError(f"{name} must be an int, got {count!r}")
            if count < 0:
                raise ValueError(f"{name} must be >= 0, got {count}")

        for name, count in (
            ("null_records_count", batch.null_records_count),
            ("duplicate_records_count", batch.duplicate_records_count),
            ("outlier_records_count", batch.outlier_records_count),
        ):
            if count > batch.total_records:
                raise ValueError(
                    f"{name} ({count}) cannot exceed total_records ({batch.total_records})"
                )

        DataQualityMonitoringEngine._require_finite(batch.avg_latency_ms, "avg_latency_ms")
        if batch.avg_latency_ms < 0.0:
            raise ValueError(f"avg_latency_ms must be >= 0.0, got {batch.avg_latency_ms}")

        DataQualityMonitoringEngine._require_finite(batch.ticks_per_second, "ticks_per_second")
        if batch.ticks_per_second < 0.0:
            raise ValueError(f"ticks_per_second must be >= 0.0, got {batch.ticks_per_second}")

    def audit_feed_quality(self, batch: FeedTelemetryBatch) -> DataQualityMonitoringReport:
        """
        Calculates the composite Data Quality Score across 5 dimensions.

        Pillar scores (each floored at 0.0 and rounded to 2 decimals before weighting):
          COMPLETENESS = 100 - null_pct * null_penalty_factor
          TIMELINESS   = 100 * (1 - avg_latency_ms / latency_zero_score_ms)
          ACCURACY     = 100 - outlier_pct * outlier_penalty_factor
          UNIQUENESS   = 100 - duplicate_pct * duplicate_penalty_factor
          LIVENESS     = 100 if ticks_per_second > 0 else 0

        A zero tick rate forces CRITICAL and failover regardless of the composite score:
        a stalled feed can still look near-perfect on the count-based pillars because
        the last delivered records were themselves clean.

        Raises:
            ValueError: if the batch is structurally invalid (negative counts, a defect
                count exceeding total_records, or a non-finite latency/tick rate).
        """
        self._validate_batch(batch)

        if batch.total_records == 0:
            msg = (
                f"DEAD FEED [{batch.vendor_id}]: Zero records received for symbol {batch.symbol}!"
            )
            logger.critical(msg)
            return DataQualityMonitoringReport(
                vendor_id=batch.vendor_id, symbol=batch.symbol, composite_dq_score=0.0,
                status="CRITICAL", is_failover_recommended=True, dimensions=[],
                alerts=[msg],
            )

        n_total = float(batch.total_records)
        w_comp, w_time, w_acc, w_uniq, w_live = self.dimension_weights

        # 1. Completeness
        null_pct = (batch.null_records_count / n_total) * 100.0
        comp_score = max(0.0, round(100.0 - null_pct * self.null_penalty_factor, 2))
        comp_msg = f"Null count: {batch.null_records_count}/{batch.total_records} ({null_pct:.2f}%)"

        # 2. Timeliness / Latency: 100 at 0ms, falling linearly to 0 at latency_zero_score_ms
        time_score = max(
            0.0,
            round(100.0 * (1.0 - batch.avg_latency_ms / self.latency_zero_score_ms), 2),
        )
        time_msg = f"Average Ingestion Latency: {batch.avg_latency_ms:.1f}ms"

        # 3. Accuracy / Outlier Index
        outlier_pct = (batch.outlier_records_count / n_total) * 100.0
        acc_score = max(0.0, round(100.0 - outlier_pct * self.outlier_penalty_factor, 2))
        acc_msg = f"Outlier count: {batch.outlier_records_count}/{batch.total_records} ({outlier_pct:.2f}%)"

        # 4. Uniqueness
        dup_pct = (batch.duplicate_records_count / n_total) * 100.0
        uniq_score = max(0.0, round(100.0 - dup_pct * self.duplicate_penalty_factor, 2))
        uniq_msg = f"Duplicate count: {batch.duplicate_records_count}/{batch.total_records} ({dup_pct:.2f}%)"

        # 5. Liveness
        live_score = 100.0 if batch.ticks_per_second > 0 else 0.0
        live_msg = f"Ticks per Second: {batch.ticks_per_second:.1f} TPS"

        dimensions = [
            DataQualityDimensionScore("COMPLETENESS", comp_score, w_comp, comp_msg),
            DataQualityDimensionScore("TIMELINESS", time_score, w_time, time_msg),
            DataQualityDimensionScore("ACCURACY", acc_score, w_acc, acc_msg),
            DataQualityDimensionScore("UNIQUENESS", uniq_score, w_uniq, uniq_msg),
            DataQualityDimensionScore("LIVENESS", live_score, w_live, live_msg),
        ]

        composite_score = round(math.fsum(d.score * d.weight for d in dimensions), 2)

        alerts: List[str] = []
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
            msg = (
                f"CRITICAL DATA QUALITY BREACH [{batch.vendor_id}]: DQ Score {composite_score} "
                f"< {self.critical_failover_score}. Triggering fallback failover!"
            )
            alerts.append(msg)
            logger.critical(msg)
        elif composite_score < self.min_healthy_score:
            status = "WARNING"
            msg = (
                f"DEGRADED DATA QUALITY [{batch.vendor_id}]: DQ Score {composite_score} "
                f"< {self.min_healthy_score}."
            )
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
            alerts=alerts,
        )
