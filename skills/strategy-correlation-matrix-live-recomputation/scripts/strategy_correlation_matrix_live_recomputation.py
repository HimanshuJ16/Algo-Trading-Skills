from dataclasses import dataclass

@dataclass
class StrategyCorrelationMatrixLiveRecomputationConfig:
    enabled: bool = True

class StrategyCorrelationMatrixLiveRecomputation:
    def __init__(self, config: StrategyCorrelationMatrixLiveRecomputationConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
