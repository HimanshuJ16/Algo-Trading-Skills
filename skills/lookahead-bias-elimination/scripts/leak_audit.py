"""
lookahead-bias-elimination: Production-grade backtest lookahead bias auditor,
same-bar fill contamination detector, indicator warmup checker,
signal execution bar alignment, and forward-leak calibration benchmarking.
"""
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class LookaheadViolationType(str, Enum):
    SAME_BAR_FILL_CONTAMINATION = "SAME_BAR_FILL_CONTAMINATION"
    INDICATOR_UNWARMED_EXECUTION = "INDICATOR_UNWARMED_EXECUTION"
    CENTERED_ROLLING_WINDOW = "CENTERED_ROLLING_WINDOW"
    TIMESTAMP_CAUSALITY_BREACH = "TIMESTAMP_CAUSALITY_BREACH"


@dataclass
class LookaheadAuditFinding:
    violation_type: LookaheadViolationType
    row_index: int
    timestamp: str
    details: str


class LookaheadBiasAuditor:
    """
    Production backtest auditor verifying strict causality:
    Signals generated at bar T must execute at bar T+1 Open, never same-bar T Close.
    """

    def __init__(self, warmup_periods: int = 30):
        self.warmup_periods = warmup_periods

    def audit_backtest_timing(
        self,
        df: pd.DataFrame,
        signal_col: str = "signal",
        close_col: str = "close",
        fill_price_col: str = "fill_price",
        timestamp_col: Optional[str] = None,
    ) -> List[LookaheadAuditFinding]:
        """
        Audits DataFrame for same-bar fill contamination (filling signal at same-bar Close/High/Low).
        """
        findings = []

        if signal_col not in df.columns or fill_price_col not in df.columns:
            return findings

        # Check if fill_price matches same-bar close exactly when signal is non-zero
        active_signals = df[df[signal_col] != 0]

        for idx, row in active_signals.iterrows():
            ts = str(row[timestamp_col]) if timestamp_col and timestamp_col in row else str(idx)

            # Check if fill price equals same-bar Close
            if close_col in row and abs(row[fill_price_col] - row[close_col]) < 1e-6:
                findings.append(
                    LookaheadAuditFinding(
                        violation_type=LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION,
                        row_index=int(idx) if isinstance(idx, (int, np.integer)) else 0,
                        timestamp=ts,
                        details=f"Signal at row {idx} filled at same-bar Close ({row[close_col]}). Must execute at bar T+1 Open.",
                    )
                )

            # Check if trading before indicator warmup period
            int_idx = int(idx) if isinstance(idx, (int, np.integer)) else 0
            if int_idx < self.warmup_periods:
                findings.append(
                    LookaheadAuditFinding(
                        violation_type=LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION,
                        row_index=int_idx,
                        timestamp=ts,
                        details=f"Signal executed at row {int_idx} before indicator warmup period ({self.warmup_periods} bars).",
                    )
                )

        return findings

    @staticmethod
    def align_signal_execution(
        df: pd.DataFrame,
        signal_col: str = "signal",
        open_col: str = "open",
        executed_signal_col: str = "executed_signal",
        fill_price_col: str = "fill_price",
    ) -> pd.DataFrame:
        """
        Guarantees strict non-lookahead execution:
        Shifts signal generated at bar T to execute at bar T+1 at bar T+1's Open price.
        """
        out = df.copy()
        # Shift signal forward by 1 bar: signal[T] executes at T+1
        out[executed_signal_col] = out[signal_col].shift(1).fillna(0)

        if open_col in out.columns:
            out[fill_price_col] = out[open_col]
        return out

    def run_leak_calibration(
        self, df: pd.DataFrame, target_col: str, feature_name: str = "leaked_feature"
    ) -> pd.DataFrame:
        """Injects +1 bar forward leak for calibration benchmarking."""
        out = df.copy()
        out[feature_name] = out[target_col].shift(-1)
        return out


# Backward compatibility functions
def inject_forward_leak(df: pd.DataFrame, target_col: str, feature_name="leaked_feature"):
    out = df.copy()
    out[feature_name] = out[target_col].shift(-1)
    return out


def check_feature_timestamps(feature_timestamps: dict, decision_ts):
    return [name for name, ts in feature_timestamps.items() if ts > decision_ts]
