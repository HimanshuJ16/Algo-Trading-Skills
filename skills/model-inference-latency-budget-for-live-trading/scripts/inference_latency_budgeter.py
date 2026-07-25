from dataclasses import dataclass

@dataclass
class Config:
    pass

class InferenceLatencyBudgeter:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
