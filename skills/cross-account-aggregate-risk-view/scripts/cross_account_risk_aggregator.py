from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class CrossAccountRiskAggregatorConfig:
    enabled: bool = True
    parameters: Dict[str, float] = field(default_factory=dict)

class CrossAccountRiskAggregator:
    def __init__(self, config: CrossAccountRiskAggregatorConfig):
        self.config = config

    def execute(self, data: Dict) -> bool:
        if not self.config.enabled:
            return False
        # Mock logic
        return True
