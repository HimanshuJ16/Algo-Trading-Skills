import pandas as pd
from dataclasses import dataclass
from typing import List

@dataclass
class SignalResult:
    timestamp: str
    signal_value: float
    asset_id: str

class SocialMediaSentimentSignalWithBotFilteringEngine:
    def __init__(self, config: dict = None):
        self.config = config or {}

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        if raw_data.empty:
            return []
        results = []
        for i, row in raw_data.iterrows():
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=float(row.get("raw_val", 0) * 1.5),
                asset_id=row.get("asset", "unknown")
            ))
        return results
