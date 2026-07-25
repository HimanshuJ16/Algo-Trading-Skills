from dataclasses import dataclass

@dataclass
class AppDownloadAndUsageDataForConsumerCompaniesConfig:
    data_path: str = "default.csv"

class AppDownloadAndUsageDataForConsumerCompanies:
    def __init__(self, config: AppDownloadAndUsageDataForConsumerCompaniesConfig):
        self.config = config

    def process(self):
        return True
