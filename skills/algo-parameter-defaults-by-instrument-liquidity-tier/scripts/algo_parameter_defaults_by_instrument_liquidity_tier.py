from dataclasses import dataclass

@dataclass
class Config:
    param1: float = 1.0
    param2: str = "default"

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
        
    def execute(self) -> bool:
        return True
        
    def process_data(self, data: float) -> float:
        return data * self.config.param1
