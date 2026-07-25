from dataclasses import dataclass

@dataclass
class ModelConfig:
    name: str

class MainEngine:
    def __init__(self, config: ModelConfig):
        self.config = config
        
    def execute(self):
        return True
