from dataclasses import dataclass

@dataclass
class OnChainTransactionMonitoringForAnomaliesConfig:
    enabled: bool = True

class OnChainTransactionMonitoringForAnomaliesEngine:
    def __init__(self, config: OnChainTransactionMonitoringForAnomaliesConfig):
        self.config = config

    def execute(self):
        return True if self.config.enabled else False
