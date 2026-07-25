from dataclasses import dataclass

@dataclass
class EsgDataSignalResearchAndVendorComparisonConfig:
    data_path: str = "default.csv"

class EsgDataSignalResearchAndVendorComparison:
    def __init__(self, config: EsgDataSignalResearchAndVendorComparisonConfig):
        self.config = config

    def process(self):
        return True
