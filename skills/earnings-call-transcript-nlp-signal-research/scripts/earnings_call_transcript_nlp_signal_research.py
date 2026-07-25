from dataclasses import dataclass

@dataclass
class EarningsCallTranscriptNlpSignalResearchConfig:
    data_path: str = "default.csv"

class EarningsCallTranscriptNlpSignalResearch:
    def __init__(self, config: EarningsCallTranscriptNlpSignalResearchConfig):
        self.config = config

    def process(self):
        return True
