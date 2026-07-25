"""
point-in-time-database-for-ml-training-data: As-of join engine for ML feature matrix construction,
data availability gap auditor, and point-in-time training dataset builder.
"""
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FeatureRecord:
    symbol: str
    feature_name: str
    value: float
    event_timestamp: str      # e.g. 2023-01-01
    available_at: str         # e.g. 2023-01-15 (public release date)


@dataclass
class LabelRecord:
    symbol: str
    label_timestamp: str      # e.g. 2023-02-01
    target_value: float


@dataclass
class PITJoinRow:
    symbol: str
    label_timestamp: str
    feature_name: str
    feature_value: Optional[float]
    feature_available_at: Optional[str]
    target_value: float
    is_valid_pit: bool


@dataclass
class PITDatasetReport:
    total_joined_rows: int
    valid_pit_rows: int
    missing_feature_rows: int
    future_leakage_prevented_count: int
    message: str


class PointInTimeMLDatabase:
    """
    Constructs leakage-free ML training datasets by executing as-of joins
    between features and labels enforcing available_at <= label_timestamp.
    """

    def __init__(self, default_lag_days: int = 0):
        self.default_lag_days = default_lag_days
        self._feature_store: List[FeatureRecord] = []

    def insert_features(self, records: List[FeatureRecord]) -> None:
        self._feature_store.extend(records)

    def as_of_join(
        self,
        labels: List[LabelRecord],
        feature_name: str,
    ) -> Tuple[List[PITJoinRow], PITDatasetReport]:
        joined_rows: List[PITJoinRow] = []
        future_leakage_prevented = 0
        missing_cnt = 0

        for label in labels:
            # Filter features for symbol and feature_name where available_at <= label_timestamp
            valid_candidates = [
                f for f in self._feature_store
                if f.symbol == label.symbol and f.feature_name == feature_name and f.available_at <= label.label_timestamp
            ]

            # Detect if a naive join (event_timestamp <= label_timestamp) would have leaked future revisions
            naive_candidates = [
                f for f in self._feature_store
                if f.symbol == label.symbol and f.feature_name == feature_name and f.event_timestamp <= label.label_timestamp
            ]

            if len(naive_candidates) > len(valid_candidates):
                future_leakage_prevented += (len(naive_candidates) - len(valid_candidates))

            if not valid_candidates:
                missing_cnt += 1
                joined_rows.append(PITJoinRow(
                    symbol=label.symbol,
                    label_timestamp=label.label_timestamp,
                    feature_name=feature_name,
                    feature_value=None,
                    feature_available_at=None,
                    target_value=label.target_value,
                    is_valid_pit=False,
                ))
            else:
                # Pick latest valid feature available_at
                best_feature = max(valid_candidates, key=lambda f: f.available_at)
                joined_rows.append(PITJoinRow(
                    symbol=label.symbol,
                    label_timestamp=label.label_timestamp,
                    feature_name=feature_name,
                    feature_value=best_feature.value,
                    feature_available_at=best_feature.available_at,
                    target_value=label.target_value,
                    is_valid_pit=True,
                ))

        n = len(joined_rows)
        valid_cnt = n - missing_cnt
        msg = f"PIT ML Join Complete: {n} label rows -> {valid_cnt} valid PIT joins ({missing_cnt} missing, {future_leakage_prevented} future revisions blocked)."
        logger.info(msg)

        report = PITDatasetReport(
            total_joined_rows=n,
            valid_pit_rows=valid_cnt,
            missing_feature_rows=missing_cnt,
            future_leakage_prevented_count=future_leakage_prevented,
            message=msg,
        )

        return joined_rows, report
