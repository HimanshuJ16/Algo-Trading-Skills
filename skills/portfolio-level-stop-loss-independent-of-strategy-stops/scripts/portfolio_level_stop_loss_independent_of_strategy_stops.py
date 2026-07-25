from dataclasses import dataclass

@dataclass
class PortfolioLevelStopLossIndependentOfStrategyStopsConfig:
    enabled: bool = True

class PortfolioLevelStopLossIndependentOfStrategyStops:
    def __init__(self, config: PortfolioLevelStopLossIndependentOfStrategyStopsConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
