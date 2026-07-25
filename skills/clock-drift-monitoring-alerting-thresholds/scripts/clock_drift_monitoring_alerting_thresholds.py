from dataclasses import dataclass

@dataclass
class Config:
    threshold: float = 1.0

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self, value: float) -> bool:
        return value > self.config.threshold
