from dataclasses import dataclass

@dataclass
class BacktestingAltDataStrategiesWithRealisticAvailabilityLagConfig:
    data_path: str = "default.csv"

class BacktestingAltDataStrategiesWithRealisticAvailabilityLag:
    def __init__(self, config: BacktestingAltDataStrategiesWithRealisticAvailabilityLagConfig):
        self.config = config

    def process(self):
        return True
