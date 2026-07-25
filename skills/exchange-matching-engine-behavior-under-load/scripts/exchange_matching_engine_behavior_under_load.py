from dataclasses import dataclass

@dataclass
class Config:
    param1: int = 1

class Engine:
    def __init__(self, config: Config):
        self.config = config
    def run(self):
        return True
