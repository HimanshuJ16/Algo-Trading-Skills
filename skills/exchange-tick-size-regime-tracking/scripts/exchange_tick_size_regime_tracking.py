
from dataclasses import dataclass

@dataclass
class ExchangeTickSizeRegimeTrackingConfig:
    enabled: bool = True

class ExchangeTickSizeRegimeTrackingEngine:
    def __init__(self, config: ExchangeTickSizeRegimeTrackingConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
