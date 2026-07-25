from dataclasses import dataclass

@dataclass
class CentralBankCommunicationNlpAnalysisConfig:
    data_path: str = "default.csv"

class CentralBankCommunicationNlpAnalysis:
    def __init__(self, config: CentralBankCommunicationNlpAnalysisConfig):
        self.config = config

    def process(self):
        return True
