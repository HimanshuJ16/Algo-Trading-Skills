from dataclasses import dataclass

@dataclass
class RiskParityAllocationAcrossStrategiesConfig:
    enabled: bool = True

class RiskParityAllocationAcrossStrategies:
    def __init__(self, config: RiskParityAllocationAcrossStrategiesConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
