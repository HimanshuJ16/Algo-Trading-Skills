from dataclasses import dataclass

@dataclass
class StrategyLifecycleRetirementCriteriaConfig:
    enabled: bool = True

class StrategyLifecycleRetirementCriteria:
    def __init__(self, config: StrategyLifecycleRetirementCriteriaConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
