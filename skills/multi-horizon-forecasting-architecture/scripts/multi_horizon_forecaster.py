from dataclasses import dataclass

@dataclass
class Config:
    pass

class MultiHorizonForecaster:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
