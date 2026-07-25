from dataclasses import dataclass

@dataclass
class CrossStrategySharedInfrastructureResourceContentionConfig:
    enabled: bool = True

class CrossStrategySharedInfrastructureResourceContention:
    def __init__(self, config: CrossStrategySharedInfrastructureResourceContentionConfig):
        self.config = config
        
    def execute(self) -> bool:
        return self.config.enabled
