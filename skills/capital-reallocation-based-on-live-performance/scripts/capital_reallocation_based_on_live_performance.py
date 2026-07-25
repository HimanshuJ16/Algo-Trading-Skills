from dataclasses import dataclass

@dataclass
class CapitalReallocationBasedOnLivePerformanceConfig:
    enabled: bool = True

class CapitalReallocationBasedOnLivePerformance:
    def __init__(self, config: CapitalReallocationBasedOnLivePerformanceConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
