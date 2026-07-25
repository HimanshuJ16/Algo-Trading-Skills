
from dataclasses import dataclass

@dataclass
class ReferenceDataChangeNotificationPipelineConfig:
    enabled: bool = True

class ReferenceDataChangeNotificationPipelineEngine:
    def __init__(self, config: ReferenceDataChangeNotificationPipelineConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
