from dataclasses import dataclass

@dataclass
class Config:
    name: str = "concept-drift-vs-staleness-differentiation"

class DriftVsStalenessClassifier:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
