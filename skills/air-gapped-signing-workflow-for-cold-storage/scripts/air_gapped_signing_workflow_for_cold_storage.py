from dataclasses import dataclass

@dataclass
class AirGappedSigningWorkflowForColdStorageConfig:
    enabled: bool = True

class AirGappedSigningWorkflowForColdStorageEngine:
    def __init__(self, config: AirGappedSigningWorkflowForColdStorageConfig):
        self.config = config

    def execute(self):
        return True if self.config.enabled else False
