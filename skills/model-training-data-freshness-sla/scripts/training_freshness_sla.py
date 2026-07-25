import dataclasses
from typing import List, Dict, Any

@dataclasses.dataclass
class TrainingFreshnessSlaConfig:
    parameter_1: float = 1.0
    parameter_2: int = 10

class TrainingFreshnessSla:
    def __init__(self, config: TrainingFreshnessSlaConfig):
        self.config = config

    def process(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"result": d.get("value", 0) * self.config.parameter_1} for d in data]
