from dataclasses import dataclass

@dataclass
class Config:
    name: str = "model-serving-infrastructure-ab-testing"

class ModelAbTester:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
