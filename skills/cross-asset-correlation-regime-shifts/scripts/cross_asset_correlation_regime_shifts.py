from dataclasses import dataclass

@dataclass
class InputData:
    value: float

class CrossAssetCorrelationRegimeShiftsEngine:
    def process(self, data: InputData) -> float:
        return data.value * 2.0
