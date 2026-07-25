
from dataclasses import dataclass

@dataclass
class HistoricalOrderBookReconstructionFromMessageLogsConfig:
    enabled: bool = True

class HistoricalOrderBookReconstructionFromMessageLogsEngine:
    def __init__(self, config: HistoricalOrderBookReconstructionFromMessageLogsConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
