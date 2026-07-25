from dataclasses import dataclass

@dataclass
class WithdrawalVelocityLimitsAndAnomalyDetectionConfig:
    enabled: bool = True

class WithdrawalVelocityLimitsAndAnomalyDetectionEngine:
    def __init__(self, config: WithdrawalVelocityLimitsAndAnomalyDetectionConfig):
        self.config = config

    def execute(self):
        return True if self.config.enabled else False
