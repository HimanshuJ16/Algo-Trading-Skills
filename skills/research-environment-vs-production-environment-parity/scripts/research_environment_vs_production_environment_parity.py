from dataclasses import dataclass

@dataclass
class ResearchEnvironmentVsProductionEnvironmentParityConfig:
    data_path: str = "default.csv"

class ResearchEnvironmentVsProductionEnvironmentParity:
    def __init__(self, config: ResearchEnvironmentVsProductionEnvironmentParityConfig):
        self.config = config

    def process(self):
        return True
