from dataclasses import dataclass

@dataclass
class Config:
    param: float = 1.0

class MainEngine:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self, value: float) -> float:
        return value * self.config.param
