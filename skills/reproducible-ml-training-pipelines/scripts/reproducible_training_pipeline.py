from dataclasses import dataclass

@dataclass
class Config:
    name: str = "reproducible-ml-training-pipelines"

class ReproducibleTrainingPipeline:
    def __init__(self, config: Config):
        self.config = config
        
    def process(self):
        return True
