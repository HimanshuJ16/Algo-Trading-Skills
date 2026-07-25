from dataclasses import dataclass

@dataclass
class Config:
    enabled: bool = True

class Engine:
    def __init__(self, config: Config):
        self.config = config
        
    def execute(self):
        return True
