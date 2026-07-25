from dataclasses import dataclass

@dataclass
class StrategyPerformanceAttributionVsMarketBetaConfig:
    enabled: bool = True

class StrategyPerformanceAttributionVsMarketBeta:
    def __init__(self, config: StrategyPerformanceAttributionVsMarketBetaConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
