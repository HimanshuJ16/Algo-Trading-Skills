from dataclasses import dataclass

@dataclass
class ResearchIdeaPipelineTrackingAndPrioritizationConfig:
    data_path: str = "default.csv"

class ResearchIdeaPipelineTrackingAndPrioritization:
    def __init__(self, config: ResearchIdeaPipelineTrackingAndPrioritizationConfig):
        self.config = config

    def process(self):
        return True
