from dataclasses import dataclass

@dataclass
class InputData:
    value: float

class ExchangeForPhysicalEfpTransactionsEngine:
    def process(self, data: InputData) -> float:
        return data.value * 2.0
