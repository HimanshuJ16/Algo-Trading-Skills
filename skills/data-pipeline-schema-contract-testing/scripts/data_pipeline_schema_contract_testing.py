
from dataclasses import dataclass

@dataclass
class DataPipelineSchemaContractTestingConfig:
    enabled: bool = True

class DataPipelineSchemaContractTestingEngine:
    def __init__(self, config: DataPipelineSchemaContractTestingConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
