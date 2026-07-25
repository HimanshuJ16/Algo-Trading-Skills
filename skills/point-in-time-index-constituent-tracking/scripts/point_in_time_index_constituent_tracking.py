
from dataclasses import dataclass

@dataclass
class PointInTimeIndexConstituentTrackingConfig:
    enabled: bool = True

class PointInTimeIndexConstituentTrackingEngine:
    def __init__(self, config: PointInTimeIndexConstituentTrackingConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
