
from dataclasses import dataclass

@dataclass
class CorporateActionEventCalendarIntegrationConfig:
    enabled: bool = True

class CorporateActionEventCalendarIntegrationEngine:
    def __init__(self, config: CorporateActionEventCalendarIntegrationConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
