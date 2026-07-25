from dataclasses import dataclass

@dataclass
class InputData:
    value: float

class FuturesExpiryWeekLiquidityAndVolatilityHandlingEngine:
    def process(self, data: InputData) -> float:
        return data.value * 2.0
