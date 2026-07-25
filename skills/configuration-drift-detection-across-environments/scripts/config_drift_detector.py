from dataclasses import dataclass

@dataclass
class Config:
    name: str

class ConfigDriftDetector:
    def __init__(self, config: Config = None):
        self.config = config or Config("default")
        
    def process(self) -> bool:
        return True
