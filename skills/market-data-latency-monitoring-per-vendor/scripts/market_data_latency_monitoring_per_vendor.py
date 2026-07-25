
from dataclasses import dataclass

@dataclass
class MarketDataLatencyMonitoringPerVendorConfig:
    enabled: bool = True

class MarketDataLatencyMonitoringPerVendorEngine:
    def __init__(self, config: MarketDataLatencyMonitoringPerVendorConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
