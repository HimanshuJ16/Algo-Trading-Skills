from dataclasses import dataclass

@dataclass
class FactorResearchMultipleTestingCorrectionConfig:
    data_path: str = "default.csv"

class FactorResearchMultipleTestingCorrection:
    def __init__(self, config: FactorResearchMultipleTestingCorrectionConfig):
        self.config = config

    def process(self):
        return True
