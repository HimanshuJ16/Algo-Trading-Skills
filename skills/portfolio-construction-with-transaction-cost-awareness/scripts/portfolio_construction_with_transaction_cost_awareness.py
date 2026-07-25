from dataclasses import dataclass

@dataclass
class Config:
    enabled: bool = True

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def run(self):
        return self.config.enabled
