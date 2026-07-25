from dataclasses import dataclass

@dataclass
class StrategyCapacityEstimationBeforeScalingCapitalConfig:
    enabled: bool = True

class StrategyCapacityEstimationBeforeScalingCapital:
    def __init__(self, config: StrategyCapacityEstimationBeforeScalingCapitalConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
