from dataclasses import dataclass

@dataclass
class AlternativeDataVendorDueDiligenceChecklistConfig:
    data_path: str = "default.csv"

class AlternativeDataVendorDueDiligenceChecklist:
    def __init__(self, config: AlternativeDataVendorDueDiligenceChecklistConfig):
        self.config = config

    def process(self):
        return True
