from dataclasses import dataclass

@dataclass
class Config:
    name: str = "label-noise-estimation-in-financial-targets"

class LabelNoiseEstimator:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
