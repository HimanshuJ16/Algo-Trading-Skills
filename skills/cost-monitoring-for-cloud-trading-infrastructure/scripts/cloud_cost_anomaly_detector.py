import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

FLAT_BASELINE_STD_EPS = 1e-4


def _require_finite_cost(value: float, label: str) -> None:
    """Costs may be negative (credits/refunds) but must be finite — a NaN
    telemetry value would otherwise make every threshold comparison False and
    silently classify the day as NORMAL."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")


@dataclass
class CostRecord:
    timestamp: str
    service_name: str                  # e.g. 'EC2', 'EgressBandwidth', 'RDS'
    category: str                      # 'COMPUTE', 'NETWORK_EGRESS', 'STORAGE', 'DATABASE'
    environment: str                   # 'PROD', 'STAGING', 'DEV'
    cost_usd: float
    # Carried for reporting/reconciliation only; no detection path reads it.
    # Unit economics use the `trading_volume` argument of analyze_service_cost.
    units_consumed: float              # e.g. GB transferred, hours run


@dataclass
class CostAnomalyReport:
    service_name: str
    current_cost_usd: float
    baseline_mean_usd: float
    baseline_std_usd: float
    z_score: float
    percentage_change_pct: float
    severity: str                      # 'NORMAL', 'WARNING', 'CRITICAL'
    unit_cost_usd: float
    recommendation: str


class CloudCostAnomalyDetector:
    """
    Quantitative FinOps engine for evaluating cloud infrastructure expenditure,
    detecting cost spikes using rolling Z-scores, and auditing unit economics.

    Baselines are scoped to (service_name, environment): PROD and DEV spend
    for the same service never share a baseline. When the baseline std is
    ~0 (flat spend, e.g. reserved capacity), the Z-score degenerates to the
    absolute dollar deviation from the mean and is therefore denominated in
    dollars, not sigmas. Two relative-change gates keep that from generating
    alert storms on large flat baselines: the >30% mean-increase requirement on
    CRITICAL, and `flat_baseline_min_pct_change` on WARNING.

    Detection is one-sided: only positive deviations escalate, so a spend
    collapse (dead feed handler, lapsed subscription) reports NORMAL.
    """

    def __init__(
        self,
        z_warning_threshold: float = 2.0,
        z_critical_threshold: float = 3.0,
        flat_baseline_min_pct_change: float = 1.0,
    ):
        """
        `flat_baseline_min_pct_change` is the relative-materiality floor applied
        ONLY when the baseline is flat (std ~ 0), where the "z" is a dollar
        deviation rather than a standard-deviation count and is therefore
        scale-dependent. Without it, +$3 on a $100,000/day reserved-capacity
        baseline (+0.003%) reaches z=3.0 and pages on-call. It is an
        operational tuning knob, not a published standard - set it from your
        own budget tolerance. It does not affect non-flat baselines, where a
        small percentage move can still be a genuine statistical outlier.
        """
        if not (
            isinstance(z_warning_threshold, (int, float))
            and isinstance(z_critical_threshold, (int, float))
            and math.isfinite(z_warning_threshold)
            and math.isfinite(z_critical_threshold)
            and 0.0 <= z_warning_threshold <= z_critical_threshold
        ):
            raise ValueError(
                "thresholds must be finite with 0 <= warning <= critical, got "
                f"warning={z_warning_threshold!r}, critical={z_critical_threshold!r}"
            )
        if not (
            isinstance(flat_baseline_min_pct_change, (int, float))
            and not isinstance(flat_baseline_min_pct_change, bool)
            and math.isfinite(flat_baseline_min_pct_change)
            and flat_baseline_min_pct_change >= 0.0
        ):
            raise ValueError(
                "flat_baseline_min_pct_change must be a non-negative finite "
                f"number, got {flat_baseline_min_pct_change!r}"
            )
        self.z_warning_threshold = z_warning_threshold
        self.z_critical_threshold = z_critical_threshold
        self.flat_baseline_min_pct_change = flat_baseline_min_pct_change

    def compute_z_score(
        self, current_cost: float, historical_costs: List[float]
    ) -> Tuple[float, float, float]:
        """
        Calculates Z-score, baseline mean, and baseline std dev (population
        std, ddof=0), each rounded to 2 decimals for reporting. With an empty
        baseline returns (0.0, current_cost, 0.0).
        """
        z, mean, std = self._z_components(current_cost, historical_costs)
        return round(z, 2), round(mean, 2), round(std, 2)

    def _z_components(
        self, current_cost: float, historical_costs: List[float]
    ) -> Tuple[float, float, float]:
        """Unrounded z/mean/std — severity decisions must compare unrounded
        values (a z of 1.996 rounds to 2.0 but is below the threshold)."""
        _require_finite_cost(current_cost, "current_cost")
        if not historical_costs:
            return 0.0, float(current_cost), 0.0

        arr = np.array(historical_costs, dtype=float)
        if not np.all(np.isfinite(arr)):
            raise ValueError(
                "historical_costs contains a non-finite value; fix the "
                "telemetry before computing a baseline"
            )
        mean = float(np.mean(arr))
        std = float(np.std(arr))

        if std < FLAT_BASELINE_STD_EPS:
            # Flat baseline: z degenerates to the absolute dollar deviation.
            z_score = 0.0 if abs(current_cost - mean) < FLAT_BASELINE_STD_EPS else (
                current_cost - mean
            )
        else:
            z_score = (current_cost - mean) / std

        return float(z_score), mean, std

    def analyze_service_cost(
        self,
        current_record: CostRecord,
        historical_records: List[CostRecord],
        trading_volume: float = 1.0
    ) -> CostAnomalyReport:
        """
        Analyzes a current service cost record against historical baseline
        records for the SAME service AND environment. `trading_volume` is the
        period's executed-trade count (default 1.0 => unit cost equals raw
        spend; always pass the real volume for meaningful unit economics). A
        volume of 0 with positive spend yields an infinite unit cost.

        `historical_records` must ALREADY be the intended rolling window: this
        method filters by (service_name, environment) but does not sort by
        timestamp, deduplicate, or truncate to a window. Passing a year of
        history to a "14-day rolling baseline" silently widens the baseline.
        """
        _require_finite_cost(current_record.cost_usd, f"cost_usd ({current_record.service_name})")
        if (
            not isinstance(trading_volume, (int, float))
            or isinstance(trading_volume, bool)
            or not math.isfinite(trading_volume)
            or trading_volume < 0
        ):
            raise ValueError(
                f"trading_volume must be a non-negative finite number, got {trading_volume!r}"
            )

        hist_costs = [
            r.cost_usd for r in historical_records
            if r.service_name == current_record.service_name
            and r.environment == current_record.environment
        ]
        if len(hist_costs) < len(historical_records):
            logger.info(
                "Baseline for %s scoped to environment %s (%d of %d history records).",
                current_record.service_name, current_record.environment,
                len(hist_costs), len(historical_records),
            )
        if not hist_costs:
            logger.warning(
                "No baseline history for %s in environment %s - cannot assess "
                "anomaly status; treating as NORMAL until a baseline accrues.",
                current_record.service_name, current_record.environment,
            )

        z_score, mean, std = self._z_components(current_record.cost_usd, hist_costs)

        if mean > 0:
            pct_change = (current_record.cost_usd - mean) / mean * 100.0
        elif current_record.cost_usd > 0:
            # Baseline mean of $0 with positive spend: the percentage increase
            # is unbounded; report it as infinite so the CRITICAL gate cannot
            # be defeated by a zero-cost baseline.
            pct_change = float("inf")
        else:
            pct_change = 0.0
        if trading_volume > 0:
            unit_cost = current_record.cost_usd / trading_volume
        elif current_record.cost_usd > 0:
            # Spend with zero executed trades is the WORST unit economics
            # possible (a halted strategy still burning compute), not the best.
            # Reporting $0.00/trade here would read as perfect efficiency, so
            # report it as unbounded - same convention as the $0-mean baseline.
            unit_cost = float("inf")
        else:
            unit_cost = 0.0

        # On a flat baseline the "z" is a dollar deviation, not a
        # standard-deviation count, so it is scale-dependent. Require relative
        # materiality before WARNING fires there; CRITICAL already carries its
        # own >30% gate below.
        baseline_is_flat = bool(hist_costs) and std < FLAT_BASELINE_STD_EPS
        warning_is_material = (
            not baseline_is_flat or pct_change > self.flat_baseline_min_pct_change
        )

        # Severity Classification — CRITICAL requires BOTH the z gate and a
        # >30% mean increase, so flat baselines with trivial absolute
        # deviations do not page anyone.
        if z_score >= self.z_critical_threshold and pct_change > 30.0:
            severity = "CRITICAL"
            rec = f"CRITICAL COST SPIKE: {current_record.service_name} spend (${current_record.cost_usd}) is {round(z_score, 2)} std dev above baseline. Inspect runaway workers or cross-AZ egress."
            logger.critical(rec)
        elif z_score >= self.z_warning_threshold and warning_is_material:
            severity = "WARNING"
            rec = f"WARNING COST SPIKE: {current_record.service_name} spend (${current_record.cost_usd}) elevated ({pct_change:.1f}% above mean)."
            logger.warning(rec)
        else:
            severity = "NORMAL"
            rec = f"Service {current_record.service_name} spend is within expected baseline bounds."
            if baseline_is_flat and z_score >= self.z_warning_threshold:
                # Don't report a suppressed deviation as an unremarkable day.
                rec = (
                    f"Service {current_record.service_name} deviated "
                    f"${z_score:.2f} from a flat baseline but only "
                    f"{pct_change:.3g}% — below the "
                    f"{self.flat_baseline_min_pct_change}% materiality floor."
                )
            if not hist_costs:
                rec = (
                    f"Service {current_record.service_name} has no baseline history in "
                    f"environment {current_record.environment}; anomaly status UNKNOWN."
                )

        return CostAnomalyReport(
            service_name=current_record.service_name,
            current_cost_usd=round(current_record.cost_usd, 2),
            baseline_mean_usd=round(mean, 2),
            baseline_std_usd=round(std, 2),
            z_score=round(z_score, 2),
            percentage_change_pct=(
                round(pct_change, 2) if math.isfinite(pct_change) else pct_change
            ),
            severity=severity,
            unit_cost_usd=round(unit_cost, 6) if math.isfinite(unit_cost) else unit_cost,
            recommendation=rec
        )
