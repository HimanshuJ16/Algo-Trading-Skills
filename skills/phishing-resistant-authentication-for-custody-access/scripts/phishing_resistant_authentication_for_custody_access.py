from dataclasses import dataclass

@dataclass
class PhishingResistantAuthenticationForCustodyAccessConfig:
    enabled: bool = True

class PhishingResistantAuthenticationForCustodyAccessEngine:
    def __init__(self, config: PhishingResistantAuthenticationForCustodyAccessConfig):
        self.config = config

    def execute(self):
        return True if self.config.enabled else False
