from dataclasses import dataclass
from typing import Dict, List

@dataclass
class ScenarioResult:
    scenario_name: str
    pnl: float

class CustomScenarioStressTester:
    def __init__(self, scenarios: Dict[str, float]):
        self.scenarios = scenarios

    def run_stress_test(self, current_value: float) -> List[ScenarioResult]:
        results = []
        for name, shock in self.scenarios.items():
            results.append(ScenarioResult(name, current_value * shock))
        return results
