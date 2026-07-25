from dataclasses import dataclass

@dataclass
class NewStrategyOnboardingChecklistConfig:
    enabled: bool = True

class NewStrategyOnboardingChecklist:
    def __init__(self, config: NewStrategyOnboardingChecklistConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
