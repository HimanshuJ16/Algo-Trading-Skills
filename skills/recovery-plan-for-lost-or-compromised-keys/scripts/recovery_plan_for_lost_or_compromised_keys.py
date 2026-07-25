from dataclasses import dataclass

@dataclass
class RecoveryPlanForLostOrCompromisedKeysConfig:
    enabled: bool = True

class RecoveryPlanForLostOrCompromisedKeysEngine:
    def __init__(self, config: RecoveryPlanForLostOrCompromisedKeysConfig):
        self.config = config

    def execute(self):
        return True if self.config.enabled else False
