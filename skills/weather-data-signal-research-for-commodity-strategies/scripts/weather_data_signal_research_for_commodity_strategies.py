from dataclasses import dataclass

@dataclass
class WeatherDataSignalResearchForCommodityStrategiesConfig:
    data_path: str = "default.csv"

class WeatherDataSignalResearchForCommodityStrategies:
    def __init__(self, config: WeatherDataSignalResearchForCommodityStrategiesConfig):
        self.config = config

    def process(self):
        return True
