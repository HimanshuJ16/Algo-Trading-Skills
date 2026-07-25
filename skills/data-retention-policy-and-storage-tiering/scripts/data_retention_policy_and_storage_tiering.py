
from dataclasses import dataclass

@dataclass
class DataRetentionPolicyAndStorageTieringConfig:
    enabled: bool = True

class DataRetentionPolicyAndStorageTieringEngine:
    def __init__(self, config: DataRetentionPolicyAndStorageTieringConfig):
        self.config = config
        
    def process(self, data: str) -> str:
        if not self.config.enabled:
            return ""
        return data.upper()
