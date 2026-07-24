"""
feature-engineering-without-leakage: Production-grade feature leakage audit engine,
cross-correlation lead/lag analysis, point-in-time as-of join verifier, and intentional
leakage calibration benchmarking.
"""
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class LeakageType(str, Enum):
    FUTURE_LOOKAHEAD = "FUTURE_LOOKAHEAD"
    SAME_BAR_CONTAMINATION = "SAME_BAR_CONTAMINATION"
    UNSHIFTED_ROLLING = "UNSHIFTED_ROLLING"
    ASOF_TIMING_VIOLATION = "ASOF_TIMING_VIOLATION"


@dataclass
class LeakageFinding:
    feature_name: str
    leakage_type: LeakageType
    max_correlation_lead: int
    correlation_value: float
    message: str


class FeatureLeakageAuditor:
    """
    Automated auditor scanning pandas DataFrame features for lookahead bias,
    un-lagged target contamination, and point-in-time timestamp violations.
    """

    def __init__(self, correlation_threshold: float = 0.85):
        self.correlation_threshold = correlation_threshold

    def audit_dataframe(
        self,
        df: pd.DataFrame,
        target_col: str,
        feature_cols: Optional[List[str]] = None,
        max_lead_periods: int = 5,
    ) -> List[LeakageFinding]:
        """
        Audits feature columns against a target column across lead/lag offsets.
        Identifies features that correlate suspiciously with future target values (lead >= 1).
        """
        if target_col not in df.columns:
            raise KeyError(f"Target column '{target_col}' not found in DataFrame.")

        features = feature_cols or [c for c in df.columns if c != target_col]
        findings = []

        target = df[target_col].astype(float)

        for col in features:
            if col not in df.columns:
                continue
            series = df[col].astype(float)

            # 1. Check Same-Bar Direct Contamination (corr == 1.0 or exact identity)
            zero_corr = series.corr(target)
            if not np.isnan(zero_corr) and abs(zero_corr) >= 0.99:
                findings.append(
                    LeakageFinding(
                        feature_name=col,
                        leakage_type=LeakageType.SAME_BAR_CONTAMINATION,
                        max_correlation_lead=0,
                        correlation_value=round(zero_corr, 4),
                        message=f"Feature '{col}' has near-identical correlation ({zero_corr:.4f}) with target at t=0. Possible target contamination.",
                    )
                )

            # 2. Check Lead Correlations (Feature vs Target in Future: t+1, t+2, ...)
            for lead in range(1, max_lead_periods + 1):
                lead_target = target.shift(-lead)
                lead_corr = series.corr(lead_target)

                if not np.isnan(lead_corr) and abs(lead_corr) >= self.correlation_threshold:
                    findings.append(
                        LeakageFinding(
                            feature_name=col,
                            leakage_type=LeakageType.FUTURE_LOOKAHEAD,
                            max_correlation_lead=lead,
                            correlation_value=round(lead_corr, 4),
                            message=f"Feature '{col}' correlates strongly ({lead_corr:.4f}) with future target at t+{lead}. Lookahead leakage suspected.",
                        )
                    )
                    break

        return findings

    @staticmethod
    def point_in_time_asof_merge(
        left_df: pd.DataFrame,
        right_df: pd.DataFrame,
        on_timestamp_col: str,
        by_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Performs strict point-in-time backward as-of merge to prevent future data leakage.
        Ensures right_df timestamps strictly precede or match left_df timestamps.
        """
        left_sorted = left_df.sort_values(on_timestamp_col)
        right_sorted = right_df.sort_values(on_timestamp_col)

        merged = pd.merge_asof(
            left_sorted,
            right_sorted,
            on=on_timestamp_col,
            by=by_col,
            direction="backward",  # Strictly backward matching!
        )
        return merged

    def run_intentional_leakage_calibration(
        self, df: pd.DataFrame, target_col: str
    ) -> Tuple[float, List[LeakageFinding]]:
        """
        Injects a known leaked feature (target shifted -1) to calibrate leakage detection threshold.
        Returns (leaked_corr, findings).
        """
        temp_df = df.copy()
        temp_df["__leaked_feature__"] = temp_df[target_col].shift(-1)
        findings = self.audit_dataframe(temp_df, target_col=target_col, feature_cols=["__leaked_feature__"])
        leaked_corr = temp_df["__leaked_feature__"].corr(temp_df[target_col].shift(-1))
        return leaked_corr if not np.isnan(leaked_corr) else 0.0, findings


# Backward compatibility functions
def verify_shift_direction(series: pd.Series, shift_periods: int, expected_lag: bool = True):
    if expected_lag and shift_periods <= 0:
        raise ValueError(
            "Expected a backward-looking (lag) shift but got shift_periods <= 0 -- this likely leaks future data."
        )
    return series.shift(shift_periods)
