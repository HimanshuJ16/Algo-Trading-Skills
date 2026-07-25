from dataclasses import dataclass

@dataclass
class Config:
    pass

class ClassImbalanceHandler:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
