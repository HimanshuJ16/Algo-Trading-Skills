from dataclasses import dataclass

@dataclass
class Config:
    name: str = "explainable-boosting-machines-for-regulated-signals"

class ExplainableBoostingPricer:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
